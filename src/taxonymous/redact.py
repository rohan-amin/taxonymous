"""Redact a PDF, scan or photo.

Text pages (a text layer):
1. Flatten form fields and annotations so their values become page text
   (and get scanned like everything else).
2. Detect identifiers on every page, then look for everything found anywhere
   in the document on every other page too.
3. Apply true redactions: the covered text, image pixels and drawings are
   removed from the file, and a placeholder like <SSN_A> is written in.
   Images on the page (an embedded scan, a signature) are OCR'd and the
   identifiers in them are removed the same way.

Image pages (a scan, a scan with only an invisible OCR layer, or a photo):
   The page is rendered, straightened and OCR'd, the identifiers are burned
   into the pixels, and the page is replaced by that one image, so nothing
   that was hidden under or behind it survives. Pages are read a few at a
   time and their images let go, so a long scan never sits in memory whole.

Then for every file: scrub metadata, XMP, attachments, JavaScript, links,
hidden text, and write the placeholder text.
"""

import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from . import ocr
from .detect import Finding, ProfileMatcher, detect, merge, relink, repeat_values, repeated
from .pagetext import PageText, extract
from .vault import Vault

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp"}
SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
PHOTO_TEXT_HEIGHT = 6.5  # points: OCR word boxes in a photo are scaled so text is about this tall
JPEG_QUALITY = 80
OCR_BATCH = 8  # scanned pages rendered and read at a time


@dataclass
class Result:
    source: Path
    pdf: Path
    text: Path
    findings: list[Finding] = field(default_factory=list)
    placeholders: dict[str, str] = field(default_factory=dict)  # placeholder -> kind
    image_pages: list[int] = field(default_factory=list)  # 1-based pages rebuilt from OCR

    def counts(self) -> Counter:
        return Counter(f.kind for f in self.findings)


@dataclass
class _Ocr:
    """One page read by OCR: the image (dropped once read, for a PDF page), points per
    pixel, and a PageText per read."""
    number: int
    img: Image.Image | None
    scale: float
    dpi: float = 0
    reads: list[tuple[PageText, list[ocr.Word]]] = field(default_factory=list)
    found: list[tuple[PageText, Finding]] = field(default_factory=list)


def output_stem(src: Path) -> str:
    """receipt.pdf -> receipt; receipt.jpg -> receipt.jpg (so the two never collide)."""
    return src.stem if src.suffix.lower() == ".pdf" else src.name


def load_image(path: Path) -> Image.Image:
    if path.suffix.lower() in (".heic", ".heif"):
        from pillow_heif import register_heif_opener
        register_heif_opener()
    img = Image.open(path)
    return ImageOps.exif_transpose(img).convert("RGB")  # applies the camera's rotation, drops EXIF


def _fontsize(rect: pymupdf.Rect, text: str) -> float:
    by_width = rect.width / pymupdf.get_text_length(text, fontname="helv", fontsize=1)
    return max(2.0, min(rect.height * 0.9, by_width, 10))


def _label(page: pymupdf.Page, rect: pymupdf.Rect, ph: str) -> None:
    # insert_text never drops text that doesn't fit, so every redaction is visible in the text layer
    fs = _fontsize(rect, ph)
    page.insert_text((rect.x0 + 0.5, rect.y1 - (rect.height - fs) / 2 - 0.15 * fs), ph,
                     fontname="helv", fontsize=fs, color=(1, 1, 1))


def _add_label(labels: list[tuple[pymupdf.Rect, str]], rect: pymupdf.Rect, ph: str) -> None:
    """One placeholder per box, even when two OCR passes found the same thing."""
    for other, _ in labels:
        overlap = rect & other
        if not overlap.is_empty and overlap.get_area() > 0.3 * min(rect.get_area(), other.get_area()):
            return
    labels.append((rect, ph))


def _dpi(page: pymupdf.Page) -> float:
    """A scanned page is read at the scan's own resolution: resampling it costs OCR accuracy."""
    area = abs(page.rect)
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"])
        if abs(bbox) > 0.8 * area and bbox.width > 0:
            return min(ocr.MAX_DPI, max(ocr.MIN_DPI, info["width"] / (bbox.width / 72)))
    return ocr.RENDER_DPI


