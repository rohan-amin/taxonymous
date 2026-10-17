"""Verification gate. A redacted file is released only if every check passes.

Checks, all fail-closed:
  structure  no metadata, XMP, annotations, form fields, attachments, JavaScript, links
  text       the PDF text layer and the .txt contain no known value (anything redacted
             from this document, any profile value in any format) and no identifier-shaped
             pattern; placeholders are ignored
  ocr        the same, on Tesseract OCR of every rendered page, so text that was only
             covered (not removed) or that lives in image pixels is still caught

Issues never contain real values: only the kind of value, the check, and the page.
"""

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from .detect import ProfileMatcher, digits, pattern_hits
from .pagetext import PageText

PLACEHOLDER_RE = re.compile(r"<[A-Z]+_[A-Z0-9]+>")
MASK_RE = re.compile(r"[*Xx•#]{2,}")
# characters Tesseract commonly reads in place of each digit
CONFUSABLE = {"0": "0Oo", "1": "1Il|i", "2": "2Z", "5": "5S", "6": "6b", "8": "8B", "9": "9g"}
OCR_DPI = 200


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


def _squash(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def _digit_regex(ds: str, fuzzy: bool) -> re.Pattern:
    parts = [f"[{re.escape(CONFUSABLE.get(d, d))}]" if fuzzy else d for d in ds]
    return re.compile(r"(?<![0-9])" + r"[\s.\-()/,]{0,2}".join(parts) + r"(?![0-9])")


class Scanner:
    """Finds known values and identifier patterns in a block of text."""

    def __init__(self, matcher: ProfileMatcher, known: list[Known]):
        self.matcher = matcher
        self.numbers: list[tuple[str, re.Pattern, re.Pattern]] = []
        self.words: list[tuple[str, str]] = []
        for k in known:
            if MASK_RE.search(k.value):
                continue  # masked forms are caught by the pattern check
            ds = digits(k.value)
            letters = re.sub(r"[^A-Za-z]", "", k.value)
            if len(ds) >= 7 and len(ds) >= len(letters):
                self.numbers.append((k.kind, _digit_regex(ds, False), _digit_regex(ds, True)))
            elif len(_squash(k.value).strip()) >= 4:
                self.words.append((k.kind, _squash(k.value)))

    def scan(self, text: str, fuzzy: bool = False) -> list[str]:
        """Kinds of everything that should not be in this text."""
        text = PLACEHOLDER_RE.sub(" ", text)
        found = [kind for m, kind in pattern_hits(text)]
        found += [f.kind for f in self.matcher.find(PageText(0, text))]
        squashed = _squash(text)
        found += [kind for kind, value in self.words if value in squashed]
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


def _ocr_page(page_png: bytes) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "page.png"
        img.write_bytes(page_png)
        proc = subprocess.run(["tesseract", str(img), "stdout"], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:200]}")
        return proc.stdout


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
            report.issues += [Issue("txt", k) for k in scanner.scan(Path(txt).read_text())]
        if ocr:
            if shutil.which("tesseract") is None:
                raise RuntimeError("tesseract is not installed; cannot OCR-verify (brew install tesseract)")
            images = [p.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY).tobytes("png") for p in doc]
            with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
                texts = list(pool.map(_ocr_page, images))
            for n, text in enumerate(texts, start=1):
                report.issues += [Issue("ocr", k, n) for k in scanner.scan(text, fuzzy=True)]
    except Exception as e:  # anything unexpected is a failure, never a pass
        report.issues.append(Issue("error", type(e).__name__, detail=str(e)[:300]))
    report.passed = not report.issues
    return report
