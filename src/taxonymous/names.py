"""Names in running text that the profile doesn't know: a trustee, a sibling, a CPA.

A local spaCy transformer model (no network) tags people and organizations in
prose. The smaller statistical models miss uncommon names ("Oyelaran" read as a
place), and a name that is missed goes out. It only reads prose: sentences,
greetings and sign-offs. Forms and tables are left to the label detector,
because there the model tags captions, security names and street names as people.
"""

import ctypes
import re
import sys
import warnings
from functools import cache

from .pagetext import Line, PageText

MODEL = "en_core_web_trf"
PROSE_WORDS = 5  # a line needs at least this many words to be read as a sentence
PROSE_LOWER = 0.4  # ...and this share of them lowercase (titles and table rows are mostly capitals)
GREETING_RE = re.compile(r"^\s*(?:dear|hi|hello|attn|attention|cc|re)\b", re.IGNORECASE)
CLOSING_RE = re.compile(r"^\s*(?:sincerely|regards|best regards|kind regards|warm regards|respectfully|"
                        r"thank you|thanks|yours truly|very truly yours|cordially)\b,?", re.IGNORECASE)
SIGNATURE_LINES = 3  # lines after a closing that may hold the signer's name and title
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@cache
def _nlp():
    import spacy
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)  # torch deprecation noise from the model's code
        nlp = spacy.load(MODEL, exclude=["tagger", "parser", "attribute_ruler", "lemmatizer"])
    _release_memory()
    return nlp


def _release_memory() -> None:
    """Loading the model goes through about 2 GB of scratch memory that the allocator
    keeps; hand it back to the OS (2.7 GB -> 0.9 GB resident on macOS)."""
    try:
        if sys.platform == "darwin":
            ctypes.CDLL("libc.dylib").malloc_zone_pressure_relief(None, 0)
        elif sys.platform.startswith("linux"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass  # only an optimization


def _is_prose(text: str) -> bool:
    words = WORD_RE.findall(text)
    return len(words) >= PROSE_WORDS and sum(w[0].islower() for w in words) >= PROSE_LOWER * len(words)


def prose_blocks(pt: PageText) -> list[tuple[int, int]]:
    """Character ranges of runs of consecutive prose lines (a wrapped sentence stays whole)."""
    blocks: list[list[Line]] = []
    signature = 0
    prev: Line | None = None
    for line in pt.lines:
        text = pt.text[line.start:line.end]
        closing = bool(CLOSING_RE.match(text))
        take = _is_prose(text) or bool(GREETING_RE.match(text)) or closing or signature > 0
        signature = SIGNATURE_LINES if closing else max(0, signature - 1)
        if not take:
            prev = None
            continue
        if prev is not None and blocks and blocks[-1][-1] is prev:
            blocks[-1].append(line)
        else:
            blocks.append([line])
        prev = line
    return [(b[0].start, b[-1].end) for b in blocks]


def entities(pt: PageText) -> list[tuple[int, int, str]]:
    """(start, end, PERSON or ORG) for the people and organizations in the page's prose."""
    blocks = prose_blocks(pt)
    if not blocks:
        return []
    texts = [pt.text[s:e].replace("\n", " ") for s, e in blocks]  # same length, offsets line up
    out = []
    for (start, _), doc in zip(blocks, _nlp().pipe(texts), strict=True):
        for ent in doc.ents:
            if ent.label_ in ("PERSON", "ORG"):
                end = ent.end_char - (2 if re.search(r"['’]s$", ent.text) else 0)  # "Varga's" -> "Varga"
                out.append((start + ent.start_char, start + end, ent.label_))
    return out


@cache
def is_person(text: str) -> bool:
    """Whether the model reads this string, on its own, as one person's name."""
    ents = _nlp()(text).ents
    return len(ents) == 1 and ents[0].label_ == "PERSON" and len(ents[0].text) >= 0.8 * len(text.strip())
