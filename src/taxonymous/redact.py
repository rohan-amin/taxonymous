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

The reviewer's decisions for a document (Overrides) are applied on every run:
placeholders to keep in the clear everywhere in it, single boxes to leave in the
clear (only that spot; the same value elsewhere is still redacted), and boxes they
drew to redact.
"""

import io
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageOps

from . import ocr
from .detect import Finding, ProfileMatcher, continue_numbers, detect, merge, relink, repeat_values, repeated
from .pagetext import PageText, extract
from .progress import NONE, Progress
from .sanitize import allow_list, glyphs, moved, sanitize
from .vault import Vault

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif", ".webp"}
SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
PHOTO_TEXT_HEIGHT = 6.5  # points: OCR word boxes in a photo are scaled so text is about this tall
JPEG_QUALITY = 80
OCR_BATCH = 8  # scanned pages rendered and read at a time


@dataclass
class Region:
    """One black box in the output: where it is and what it stands for, never the value."""
    page: int  # 0-based
    rect: tuple[float, float, float, float]  # page points
    placeholder: str
    kind: str
    source: str  # profile | pattern | label | ner | repeat | manual


@dataclass
class Overrides:
    """A reviewer's decisions for one document. Holds placeholders and boxes, no values."""
    keep: set[str] = field(default_factory=set)  # placeholders left in the clear in this document
    add: list[tuple[int, tuple[float, float, float, float]]] = field(default_factory=list)  # (page, rect)
    # single boxes left in the clear: (page, rect, placeholder)
    show: list[tuple[int, tuple[float, float, float, float], str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Overrides":
        if not Path(path).exists():
            return cls()
        d = json.loads(Path(path).read_text())
        return cls(set(d.get("keep", [])), [(a["page"], tuple(a["rect"])) for a in d.get("add", [])],
                   [(a["page"], tuple(a["rect"]), a["placeholder"]) for a in d.get("show", [])])

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        add = [{"page": n, "rect": list(r)} for n, r in self.add]
        show = [{"page": n, "rect": list(r), "placeholder": ph} for n, r, ph in self.show]
        data = {"keep": sorted(self.keep), "add": add, "show": show}
        Path(path).write_text(json.dumps(data, indent=2) + "\n")

    def shown(self, page: int) -> list[pymupdf.Rect]:
        return [pymupdf.Rect(r) for n, r, _ in self.show if n == page]


def overlaps(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    """The same box: they share more than 30% of the smaller one."""
    both = a & b
    return not both.is_empty and both.get_area() > 0.3 * min(a.get_area(), b.get_area())


def _is_shown(rects, shown: list[pymupdf.Rect]) -> bool:
    return any(overlaps(pymupdf.Rect(r), s) for r in rects for s in shown)


def manual_key(stem: str, page: int, rect) -> str:
    return f"manual:{stem}:{page}:" + ",".join(f"{v:.1f}" for v in rect)


@dataclass
class Result:
    source: Path
    pdf: Path
    text: Path
    findings: list[Finding] = field(default_factory=list)
    placeholders: dict[str, str] = field(default_factory=dict)  # placeholder -> kind
    image_pages: list[int] = field(default_factory=list)  # 1-based pages rebuilt from OCR
    moved: list[int] = field(default_factory=list)  # 1-based: redacting their text moved what stays
    regions: list[Region] = field(default_factory=list)

    def counts(self) -> Counter:
        return Counter(f.kind for f in self.findings)

    def region(self, page: int, rect: pymupdf.Rect, ph: str, kind: str, source: str) -> None:
        """Record a box, once: OCR reads and repeats find the same thing many times over."""
        for r in self.regions:
            if r.page == page and r.placeholder == ph and overlaps(pymupdf.Rect(r.rect), rect):
                return
        self.regions.append(Region(page, tuple(round(v, 2) for v in rect), ph, kind, source))

    def regions_json(self) -> list[dict]:
        return [asdict(r) for r in self.regions]


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
    if not any(overlaps(rect, other) for other, _ in labels):
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


def _read_pages(doc: pymupdf.Document, numbers: list[int], matcher: ProfileMatcher,
                progress: Progress = NONE) -> list[_Ocr]:
    out = []
    for i in range(0, len(numbers), OCR_BATCH):
        batch = [_render(doc[n]) for n in numbers[i:i + OCR_BATCH]]
        _read_all(batch, matcher, progress)
        for p in batch:
            p.img = None  # rendered again if the page is rebuilt
        out += batch
    return out


def _read_all(pages: list[_Ocr], matcher: ProfileMatcher, progress: Progress = NONE) -> None:
    views = [(p, ocr.variants(p.img)) for p in pages]
    jobs = [(ocr.png(img), psm) for _, vs in views for img, _ in vs for psm in ocr.PASSES]
    done = progress.per_page([len(vs) * len(ocr.PASSES) for _, vs in views])
    tsvs = iter(ocr.run_all(jobs, tsv=True, done=done))
    for p, vs in views:
        reads = [(ocr.tighten(ocr.parse_tsv(next(tsvs)), img), m) for img, m in vs for _ in ocr.PASSES]
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
                 shown: list[pymupdf.Rect] = ()) -> list[tuple[pymupdf.Rect, str, Finding]]:
    """Padded rectangles (page points) to remove, from every read of the page, except
    the boxes the reviewer chose to leave in the clear."""
    out = []
    counted: set[tuple[str, str]] = set()
    for pt, f in p.found:
        if f.source == "manual":
            continue
        rects = pt.rects(*_whole_words(pt, f))
        if within is not None and not any(r.intersects(w) for r in rects for w in within):
            continue
        padded = []
        for r in rects:
            pad = 0.25 * r.height
            padded.append(pymupdf.Rect(r.x0 - pad, r.y0 - 0.6 * pad, r.x1 + pad, r.y1 + 0.6 * pad))
        if _is_shown(padded, shown):
            continue
        ph = vault.placeholder(f.kind, f.key, f.text)
        if (f.key, f.text) not in counted:  # the same value seen by several reads counts once
            counted.add((f.key, f.text))
            result.findings.append(f)
        result.placeholders[ph] = f.kind
        out += [(r, ph, f) for r in padded]
    return out


def _text_regions(pt: PageText, found: list[Finding], vault: Vault, result: Result,
                  shown: list[pymupdf.Rect]) -> list[tuple[pymupdf.Rect, str, Finding]]:
    """Padded rectangles over what the text layer found, for a page rebuilt as an image."""
    out = []
    for f in found:
        rects = [r + (-1, -1, 1, 1) for r in pt.rects(f.start, f.end)]
        if _is_shown(rects, shown):
            continue
        ph = vault.placeholder(f.kind, f.key, f.text)
        if not any(g.page == f.page and g.key == f.key and g.text == f.text for g in result.findings):
            result.findings.append(f)
        result.placeholders[ph] = f.kind
        out += [(r, ph, f) for r in rects]
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


def _text_in(pt: PageText, rect: pymupdf.Rect) -> str:
    """What a read has inside a box (a reviewer's box on a scan)."""
    chars = [c for c, b in zip(pt.text, pt.boxes, strict=True)
             if b is not None and pymupdf.Point((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2) in rect]
    return "".join(chars).strip()


def _manual_ocr(p: _Ocr, stem: str, boxes: list[pymupdf.Rect], vault: Vault, result: Result,
                ) -> list[tuple[pymupdf.Rect, str, Finding]]:
    """A reviewer's boxes on an image page, as regions. The text OCR read inside each one
    becomes a known value, so verification checks it is gone."""
    out = []
    for rect in boxes:
        key = manual_key(stem, p.number, rect)
        values = {_text_in(pt, rect) for pt, _ in p.reads} - {""}
        ph = vault.placeholder("REDACTED", key, "")
        for v in sorted(values):
            vault.placeholder("REDACTED", key, v)
        f = Finding(p.number, 0, 0, "REDACTED", key, "manual", max(values, key=len, default=""))
        result.findings += [Finding(p.number, 0, 0, "REDACTED", key, "manual", v) for v in values]
        result.placeholders[ph] = "REDACTED"
        out.append((rect, ph, f))
    return out


def _rebuild(doc: pymupdf.Document, p: _Ocr, regions, at: int | None, result: Result) -> str:
    """Replace page `at` (or append) with the burned image; return its placeholder text."""
    img = p.img.copy() if p.img is not None else _image(doc[at], p.dpi)
    draw = ImageDraw.Draw(img)
    labels: list[tuple[pymupdf.Rect, str]] = []
    for r, ph, f in regions:
        draw.rectangle([r.x0 / p.scale, r.y0 / p.scale, r.x1 / p.scale, r.y1 / p.scale], fill=(0, 0, 0))
        _add_label(labels, r, ph)
        result.region(p.number, r, ph, f.kind, f.source)
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


def redact_file(src: Path, out_dir: Path, matcher: ProfileMatcher, vault: Vault,
                overrides: Overrides | None = None, as_images: frozenset[int] = frozenset(),
                progress: Progress = NONE, text_too: frozenset[int] = frozenset()) -> Result:
    """`as_images`: 0-based pages to rebuild from OCR even though they have a text layer
    (one that doesn't match what the page shows). `text_too`: those of them whose text
    layer is right (only redacting it went wrong): what it has is blacked out as well."""
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(src)
    result = Result(src, out_dir / f"{stem}.redacted.pdf", out_dir / f"{stem}.redacted.txt")
    texts: dict[int, str] = {}
    overrides = overrides or Overrides()
    manual: dict[int, list[pymupdf.Rect]] = {}
    for n, rect in overrides.add:
        manual.setdefault(n, []).append(pymupdf.Rect(rect))

    def wanted(f: Finding) -> bool:
        return vault.by_key.get(f.key) not in overrides.keep

    def drop_kept(p: _Ocr) -> None:
        p.found = [(pt, f) for pt, f in p.found if wanted(f)]

    if src.suffix.lower() in IMAGE_SUFFIXES:
        doc = pymupdf.open()
        photo = _Ocr(0, load_image(src), 0.0)
        progress.start("reading", 1)
        _read_all([photo], matcher, progress)
        progress.start("redacting", 1)
        drop_kept(photo)
        _repeat_ocr(photo, repeat_values([f for _, f in photo.found]))
        drop_kept(photo)
        regions = _ocr_regions(photo, vault, result, shown=overrides.shown(0))
        regions += _manual_ocr(photo, stem, manual.get(0, []), vault, result)
        texts[0] = _rebuild(doc, photo, regions, None, result)
        result.image_pages.append(1)
        progress.step()
    else:
        doc = pymupdf.open(src)
        if doc.needs_pass:
            raise ValueError(f"{src.name} is password-protected; remove the password first")
        doc.bake(annots=True, widgets=True)
        broken = sanitize(doc, skip=as_images)  # only what the pages show is left to detect
        if broken:
            # cutting away what is off the page moved their text: start again, and rebuild them
            # from what they show (their text layer is still right)
            doc.close()
            retry = redact_file(src, out_dir, matcher, vault, overrides, as_images=as_images | broken,
                                progress=progress, text_too=text_too | broken)
            retry.moved = sorted(set(retry.moved) | {n + 1 for n in broken})
            return retry
        # pass 1: find everything, on every page
        text_pages: dict[int, tuple[PageText, list[Finding]]] = {}
        scan_text: dict[int, tuple[PageText, list[Finding]]] = {}  # text_too pages
        scan_numbers, mixed_numbers = [], []
        progress.start("reading", doc.page_count)  # a page counts once its text (and OCR, if any) is read
        for page in doc:
            if page.number in as_images or not _visible_text(page):
                scan_numbers.append(page.number)
                if page.number in text_too:
                    pt = extract(page)
                    scan_text[page.number] = (pt, detect(pt, matcher))
                continue
            pt = extract(page)
            text_pages[page.number] = (pt, detect(pt, matcher))
            if _image_rects(page):
                mixed_numbers.append(page.number)
            else:
                progress.step()
        read = _read_pages(doc, scan_numbers + mixed_numbers, matcher, progress)
        scans, mixed = read[:len(scan_numbers)], {p.number: p for p in read[len(scan_numbers):]}
        everything = [f for _, fs in [*text_pages.values(), *scan_text.values()] for f in fs]
        everything += [f for p in read for _, f in p.found]
        relink(everything, repeat_values(everything))
        # the reviewer's keeps apply after linking: keeping a full name keeps its parts too
        text_pages = {n: (pt, [f for f in fs if wanted(f)]) for n, (pt, fs) in text_pages.items()}
        for p in read:
            drop_kept(p)
        scan_text = {n: (pt, [f for f in fs if wanted(f)]) for n, (pt, fs) in scan_text.items()}
        everything = [f for _, fs in [*text_pages.values(), *scan_text.values()] for f in fs]
        everything += [f for p in read for _, f in p.found]
        values = repeat_values(everything)

        # pass 2: add what the rest of the document found, and redact
        progress.start("redacting", doc.page_count)
        for page in doc:
            if page.number not in text_pages:
                continue
            pt, found = text_pages.pop(page.number)
            found = [f for f in merge(found + repeated(pt, found, values), pt) if wanted(f)]
            continue_numbers(found, pt)  # copies found by the repeat step get their suffix too
            ocr_page = mixed.get(page.number)
            if ocr_page is not None:
                _repeat_ocr(ocr_page, values)
                drop_kept(ocr_page)
            _redact_text_page(page, pt, found, vault, result, ocr_page, stem, manual.get(page.number, []),
                              overrides.shown(page.number))
            progress.step()
        for p in scans:
            _repeat_ocr(p, values)
            drop_kept(p)
            regions = _ocr_regions(p, vault, result, shown=overrides.shown(p.number))
            if p.number in scan_text:
                pt, found = scan_text[p.number]
                found = [f for f in merge(found + repeated(pt, found, values), pt) if wanted(f)]
                regions += _text_regions(pt, found, vault, result, overrides.shown(p.number))
            regions += _manual_ocr(p, stem, manual.get(p.number, []), vault, result)
            texts[p.number] = _rebuild(doc, p, regions, p.number, result)
            result.image_pages.append(p.number + 1)
            progress.step()

    if result.moved:
        # rebuild those pages from what they show, as a scan's are; the original draws them right
        doc.close()
        moved = {n - 1 for n in result.moved}
        retry = redact_file(src, out_dir, matcher, vault, overrides, as_images=as_images | moved,
                            progress=progress, text_too=text_too | moved)
        retry.moved = sorted(set(retry.moved) | set(result.moved))
        return retry
    doc.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
              javascript=True, metadata=True, redactions=False, remove_links=True, reset_fields=True,
              reset_responses=True, thumbnails=True, xml_metadata=True)
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for widget in list(page.widgets()):
            page.delete_widget(widget)
    doc.set_metadata({})
    allow_list(doc)  # rebuilding pages and clearing metadata can add keys back
    doc.save(result.pdf, garbage=4, deflate=True, clean=True)
    doc.close()

    out = pymupdf.open(result.pdf)
    pages = [f"=== Page {p.number + 1} ===\n"
             f"{texts[p.number] if p.number in texts else p.get_text(sort=True).rstrip()}\n" for p in out]
    # no file name: one like "Fidelity Rohan Trust 1099.pdf" carries the very name redacted inside
    result.text.write_text("[redacted by taxonymous]\n\n" + "\n".join(pages))
    vault.save()
    return result


STRIP = 1.0  # points: the height of the band text is removed from


def _strip(rect: pymupdf.Rect, chars: list[pymupdf.Rect]) -> pymupdf.Rect:
    """The band through the middle of a line of text, if it crosses every character
    (rotated or slanted text: the whole box)."""
    middle = (rect.y0 + rect.y1) / 2
    if all(c.y0 < middle - STRIP / 2 and middle + STRIP / 2 < c.y1 for c in chars):
        return pymupdf.Rect(rect.x0, middle - STRIP / 2, rect.x1, middle + STRIP / 2)
    return rect


def _redact_text_page(page: pymupdf.Page, pt: PageText, found: list[Finding], vault: Vault,
                      result: Result, images: _Ocr | None, stem: str, manual: list[pymupdf.Rect],
                      shown: list[pymupdf.Rect]) -> None:
    labels: list[tuple[pymupdf.Rect, str]] = []
    # what goes black (and the image pixels under it), and where text is removed: a strip
    # through the middle of each identifier's line. MuPDF removes every character whose box
    # touches a redaction, and a caption printed just above a value ("Employer's FED ID
    # number") touches the value's box, but not that strip.
    boxes: list[pymupdf.Rect] = []
    strips: list[pymupdf.Rect] = []
    for f in found:
        # trim to the middle of the line: char boxes are taller than glyphs and
        # overlap the lines above/below, which would otherwise be removed too
        rects = [pymupdf.Rect(r.x0, r.y0 + 0.2 * r.height, r.x1, r.y1 - 0.2 * r.height)
                 for r in pt.rects(f.start, f.end)]
        if _is_shown(rects, shown):  # the reviewer left this one in the clear
            continue
        ph = vault.placeholder(f.kind, f.key, f.text)
        result.findings.append(f)
        result.placeholders[ph] = f.kind
        chars = [b for b in pt.boxes[f.start:f.end] if b is not None and not b.is_empty]
        for i, rect in enumerate(rects):
            boxes.append(rect)
            strips.append(_strip(rect, [b for b in chars if b.intersects(rect)]))
            result.region(page.number, rect, ph, f.kind, f.source)
            if i == 0:
                labels.append((rect, ph))
    if images is not None:
        # identifiers inside pictures on the page: only the image pixels go, text is handled above
        for rect, ph, f in _ocr_regions(images, vault, result, within=_image_rects(page), shown=shown):
            boxes.append(rect)
            result.region(page.number, rect, ph, f.kind, f.source)
            _add_label(labels, rect, ph)
    for rect in manual:
        # a reviewer's box: whatever text is under it becomes a known value for verification
        key = manual_key(stem, page.number, rect)
        text = " ".join(page.get_textbox(rect).split())
        ph = vault.placeholder("REDACTED", key, text)
        if text:
            result.findings.append(Finding(page.number, 0, 0, "REDACTED", key, "manual", text))
        result.placeholders[ph] = "REDACTED"
        boxes.append(rect)
        strips.append(rect)  # a reviewer's box takes everything it touches
        result.region(page.number, rect, ph, "REDACTED", "manual")
        _add_label(labels, rect, ph)
    before = glyphs(page)
    for rect in boxes:
        page.add_redact_annot(rect, fill=(0, 0, 0))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)
    for rect in strips:
        page.add_redact_annot(rect, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    if moved(before, glyphs(page)):
        result.moved.append(page.number + 1)
    # placeholders go in after the real text is gone
    for rect, ph in labels:
        _label(page, rect, ph)
