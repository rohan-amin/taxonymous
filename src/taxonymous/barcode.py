"""Barcodes, found on the page as drawn and blacked out whatever they hold: a state
return's 2D barcode carries the whole return (names, SSNs, address, birth dates), a
payment voucher's the SSN. Nothing in one is needed to review a document, and verification
fails a page that still has one that reads."""

import pymupdf
import zxingcpp
from PIL import Image

DPI = 200  # a PDF417's narrowest bars are about 0.7 pt: 2 pixels or more at this resolution
PAD = 3.0  # points around what the decoder reports, which is the code's outline, not its quiet zone
# stacked and 2D codes, and the linear codes forms use; not Codabar, DataBar or EAN/UPC,
# which are for goods and more prone to reading table stripes as a code
FORMATS = [zxingcpp.PDF417, zxingcpp.MicroPDF417, zxingcpp.CompactPDF417, zxingcpp.QRCode,
           zxingcpp.MicroQRCode, zxingcpp.RMQRCode, zxingcpp.DataMatrix, zxingcpp.Aztec,
           zxingcpp.MaxiCode, zxingcpp.Code128, zxingcpp.Code39, zxingcpp.Code93, zxingcpp.ITF]


def find(img: Image.Image, scale: float) -> list[pymupdf.Rect]:
    """Each barcode in a picture of a page, as a rectangle in page points (`scale`:
    points per pixel)."""
    out = []
    for r in zxingcpp.read_barcodes(img.convert("L"), formats=FORMATS):
        p = r.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
        rect = pymupdf.Rect(min(xs), min(ys), max(xs) + 1, max(ys) + 1) * scale
        out.append(rect + (-PAD, -PAD, PAD, PAD))
    return out


def on_page(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Each barcode the page shows, in the page's own (unrotated) coordinates, as
    redaction takes them; the picture is of the page as shown, turned."""
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY, alpha=False)
    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    return [r * page.derotation_matrix for r in find(img, page.rect.width / pix.width)]