def _image(page: pymupdf.Page, dpi: float) -> Image.Image:
    pix = page.get_pixmap(dpi=round(dpi), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _render(page: pymupdf.Page) -> _Ocr:
    dpi = _dpi(page)
    img = _image(page, dpi)
    return _Ocr(page.number, img, page.rect.width / img.width, dpi)


def _read_pages(doc: pymupdf.Document, numbers: list[int], matcher: ProfileMatcher) -> list[_Ocr]:
    out = []
    for i in range(0, len(numbers), OCR_BATCH):
        batch = [_render(doc[n]) for n in numbers[i:i + OCR_BATCH]]
        _read_all(batch, matcher)
        for p in batch:
            p.img = None  # rendered again if the page is rebuilt
        out += batch
    return out


def _read_all(pages: list[_Ocr], matcher: ProfileMatcher) -> None:
    views = [(p, ocr.variants(p.img)) for p in pages]
    jobs = [(ocr.png(img), psm) for _, vs in views for img, _ in vs for psm in ocr.PASSES]
    tsvs = iter(ocr.run_all(jobs, tsv=True))
    for p, vs in views:
        reads = [(ocr.parse_tsv(next(tsvs)), m) for _, m in vs for _ in ocr.PASSES]
        if p.scale <= 0:  # a photo: no physical size, so size it from its text
            h = next((h for h in (ocr.median_height(w) for w, _ in reads) if h), None)
            p.scale = PHOTO_TEXT_HEIGHT / h if h else 72 / 200
        p.reads = [(ocr.page_text(w, p.number, p.scale, m), w) for w, m in reads]
        p.found = [(pt, f) for pt, _ in p.reads for f in detect(pt, matcher)]


def _repeat_ocr(p: _Ocr, values: dict[str, Finding]) -> None:
    """OCR reads disagree (a label is recognised in one and not another), so a value
    any read or page redacts is redacted wherever any read sees it."""
    for pt, _ in p.reads:
        p.found += [(pt, f) for f in repeated(pt, [f for t, f in p.found if t is pt], values)]


def _whole_words(pt: PageText, f: Finding) -> tuple[int, int]:
    """OCR character boxes are estimates, so cover every word the finding touches."""
    start, end = f.start, f.end
    while start > 0 and not pt.text[start - 1].isspace():
        start -= 1
    while end < len(pt.text) and not pt.text[end].isspace():
        end += 1
    return start, end


def _ocr_regions(p: _Ocr, vault: Vault, result: Result, within: list[pymupdf.Rect] | None = None,
                 ) -> list[tuple[pymupdf.Rect, str, Finding]]:
    """Padded rectangles (page points) to remove, from every read of the page."""
    out = []
    counted: set[tuple[str, str]] = set()
    for pt, f in p.found:
        rects = pt.rects(*_whole_words(pt, f))
        if within is not None and not any(r.intersects(w) for r in rects for w in within):
            continue
        ph = vault.placeholder(f.kind, f.key, f.text)
        if (f.key, f.text) not in counted:  # the same value seen by several reads counts once
            counted.add((f.key, f.text))
            result.findings.append(f)
        result.placeholders[ph] = f.kind
        for r in rects:
            pad = 0.25 * r.height
            out.append((pymupdf.Rect(r.x0 - pad, r.y0 - 0.6 * pad, r.x1 + pad, r.y1 + 0.6 * pad), ph, f))
    return out


def _substituted(pt: PageText, regions: list[tuple[pymupdf.Rect, str, Finding]]) -> str:
    """The OCR text with every redacted run replaced by its placeholder."""
    out: list[str] = []
    last_ph, pending = None, ""
    for c, box in zip(pt.text, pt.boxes, strict=True):
        if box is None:
            pending += c
            if c == "\n":
                last_ph = None
            continue
        centre = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        ph = next((ph for r, ph, _ in regions if centre in r), None)
        if ph is not None and ph == last_ph:
            pending = ""  # same redaction continues across a space
            continue
        out.append(pending)
        pending = ""
        out.append(ph if ph is not None else c)
        last_ph = ph
    out.append(pending)
    return "".join(out)


def _rebuild(doc: pymupdf.Document, p: _Ocr, regions, at: int | None) -> str:
    """Replace page `at` (or append) with the burned image; return its placeholder text."""
    img = p.img.copy() if p.img is not None else _image(doc[at], p.dpi)
    draw = ImageDraw.Draw(img)
    labels: list[tuple[pymupdf.Rect, str]] = []
    for r, ph, _ in regions:
        draw.rectangle([r.x0 / p.scale, r.y0 / p.scale, r.x1 / p.scale, r.y1 / p.scale], fill=(0, 0, 0))
        _add_label(labels, r, ph)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=JPEG_QUALITY)
    width, height = img.width * p.scale, img.height * p.scale
    if at is not None:
        doc.delete_page(at)
    page = doc.new_page(at if at is not None else -1, width=width, height=height)
    page.insert_image(page.rect, stream=buf.getvalue())
    for rect, ph in labels:
        _label(page, rect, ph)
    # the clearest pass becomes the text: most characters read with confidence
    pt, _ = max(p.reads, key=lambda r: sum(len(w.text) for w in r[1] if w.conf >= 60))
    return _substituted(pt, regions).rstrip()


