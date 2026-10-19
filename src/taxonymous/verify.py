"""Verification gate. A redacted file is released only if every check passes.

Checks, all fail-closed:
  structure  no metadata, XMP, annotations, form fields, attachments, JavaScript, links;
             document and page dictionaries hold only allow-listed keys (no outline,
             page labels, layers, structure tree...); nothing drawn outside the page
  raw        no known value in any object or stream of the file, as text or UTF-16,
             wherever it is (catches hiding places the other checks don't render)
  text       the PDF text layer and the .txt contain no known value (anything redacted
             from this document, any profile value in any format) and no identifier-shaped
             pattern; placeholders are ignored
  ocr        the same, on Tesseract OCR of every rendered page, so text that was only
             covered (not removed) or that lives in image pixels is still caught; matching
             tolerates the digits and letters OCR confuses. Sparse pages (forms, receipts,
             anything with an image) get both OCR passes; dense text pages (statements,
             letters) get the layout pass, which is what reads dense text best

Values the reviewer chose to keep in the clear everywhere in the document
(`allowed`) are not leaks. A single box they left in the clear (`shown`) is not
checked at that spot only: it is blanked in a scratch copy before the text and
OCR checks, and the .txt may hold its value once on that page. The same value
anywhere else is still a leak.

Issues never contain real values: only the kind of value, the check, and the page.
"""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import ocr as ocr_mod
from .detect import (
    NOT_AMOUNT,
    NUMBER_SEP,
    PROSE_KINDS,
    ProfileMatcher,
    digits,
    identifies,
    in_prose,
    incidental,
    is_number_value,
    pattern_hits,
)
from .pagetext import PageText
from .progress import NONE, Progress
from .sanitize import CATALOG_KEYS, PAGE_KEYS, outside

PLACEHOLDER_RE = re.compile(r"<[A-Z]+_[A-Z0-9]+>")
MASK_RE = re.compile(r"[*Xx•#]{2,}")
# characters Tesseract commonly reads in place of each digit
CONFUSABLE = {"0": "0Oo", "1": "1Il|i", "2": "2Z", "5": "5S", "6": "6b", "8": "8B", "9": "9g"}
# letters OCR swaps for each other in words ("Iyer" read as "lyer")
LETTER_FOLD = str.maketrans({"l": "i", "1": "i", "|": "i", "!": "i", "0": "o", "5": "s", "8": "b"})
OCR_DPI = 200
DENSE_CHARS = 1500  # a page with more text than this and no images is read with one OCR pass


@dataclass
class Issue:
    check: str  # structure | text | txt | ocr | error
    kind: str  # what leaked: SSN, NAME, metadata, ...
    page: int | None = None
    detail: str = ""


@dataclass
class Report:
    pdf: str
    passed: bool = False
    issues: list[Issue] = field(default_factory=list)
    pages: int = 0
    ocr: bool = True
    notes: list[str] = field(default_factory=list)  # what was done to make it pass, for the reviewer

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Known:
    kind: str
    value: str


@dataclass
class Shown:
    """A box the reviewer left in the clear: where, and the values it may hold."""
    page: int  # 0-based
    rect: tuple[float, float, float, float]
    values: list[str]


def _squash(s: str, fuzzy: bool = False) -> str:
    s = s.lower()
    if fuzzy:
        s = s.translate(LETTER_FOLD).replace("rn", "m")
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def _digit_regex(ds: str, fuzzy: bool) -> re.Pattern:
    parts = [f"[{re.escape(CONFUSABLE.get(d, d))}]" if fuzzy else d for d in ds]
    return re.compile(r"(?<![0-9])" + NOT_AMOUNT + NUMBER_SEP.join(parts) + r"(?![0-9])")


