"""Turn clean fixtures into scans and phone photos.

All randomness comes from a seeded Random so output is reproducible.
"""

import io
import re
from pathlib import Path
from random import Random

import pymupdf
from PIL import Image, ImageFilter

from .manifest import DocRecord, Item


def _norm(s: str) -> str:
    return re.sub(r"[\s\-]", "", s).lower()


def _noise(img: Image.Image, rng: Random, strength: float) -> Image.Image:
    grey = Image.frombytes("L", img.size, rng.randbytes(img.width * img.height))
    return Image.blend(img.convert("L"), grey, strength)


def _render(src: Path, page: int, dpi: int) -> Image.Image:
    pix = pymupdf.open(src)[page].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _subset(rec: DocRecord, src: Path, pages: list[int]) -> tuple[list[Item], list[Item]]:
    """Keep only manifest items that actually appear on the chosen pages."""
    doc = pymupdf.open(src)
    text = _norm(" ".join(doc[p].get_text() for p in pages))
    return ([i for i in rec.redact if _norm(i.value) in text],
            [i for i in rec.keep if _norm(i.value) in text])


def scan(src_rec: DocRecord, src_dir: Path, out_dir: Path, name: str, pages: list[int],
         seed: int) -> DocRecord:
    """Image-only PDF that looks like a flatbed scan: slight skew, noise, blur, JPEG."""
    rng = Random(f"{seed}:scan:{name}")
    src = src_dir / src_rec.file
    redact, keep = _subset(src_rec, src, pages)
    rec = DocRecord(file=f"{name}.pdf", source=f"scan:{src_rec.source}", kind="scan",
                    redact=redact, keep=keep)
    doc = pymupdf.open()
    for p in pages:
        img = _render(src, p, 200)
        img = img.rotate(rng.uniform(-2.5, 2.5), resample=Image.BICUBIC, expand=False, fillcolor=255)
        img = _noise(img, rng, 0.08).filter(ImageFilter.GaussianBlur(0.6))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=55)
        page = doc.new_page(width=img.width * 72 / 200, height=img.height * 72 / 200)
        page.insert_image(page.rect, stream=buf.getvalue())
    doc.set_metadata({})
    doc.save(out_dir / rec.file, garbage=4, deflate=True, no_new_id=True)
    return rec


def photo(src_rec: DocRecord, src_dir: Path, out_dir: Path, name: str, seed: int) -> DocRecord:
    """JPEG that looks like a phone photo of a paper receipt on a table."""
    rng = Random(f"{seed}:photo:{name}")
    src = src_dir / src_rec.file
    redact, keep = _subset(src_rec, src, [0])
    rec = DocRecord(file=f"{name}.jpg", source=f"photo:{src_rec.source}", kind="photo",
                    redact=redact, keep=keep)
    paper = _render(src, 0, 220)
    w, h = paper.size
    canvas = Image.new("L", (int(w * 1.5), int(h * 1.15)), 95)
    canvas.paste(paper, ((canvas.width - w) // 2, (canvas.height - h) // 2))
    # perspective: pull the top corners inward a little, as if shot from below
    dx = w * rng.uniform(0.03, 0.07)
    W, H = canvas.size
    quad = (-dx, 0, -dx * 0.2, H, W + dx * 0.2, H, W + dx, 0)
    canvas = canvas.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=95)
    canvas = canvas.rotate(rng.uniform(-6, 6), resample=Image.BICUBIC, fillcolor=95)
    canvas = _noise(canvas, rng, 0.06).filter(ImageFilter.GaussianBlur(0.8))
    canvas.save(out_dir / rec.file, "JPEG", quality=70)
    return rec
