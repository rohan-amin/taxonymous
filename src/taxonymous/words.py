"""Where each word is on a document's original pages, for clicking a value to add it.

A page with a text layer gives its words directly; a scan, a photo, and the pictures on a
text page are read by OCR (Vision when it is on, else Tesseract), once, when the reviewer
first asks for that page. A PDF's text layer is read for all its pages at once: cleaning
the file up first (as redaction does, so hidden text is never offered) is most of the
cost. Positions are page points on the page as shown (rotation applied), the same space
as the page images and the redaction boxes. Words are kept in memory only, for the last
few documents, and never written anywhere.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import ocr
from .redact import IMAGE_SUFFIXES, _image_rects, _render, _visible_text, load_image
from .sanitize import sanitize

KEEP = 6  # documents whose words are kept in memory


@dataclass(frozen=True)
class PageWord:
    text: str
    rect: tuple[float, float, float, float]  # page points, as shown
    line: int  # words with the same number are one line, in reading order

    def json(self) -> dict:
        return {"text": self.text, "rect": [round(v, 2) for v in self.rect], "line": self.line}


@dataclass
class _Doc:
    """One version of one file: the words of the pages read so far."""
    lock: threading.Lock = field(default_factory=threading.Lock)  # one reader at a time
    pages: int = -1  # -1 until the file is opened
    words: dict[int, list[PageWord]] = field(default_factory=dict)
    pictures: dict[int, list[pymupdf.Rect]] = field(default_factory=dict)  # a text page's, OCR'd when asked
    scans: set[int] = field(default_factory=set)  # pages with no text to show, OCR'd when asked


_docs: OrderedDict[tuple, _Doc] = OrderedDict()
_lock = threading.Lock()


def page_words(src: Path, n: int, size: tuple[float, float] | None = None) -> list[PageWord]:
    """The words on page `n` (0-based) of `src`. `size`: a photo's page size in points
    (it has none of its own; the review screen shows it at that size)."""
    key = (str(src), src.stat().st_mtime_ns, size)
    with _lock:
        doc = _docs.setdefault(key, _Doc())
        _docs.move_to_end(key)
        while len(_docs) > KEEP:
            _docs.popitem(last=False)
    with doc.lock:
        if doc.pages < 0:
            _open(src, doc, size)
        if not 0 <= n < doc.pages:
            raise IndexError("no such page")
        if n not in doc.words:
            doc.words[n] = _read_page(src, doc, n)
        return doc.words[n]


def _open(src: Path, doc: _Doc, size: tuple[float, float] | None) -> None:
    if src.suffix.lower() in IMAGE_SUFFIXES:
        img = load_image(src)
        width = size[0] if size else img.width * 72 / 200
        doc.words[0] = _ocr_words(img, width / img.width, line_base=0)
        doc.pages = 1
        return
    pdf = pymupdf.open(src)
    if pdf.needs_pass:
        raise ValueError("password-protected")
    pdf.bake(annots=True, widgets=True)  # form fields' values are page text, as redaction sees them
    sanitize(pdf)  # hidden and off-page text is gone: only words you can see are offered
    for page in pdf:
        if not _visible_text(page):
            doc.scans.add(page.number)
            continue
        words = _text_words(page)
        pictures = _image_rects(page)
        if pictures and page.rotation == 0:
            doc.pictures[page.number] = pictures
            doc.words[-1 - page.number] = words  # finished when its pictures are read
        else:
            doc.words[page.number] = words
    doc.pages = pdf.page_count


def _read_page(src: Path, doc: _Doc, n: int) -> list[PageWord]:
    """OCR for a scanned page, or for the pictures on a text page (rendering the original
    needs no clean-up: hidden text doesn't show)."""
    page = pymupdf.open(src)[n]
    if n in doc.scans:
        p = _render(page)
        return _ocr_words(p.img, p.scale, line_base=0)
    words = doc.words[-1 - n]
    p = _render(page, only=doc.pictures[n])
    seen = [pymupdf.Rect(w.rect) for w in words]
    extra = _ocr_words(p.img, p.scale, line_base=max((w.line for w in words), default=0) + 1)
    del doc.words[-1 - n]
    return words + [w for w in extra if not any(pymupdf.Rect(w.rect).intersects(r) for r in seen)]


def _text_words(page: pymupdf.Page) -> list[PageWord]:
    turn = page.rotation_matrix
    out = []
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words", sort=True):
        r = pymupdf.Rect(x0, y0, x1, y1) * turn
        out.append(PageWord(text, (r.x0, r.y0, r.x1, r.y1), block * 1000 + line))
    return out


def _ocr_words(img, scale: float, line_base: int) -> list[PageWord]:
    engine = ocr.VISION if ocr.vision_on() else "3"
    if engine != ocr.VISION:
        ocr.require()
    found = ocr.parse_tsv(ocr.read(ocr.picture(img), engine, tsv=True))
    found = ocr.tighten(found, img, every=engine == ocr.VISION)
    lines: dict[tuple, int] = {}
    out = []
    for w in found:
        line = lines.setdefault(w.line, line_base + len(lines))
        x0, y0, x1, y1 = (v * scale for v in w.rect)
        out.append(PageWord(w.text, (x0, y0, x1, y1), line))
    return out
