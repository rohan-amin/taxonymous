"""Verification gate. A redacted file is released only if every check passes.

Checks, all fail-closed:
  structure  no metadata, XMP, annotations, form fields, attachments, JavaScript, links
  text       the PDF text layer and the .txt contain no known value (anything redacted
             from this document, any profile value in any format) and no identifier-shaped
             pattern; placeholders are ignored
  ocr        the same, on Tesseract OCR of every rendered page, so text that was only
             covered (not removed) or that lives in image pixels is still caught; matching
             tolerates the digits and letters OCR confuses. Sparse pages (forms, receipts,
             anything with an image) get both OCR passes; dense text pages (statements,
             letters) get the layout pass, which is what reads dense text best

Issues never contain real values: only the kind of value, the check, and the page.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from . import ocr as ocr_mod
from .detect import ProfileMatcher, digits, pattern_hits
from .pagetext import PageText

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

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Known:
    kind: str
    value: str


def _squash(s: str, fuzzy: bool = False) -> str:
    s = s.lower()
    if fuzzy:
        s = s.translate(LETTER_FOLD).replace("rn", "m")
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def _digit_regex(ds: str, fuzzy: bool) -> re.Pattern:
    parts = [f"[{re.escape(CONFUSABLE.get(d, d))}]" if fuzzy else d for d in ds]
    return re.compile(r"(?<![0-9])" + r"[\s.\-()/,]{0,2}".join(parts) + r"(?![0-9])")


class Scanner:
    """Finds known values and identifier patterns in a block of text."""

    def __init__(self, matcher: ProfileMatcher, known: list[Known]):
        self.matcher = matcher
        self.numbers: list[tuple[str, re.Pattern, re.Pattern]] = []
        self.words: list[tuple[str, str, str]] = []
        for k in known:
            if MASK_RE.search(k.value):
                continue  # masked forms are caught by the pattern check
            ds = digits(k.value)
            letters = re.sub(r"[^A-Za-z]", "", k.value)
            if len(ds) >= 7 and len(ds) >= len(letters):
                self.numbers.append((k.kind, _digit_regex(ds, False), _digit_regex(ds, True)))
            elif len(_squash(k.value).strip()) >= 4:
                self.words.append((k.kind, _squash(k.value), _squash(k.value, True)))

    def scan(self, text: str, fuzzy: bool = False) -> list[str]:
        """Kinds of everything that should not be in this text."""
        text = PLACEHOLDER_RE.sub(" ", text)
        found = [kind for m, kind in pattern_hits(text)]
        found += [f.kind for f in self.matcher.find(PageText(0, text))]
        squashed = _squash(text, fuzzy)
        found += [kind for kind, exact, loose in self.words if (loose if fuzzy else exact) in squashed]
        found += [kind for kind, exact, loose in self.numbers if (loose if fuzzy else exact).search(text)]
        return found


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
    for page in doc:
        if page.first_annot is not None:
            issues.append(Issue("structure", "annotations", page.number + 1))
        if page.first_widget is not None:
            issues.append(Issue("structure", "form-fields", page.number + 1))
        if page.get_links():
            issues.append(Issue("structure", "links", page.number + 1))
    return issues


def _passes(page: pymupdf.Page) -> tuple[str, ...]:
    if not page.get_images() and len(page.get_text()) > DENSE_CHARS:
        return ocr_mod.PASSES[:1]
    return ocr_mod.PASSES


def verify(pdf: Path, matcher: ProfileMatcher, known: list[Known], txt: Path | None = None,
           ocr: bool = True) -> Report:
    report = Report(pdf=str(pdf), ocr=ocr)
    scanner = Scanner(matcher, known)
    try:
        doc = pymupdf.open(pdf)
        report.pages = doc.page_count
        report.issues += _structure(doc)
        for page in doc:
            report.issues += [Issue("text", k, page.number + 1) for k in scanner.scan(page.get_text())]
        if txt is not None:
            # text from scanned pages is OCR output, so it gets OCR tolerance too
            report.issues += [Issue("txt", k) for k in scanner.scan(Path(txt).read_text(), fuzzy=True)]
        if ocr:
            ocr_mod.require()
            passes = [_passes(p) for p in doc]
            jobs = [(p.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY).tobytes("png"), psm)
                    for p, psms in zip(doc, passes, strict=True) for psm in psms]
            texts = iter(ocr_mod.run_all(jobs))
            for n, psms in enumerate(passes, 1):
                found = set()
                for _ in psms:
                    found.update(scanner.scan(next(texts), fuzzy=True))
                report.issues += [Issue("ocr", k, n) for k in sorted(found)]
    except Exception as e:  # anything unexpected is a failure, never a pass
        report.issues.append(Issue("error", type(e).__name__, detail=str(e)[:300]))
    report.passed = not report.issues
    return report
