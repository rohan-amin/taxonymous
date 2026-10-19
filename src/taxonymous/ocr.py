"""Local OCR (Tesseract) turned into a PageText, so scans and photos go through
the same detectors as text PDFs.

Every image is read several times: as is and straightened (when it is tilted),
each with automatic page segmentation (forms, statements) and sparse-text
segmentation (photos, receipts, scattered boxes). Each read misses things the
others catch, and redaction uses the union of all of them.
"""

import io
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .pagetext import Line, PageText

PASSES = ("3", "11")  # tesseract --psm: automatic layout, sparse text
RENDER_DPI = 300  # for pages with no embedded scan to take the resolution from
MIN_DPI, MAX_DPI = 150, 400
COLUMN_GAP = 1.5  # a gap wider than this many text heights splits an OCR line (form columns)
SIZE_JUMP = 1.8  # so does a jump in text height (an amount next to a big printed year)
CHAR_GAP = 1.5  # or a gap wider than 1.5 characters (word spacing is about 0.6)
# letters Tesseract reads in place of digits; folded back inside number-like tokens
DIGIT_FOLD = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1", "i": "1",
                            "Z": "2", "S": "5", "s": "5", "B": "8", "g": "9", "b": "6"})
NUMBERISH_RE = re.compile(r"\S{4,}")


@dataclass
class Word:
    text: str
    conf: float
    rect: tuple[int, int, int, int]  # pixels: x0, y0, x1, y1
    line: tuple[int, int, int]  # (block, paragraph, line)


def require() -> None:
    if shutil.which("tesseract") is None:
        raise RuntimeError("tesseract is not installed; cannot read scans (brew install tesseract)")


def tesseract(png: bytes, psm: str, tsv: bool = False) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "page.png"
        img.write_bytes(png)
        cmd = ["tesseract", str(img), "stdout", "--psm", psm] + (["tsv"] if tsv else [])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"tesseract failed: {proc.stderr.strip()[:200]}")
        return proc.stdout


def run_all(jobs: list[tuple[bytes, str]], tsv: bool = False,
            done: Callable[[int], None] | None = None) -> list[str]:
    """OCR many (png, psm) jobs in parallel; `done(i)` is called as job i finishes."""
    require()

    def one(i: int) -> str:
        out = tesseract(jobs[i][0], jobs[i][1], tsv)
        if done is not None:
            done(i)
        return out
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        return list(pool.map(one, range(len(jobs))))


def parse_tsv(tsv: str) -> list[Word]:
    words = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        left, top, width, height = map(int, cols[6:10])
        words.append(Word(cols[11].strip(), float(cols[10]), (left, top, left + width, top + height),
                          (int(cols[2]), int(cols[3]), int(cols[4]))))
    return words


def _fold(token: str) -> str:
    """Digits misread as letters, put back: '634619IOO8' -> '6346191008'. Only for
    tokens that are mostly digits already, so words are left alone."""
    n = sum(c.isdigit() for c in token)
    if n >= 3 and n >= 0.6 * sum(c.isalnum() for c in token):
        return token.translate(DIGIT_FOLD)
    return token


def _lines(words: list[Word]) -> list[list[Word]]:
    """Tesseract's lines, split where a wide gap shows they run across columns."""
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for w in words:
        grouped.setdefault(w.line, []).append(w)
    out = []
    for group in grouped.values():
        current = [group[0]]
        for prev, w in zip(group, group[1:], strict=False):
            gap = w.rect[0] - prev.rect[2]
            heights = sorted((prev.rect[3] - prev.rect[1], w.rect[3] - w.rect[1]))
            widths = [(x.rect[2] - x.rect[0]) / len(x.text) for x in (prev, w) if len(x.text) >= 3]
            # a wide gap, or text of a very different size, is the next field
            if (gap > COLUMN_GAP * heights[0] or heights[1] > SIZE_JUMP * heights[0]
                    or (widths and gap > CHAR_GAP * min(widths))):
                out.append(current)
                current = []
            current.append(w)
        out.append(current)
    return out


def page_text(words: list[Word], number: int, scale: float, matrix: pymupdf.Matrix | None = None) -> PageText:
    """A PageText in page points: pixels mapped through `matrix` (from a straightened
    copy back to the original image), then times `scale`. Character boxes are the
    word box split evenly, close enough to cover a match inside a word."""
    pt = PageText(number=number)
    chunks: list[str] = []
    boxes: list[pymupdf.Rect | None] = []
    pos = 0
    for group in _lines(words):
        start = pos
        rect = pymupdf.Rect()
        for j, w in enumerate(group):
            if j:
                chunks.append(" ")
                boxes.append(None)
                pos += 1
            r = pymupdf.Rect(w.rect)
            if matrix is not None:
                r = r * matrix
            x0, y0, x1, y1 = r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale
            text = NUMBERISH_RE.sub(lambda m: _fold(m.group()), w.text)
            step = (x1 - x0) / len(text)
            for k, c in enumerate(text):
                chunks.append(c)
                boxes.append(pymupdf.Rect(x0 + k * step, y0, x0 + (k + 1) * step, y1))
                pos += 1
            rect |= pymupdf.Rect(x0, y0, x1, y1)
        pt.lines.append(Line(start, pos, rect))
        chunks.append("\n")
        boxes.append(None)
        pos += 1
    pt.text = "".join(chunks)
    pt.boxes = boxes
    return pt


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("L").save(buf, "PNG")
    return buf.getvalue()


def _skew_score(img: Image.Image) -> float:
    rows = img.resize((1, img.height), Image.Resampling.BOX).tobytes()
    return sum((a - b) ** 2 for a, b in zip(rows, rows[1:], strict=False))


def skew(img: Image.Image) -> float:
    """How far a scan/photo is tilted, in degrees (PIL's rotate() undoes it): text
    rows give the sharpest horizontal projection profile when level."""
    small = ImageOps.invert(img.convert("L").resize((800, max(1, int(img.height * 800 / img.width)))))
    small = small.point(lambda v: 255 if v > 110 else 0)

    def score(a: float) -> float:
        return _skew_score(small.rotate(a, resample=Image.Resampling.BILINEAR))

    angle = max((a / 2 for a in range(-20, 21)), key=score)
    return max((angle + d / 10 for d in range(-5, 6)), key=score)


def variants(img: Image.Image) -> list[tuple[Image.Image, pymupdf.Matrix | None]]:
    """The image to read, plus a straightened copy if it is tilted, each with the
    matrix that maps its pixels back onto the original."""
    out: list[tuple[Image.Image, pymupdf.Matrix | None]] = [(img, None)]
    angle = skew(img)
    if abs(angle) >= 0.3:
        straight = img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=img.getpixel((0, 0)))
        cx, cy = img.width / 2, img.height / 2
        back = (pymupdf.Matrix(1, 0, 0, 1, -cx, -cy) * pymupdf.Matrix(angle)
                * pymupdf.Matrix(1, 0, 0, 1, cx, cy))
        out.append((straight, back))
    return out


def median_height(words: list[Word]) -> float | None:
    hs = sorted(w.rect[3] - w.rect[1] for w in words if w.conf > 50 and len(w.text) > 2)
    return hs[len(hs) // 2] if hs else None