class Scanner:
    """Finds known values and identifier patterns in a block of text."""

    def __init__(self, matcher: ProfileMatcher, known: list[Known], allowed: Iterable[str] = ()):
        self.matcher = matcher
        self.allowed = {_squash(a) for a in allowed}
        self.numbers: list[tuple[str, re.Pattern, re.Pattern]] = []
        self.words: list[tuple[str, str, str, re.Pattern | None]] = []
        for k in known:
            if MASK_RE.search(k.value) or _squash(k.value) in self.allowed:
                continue  # masked forms are caught by the pattern check
            if not identifies(k.kind, k.value):
                continue  # a detector once took a caption for a name, a date for an ID...
            ds = digits(k.value)
            if is_number_value(k.value):
                self.numbers.append((k.kind, _digit_regex(ds, False), _digit_regex(ds, True)))
            elif len(_squash(k.value).strip()) >= 4:
                self.words.append((k.kind, _squash(k.value), _squash(k.value, True), _prose_regex(k)))

    def known(self, text: str) -> list[str]:
        """Kinds of known values (not generic patterns) in raw file text: exact matches only."""
        text = PLACEHOLDER_RE.sub(" ", text)
        found = [f.kind for f in self.matcher.find(PageText(0, text)) if _squash(f.text) not in self.allowed]
        squashed = _squash(text)
        found += [kind for kind, exact, _, rx in self.words if _word_in(exact, squashed, rx, text)]
        found += [kind for kind, exact, _ in self.numbers if _number_in(kind, exact, text)]
        return found

    def scan(self, text: str, fuzzy: bool = False) -> list[str]:
        """Kinds of everything that should not be in this text."""
        text = PLACEHOLDER_RE.sub(" ", text)
        found = [kind for m, kind in pattern_hits(text) if _squash(m.group()) not in self.allowed]
        found += [f.kind for f in self.matcher.find(PageText(0, text)) if _squash(f.text) not in self.allowed]
        squashed = _squash(text, fuzzy)
        found += [kind for kind, exact, loose, rx in self.words
                  if _word_in(loose if fuzzy else exact, squashed, rx, text)]
        found += [kind for kind, exact, loose in self.numbers
                  if _number_in(kind, loose if fuzzy else exact, text)]
        return found


def _prose_regex(k: Known) -> re.Pattern | None:
    """For a name: finds it in the text as printed, to tell a name from a word in a sentence."""
    if k.kind not in PROSE_KINDS:
        return None
    words = re.findall(r"[A-Za-z0-9]+", k.value)
    return re.compile(r"(?<![A-Za-z0-9])" + r"[^A-Za-z0-9]+".join(map(re.escape, words)) + r"(?![A-Za-z0-9])",
                      re.I)


def _word_in(value: str, squashed: str, rx: re.Pattern | None, text: str) -> bool:
    """A known value in the text. A name only counts where it isn't a word in a sentence
    ("filing" in "for filing your return" isn't the name "Filing"); one found only loosely
    (an OCR misread) counts."""
    if value not in squashed:
        return False
    if rx is None:
        return True
    spots = list(rx.finditer(text))
    return not spots or any(not in_prose(text, m.start(), m.end()) for m in spots)


def _number_in(kind: str, rx: re.Pattern, text: str) -> bool:
    """A copy of a known number, other than as a form's OMB number or a date."""
    return any(not incidental(kind, text, m) for m in rx.finditer(text))


def _keys(doc: pymupdf.Document, xref: int) -> list[str]:
    """Keys that are set: a null value is the same as no key."""
    return [k for k in doc.xref_get_keys(xref) if doc.xref_get_key(xref, k)[0] != "null"]


def _structure(doc: pymupdf.Document) -> list[Issue]:
    issues = []
    if any(v for k, v in doc.metadata.items() if k not in ("format", "encryption")):
        issues.append(Issue("structure", "metadata"))
    if doc.get_xml_metadata().strip():
        issues.append(Issue("structure", "xmp-metadata"))
    if doc.embfile_count():
        issues.append(Issue("structure", "attachments"))
    if doc.is_encrypted or doc.needs_pass:
        issues.append(Issue("structure", "encrypted"))
    catalog = doc.xref_object(doc.pdf_catalog())
    if "/JavaScript" in catalog or "/OpenAction" in catalog or "/AA" in catalog:
        issues.append(Issue("structure", "javascript-or-actions"))
    if "/AcroForm" in catalog:
        issues.append(Issue("structure", "form"))
    for key in _keys(doc, doc.pdf_catalog()):
        if key not in CATALOG_KEYS:
            issues.append(Issue("structure", f"document-{key}"))
    for page in doc:
        for key in _keys(doc, page.xref):
            if key not in PAGE_KEYS and not (key == "Annots" and page.first_annot is None):
                issues.append(Issue("structure", f"page-{key}", page.number + 1))
        if outside(page):
            issues.append(Issue("structure", "outside-the-page", page.number + 1))
        if page.first_annot is not None:
            issues.append(Issue("structure", "annotations", page.number + 1))
        if page.first_widget is not None:
            issues.append(Issue("structure", "form-fields", page.number + 1))
        if page.get_links():
            issues.append(Issue("structure", "links", page.number + 1))
    return issues


HEX_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>")
BINARY_SUBTYPES = {"/Image", "/Type1C", "/CIDFontType0C", "/OpenType"}