def _visible_text(page: pymupdf.Page) -> bool:
    """False for a bare scan, and for a scan whose only text is a scanner's invisible
    OCR layer (render mode 3): both are read and rebuilt as images."""
    return any(t["type"] != 3 and t["chars"] for t in page.get_texttrace()) and bool(page.get_text().strip())


def _image_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    return [r for r in rects if r.width > 36 and r.height > 10]  # skip rules and dots


def redact_file(src: Path, out_dir: Path, matcher: ProfileMatcher, vault: Vault) -> Result:
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(src)
    result = Result(src, out_dir / f"{stem}.redacted.pdf", out_dir / f"{stem}.redacted.txt")
    texts: dict[int, str] = {}

    if src.suffix.lower() in IMAGE_SUFFIXES:
        doc = pymupdf.open()
        photo = _Ocr(0, load_image(src), 0.0)
        _read_all([photo], matcher)
        _repeat_ocr(photo, repeat_values([f for _, f in photo.found]))
        texts[0] = _rebuild(doc, photo, _ocr_regions(photo, vault, result), None)
        result.image_pages.append(1)
    else:
        doc = pymupdf.open(src)
        if doc.needs_pass:
            raise ValueError(f"{src.name} is password-protected; remove the password first")
        doc.bake(annots=True, widgets=True)
        # pass 1: find everything, on every page
        text_pages: dict[int, tuple[PageText, list[Finding]]] = {}
        scan_numbers, mixed_numbers = [], []
        for page in doc:
            if not _visible_text(page):
                scan_numbers.append(page.number)
                continue
            pt = extract(page)
            text_pages[page.number] = (pt, detect(pt, matcher))
            if _image_rects(page):
                mixed_numbers.append(page.number)
        read = _read_pages(doc, scan_numbers + mixed_numbers, matcher)
        scans, mixed = read[:len(scan_numbers)], {p.number: p for p in read[len(scan_numbers):]}
        everything = [f for _, fs in text_pages.values() for f in fs] + [f for p in read for _, f in p.found]
        values = repeat_values(everything)
        relink(everything, values)

        # pass 2: add what the rest of the document found, and redact
        for page in doc:
            if page.number not in text_pages:
                continue
            pt, found = text_pages.pop(page.number)
            found = merge(found + repeated(pt, found, values), pt)
            ocr_page = mixed.get(page.number)
            if ocr_page is not None:
                _repeat_ocr(ocr_page, values)
            _redact_text_page(page, pt, found, vault, result, ocr_page)
        for p in scans:
            _repeat_ocr(p, values)
            texts[p.number] = _rebuild(doc, p, _ocr_regions(p, vault, result), p.number)
            result.image_pages.append(p.number + 1)

    doc.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
              javascript=True, metadata=True, redactions=False, remove_links=True, reset_fields=True,
              reset_responses=True, thumbnails=True, xml_metadata=True)
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for widget in list(page.widgets()):
            page.delete_widget(widget)
    doc.set_metadata({})
    doc.save(result.pdf, garbage=4, deflate=True, clean=True)
    doc.close()

    out = pymupdf.open(result.pdf)
    pages = [f"=== Page {p.number + 1} ===\n"
             f"{texts[p.number] if p.number in texts else p.get_text(sort=True).rstrip()}\n" for p in out]
    result.text.write_text(f"[{src.name} — redacted by taxonymous]\n\n" + "\n".join(pages))
    vault.save()
    return result


def _redact_text_page(page: pymupdf.Page, pt: PageText, found: list[Finding], vault: Vault,
                      result: Result, images: _Ocr | None) -> None:
    labels: list[tuple[pymupdf.Rect, str]] = []
    for f in found:
        ph = vault.placeholder(f.kind, f.key, f.text)
        result.findings.append(f)
        result.placeholders[ph] = f.kind
        for i, line_rect in enumerate(pt.rects(f.start, f.end)):
            # trim to the middle of the line: char boxes are taller than glyphs and
            # overlap the lines above/below, which would otherwise be removed too
            h = line_rect.height
            rect = pymupdf.Rect(line_rect.x0, line_rect.y0 + 0.2 * h, line_rect.x1, line_rect.y1 - 0.2 * h)
            page.add_redact_annot(rect, fill=(0, 0, 0))
            if i == 0:
                labels.append((rect, ph))
    if images is not None:
        # identifiers inside pictures on the page: only the image pixels go, text is handled above
        for rect, ph, _ in _ocr_regions(images, vault, result, within=_image_rects(page)):
            page.add_redact_annot(rect, fill=(0, 0, 0))
            _add_label(labels, rect, ph)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    # placeholders go in after the real text is gone
    for rect, ph in labels:
        _label(page, rect, ph)
