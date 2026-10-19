"""Which text does macOS (Preview, Quick Look) leave out that MuPDF, and so the review UI, draws?

    uv run python -m tools.viewer_diff ORIGINAL.pdf REDACTED.pdf [PAGE]

Renders one page of each file with MuPDF and with macOS's own renderer (sips), then
reports per font how many characters have ink under them in each. Prints font names,
font facts and counts only, never the text, so the output is safe to share.
"""

import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

DPI = 72  # sips renders at the page's point size
INK = 220  # gray level below which a pixel counts as ink (small text at 72 dpi is light)


def _apple(pdf: Path, page: int, tmp: Path) -> np.ndarray:
    one = pdf  # sips draws the first page; another page goes into a file of its own
    if page:
        one = tmp / f"{pdf.stem}-p.pdf"
        doc = pymupdf.open(pdf)
        doc.select([page])
        doc.save(one)
    png = tmp / f"{pdf.stem}-p.png"
    subprocess.run(["sips", "-s", "format", "png", str(one), "--out", str(png)],
                   check=True, capture_output=True)
    im = Image.open(png).convert("RGBA")
    flat = Image.alpha_composite(Image.new("RGBA", im.size, "white"), im).convert("L")
    return np.asarray(flat) < INK


def _mupdf(page: pymupdf.Page) -> np.ndarray:
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w) < INK


def _inked(ink: np.ndarray, bbox) -> bool:
    x0, y0, x1, y1 = (round(v) for v in bbox)
    return bool(ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)].any())


def _fonts(doc: pymupdf.Document, page: pymupdf.Page) -> dict[str, str]:
    out = {}
    for xref, ext, ftype, name, ref, enc, *_ in page.get_fonts(full=True):
        embedded = "embedded" if ext not in ("n/a", "") else "NOT embedded"
        out[name.split("+")[-1]] = f"{ftype}, {enc or 'no encoding'}, {embedded}, xref {xref}, as /{ref}"
    return out


def report(pdf: Path, n: int, tmp: Path) -> None:
    doc = pymupdf.open(pdf)
    page = doc[n]
    mu, apple = _mupdf(page), _apple(pdf, n, tmp)
    if mu.shape != apple.shape:
        apple = np.asarray(Image.fromarray(apple).resize(mu.shape[::-1]))
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].isspace():
                        continue
                    c = counts[span["font"].split("+")[-1]]
                    c[0] += 1
                    c[1] += _inked(mu, ch["bbox"])
                    c[2] += _inked(apple, ch["bbox"])
    print(f"{pdf.name}, page {n + 1}: ink {mu.mean():.4f} MuPDF, {apple.mean():.4f} macOS")
    facts = _fonts(doc, page)
    for font, (total, m, a) in sorted(counts.items(), key=lambda kv: -kv[1][0]):
        flag = "  <-- macOS leaves these out" if m and a < m * 0.5 else ""
        print(f"  {font}: {total} chars, drawn by MuPDF {m}, by macOS {a}{flag}")
        print(f"      {facts.get(font, '(font not in the page resources: drawn inside a form XObject?)')}")
    xobjects = page.get_xobjects()
    if xobjects:
        print(f"  form XObjects on the page: {len(xobjects)}")
    contents = b"".join(doc.xref_stream(x) or b"" for x in page.get_contents())
    for op in (b"/OC ", b"BDC", b" Tr", b" gs", b"Do"):
        print(f"  content stream: {op.decode().strip()} ×{contents.count(op)}")
    print()


def main() -> None:
    original, redacted = Path(sys.argv[1]), Path(sys.argv[2])
    n = int(sys.argv[3]) - 1 if len(sys.argv) > 3 else 0
    with tempfile.TemporaryDirectory() as tmp:
        for pdf in (original, redacted):
            report(pdf, n, Path(tmp))


if __name__ == "__main__":
    main()