def _decode(raw: bytes) -> str:
    text = raw.decode("latin-1")
    for m in HEX_RE.finditer(raw):  # hex strings: <FEFF...> is UTF-16
        h = re.sub(rb"\s", b"", m.group(1))
        b = bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
        text += " " + (b[2:].decode("utf-16-be", "replace") if b[:2] == b"\xfe\xff" else b.decode("latin-1"))
    if b"\xfe\xff" in raw:  # literal UTF-16 strings
        for chunk in raw.split(b"\xfe\xff")[1:]:
            text += " " + chunk[: len(chunk) // 2 * 2].decode("utf-16-be", "replace")
    return text


def _raw(doc: pymupdf.Document, scanner: Scanner) -> list[Issue]:
    """Every object and every non-binary stream, read as text."""
    found = set()
    for xref in range(1, doc.xref_length()):
        try:
            parts = [doc.xref_object(xref, compressed=False).encode("latin-1", "replace")]
            if doc.xref_is_stream(xref):
                keys = doc.xref_get_keys(xref)
                binary = doc.xref_get_key(xref, "Subtype")[1] in BINARY_SUBTYPES or "Length1" in keys
                if not binary:
                    parts.append(doc.xref_stream(xref) or b"")
        except Exception:  # a free or broken object: nothing to read
            continue
        for part in parts:
            found.update(scanner.known(_decode(part)))
    return [Issue("raw", k) for k in sorted(found)]


def _without_shown(doc: pymupdf.Document, shown: list[Shown]) -> pymupdf.Document:
    """A scratch copy with the shown boxes blanked (text removed, pixels whited out)."""
    copy = pymupdf.open("pdf", doc.tobytes())
    for n in {s.page for s in shown}:
        if 0 <= n < copy.page_count:
            page = copy[n]
            for s in shown:
                if s.page == n:
                    page.add_redact_annot(pymupdf.Rect(s.rect), fill=(1, 1, 1))
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return copy


PAGE_HEADER_RE = re.compile(r"^=== Page (\d+) ===$", re.M)


def _txt_without_shown(text: str, shown: list[Shown]) -> str:
    """The .txt with one copy of each shown box's value taken out of its page. A value
    that isn't there as expected stays in, and is reported like any other."""
    parts = PAGE_HEADER_RE.split(text)  # [preamble, "1", page 1, "2", page 2, ...]
    for s in shown:
        i = 2 * s.page + 2
        if i >= len(parts):
            continue
        for v in sorted(s.values, key=len, reverse=True):
            words = v.split()
            if not words:
                continue
            m = re.search(r"\s+".join(map(re.escape, words)), parts[i], re.I)
            if m:
                parts[i] = parts[i][:m.start()] + " " + parts[i][m.end():]
                break
    out = parts[0]
    for n, body in zip(parts[1::2], parts[2::2], strict=True):
        out += f"=== Page {n} ===" + body
    return out


def _passes(page: pymupdf.Page) -> tuple[str, ...]:
    if not page.get_images() and len(page.get_text()) > DENSE_CHARS:
        return ocr_mod.PASSES[:1]
    return ocr_mod.PASSES


def verify(pdf: Path, matcher: ProfileMatcher, known: list[Known], txt: Path | None = None,
           ocr: bool = True, allowed: Iterable[str] = (), shown: list[Shown] = (),
           progress: Progress = NONE) -> Report:
    report = Report(pdf=str(pdf), ocr=ocr)
    allowed = list(allowed)
    scanner = Scanner(matcher, known, allowed)
    try:
        doc = pymupdf.open(pdf)
        report.pages = doc.page_count
        report.issues += _structure(doc)
        # a value left readable in one spot is in the file's raw text; the text and OCR
        # checks below hold it to that spot
        excused = [v for s in shown for v in s.values]
        report.issues += _raw(doc, Scanner(matcher, known, [*allowed, *excused]))
        if shown:
            doc = _without_shown(doc, list(shown))
        progress.start("checking", doc.page_count)
        for page in doc:
            report.issues += [Issue("text", k, page.number + 1) for k in scanner.scan(page.get_text())]
            if not ocr:
                progress.step()
        if txt is not None:
            # text from scanned pages is OCR output, so it gets OCR tolerance too
            text = _txt_without_shown(Path(txt).read_text(), list(shown))
            report.issues += [Issue("txt", k) for k in scanner.scan(text, fuzzy=True)]
        if ocr:
            ocr_mod.require()
            passes = [_passes(p) for p in doc]
            jobs = [(p.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY).tobytes("png"), psm)
                    for p, psms in zip(doc, passes, strict=True) for psm in psms]
            texts = iter(ocr_mod.run_all(jobs, done=progress.per_page([len(p) for p in passes])))
            for n, psms in enumerate(passes, 1):
                found = set()
                for _ in psms:
                    found.update(scanner.scan(next(texts), fuzzy=True))
                report.issues += [Issue("ocr", k, n) for k in sorted(found)]
    except Exception as e:  # anything unexpected is a failure, never a pass
        report.issues.append(Issue("error", type(e).__name__, detail=str(e)[:300]))
    report.passed = not report.issues
    return report
