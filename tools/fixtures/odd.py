"""Odd PDFs for hardening the redactor (M7).

Each builder writes one synthetic PDF that is built the way real-world files
sometimes are (rotated pages, Type3 fonts, text drawn as outlines, incremental
updates, encryption, text hidden off the page or in a hidden layer...), with
the household's identifiers in it. `build_all` returns {file name: the values
that must not survive}.

Values come from the fake household, so nothing here is real.
"""

import io
from collections.abc import Callable
from pathlib import Path

import pymupdf

from .fake import Household

FONT = "helv"
SIZE = 11


def _account(h: Household) -> str:
    return next(iter(h.accounts.values()))


def _lines(h: Household) -> list[str]:
    p = h.taxpayer
    return [
        "Statement of account",
        f"Account holder: {p.name}",
        f"SSN: {p.ssn}",
        f"Account number: {_account(h)}",
        "Interest paid this year: $1,234.56",
    ]


def _values(h: Household) -> list[str]:
    p = h.taxpayer
    return [p.ssn, p.name, _account(h)]


def _write(page: pymupdf.Page, lines: list[str], x: float = 72, y: float = 90, **kw) -> None:
    for i, line in enumerate(lines):
        page.insert_text((x, y + i * 18), line, fontname=FONT, fontsize=SIZE, **kw)


def _append_stream(doc: pymupdf.Document, page: pymupdf.Page, content: bytes) -> None:
    """Add a content stream to a page, after the ones it has."""
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, content)
    refs = " ".join(f"{x} 0 R" for x in page.get_contents())
    doc.xref_set_key(page.xref, "Contents", f"[{refs} {xref} 0 R]")


# ---------------------------------------------------------------- page geometry

def rotated(h: Household, out: Path, angle: int) -> list[str]:
    """Pages stored sideways with /Rotate (common in scanned-then-OCR'd and landscape files)."""
    doc = pymupdf.open()
    page = doc.new_page()
    _write(page, _lines(h))
    page.set_rotation(angle)
    doc.save(out)
    return _values(h)


def rotated_text(h: Household, out: Path) -> list[str]:
    """Upright page, text running vertically (a margin note, a sideways table)."""
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(_lines(h)):
        page.insert_text((300 + i * 18, 700), line, fontname=FONT, fontsize=SIZE, rotate=90)
    doc.save(out)
    return _values(h)


def off_page(h: Household, out: Path) -> list[str]:
    """Identifiers drawn outside the page: invisible in a viewer, but in the file."""
    doc = pymupdf.open()
    page = doc.new_page()
    _write(page, _lines(h)[:1] + _lines(h)[-1:])
    _write(page, _lines(h)[1:4], x=72, y=-200)
    _write(page, _lines(h)[1:4], x=700, y=200)
    doc.save(out)
    return _values(h)


def outside_cropbox(h: Household, out: Path) -> list[str]:
    """Identifiers inside the MediaBox but outside the CropBox (trimmed off when shown)."""
    doc = pymupdf.open()
    page = doc.new_page()
    _write(page, _lines(h)[:1] + _lines(h)[-1:], y=500)
    _write(page, _lines(h)[1:4], y=60)
    doc.xref_set_key(page.xref, "CropBox", "[0 0 595 442]")  # PDF space: the lower part of the page
    doc.save(out)
    return _values(h)


# ---------------------------------------------------------------- fonts and drawing

def _type3(doc: pymupdf.Document, shift: int, flip: bool = False) -> int:
    """A Type3 font whose glyphs draw Helvetica characters. With `shift`, code c draws
    character c - shift and the glyph names mean nothing, so the text layer extracts
    as garbage while the page reads normally (a broken font encoding). With `flip`, its
    glyphs are drawn upside down, for a page whose coordinates run downwards."""
    helv = doc.get_new_xref()
    doc.update_object(helv, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                            " /Encoding /WinAnsiEncoding >>")
    measure = pymupdf.Font("helv")
    first, last = 32 + shift, 126 + shift
    procs, widths, diffs = [], [], []
    for code in range(first, last + 1):
        ch = chr(code - shift)
        w = round(measure.text_length(ch, fontsize=1000))
        esc = ch.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, f"{w} 0 d0 BT /H 1000 Tf 0 0 Td ({esc}) Tj ET".encode("latin-1"))
        procs.append(f"/g{code} {xref} 0 R")
        widths.append(str(w))
        diffs.append(f"/g{code}")
    charprocs = doc.get_new_xref()
    doc.update_object(charprocs, "<< " + " ".join(procs) + " >>")
    font = doc.get_new_xref()
    doc.update_object(font, (
        f"<< /Type /Font /Subtype /Type3 /FontBBox [0 -250 1000 1000]"
        f" /FontMatrix [0.001 0 0 {-0.001 if flip else 0.001} 0 0]"
        f" /CharProcs {charprocs} 0 R"
        f" /Encoding << /Type /Encoding /Differences [{first} {' '.join(diffs)}] >>"
        f" /FirstChar {first} /LastChar {last} /Widths [{' '.join(widths)}]"
        f" /Resources << /Font << /H {helv} 0 R >> >> >>"))
    return font


def _type3_page(h: Household, out: Path, shift: int) -> list[str]:
    doc = pymupdf.open()
    page = doc.new_page()
    font = _type3(doc, shift)
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /T3 {font} 0 R >> >>")
    ops = ["BT", f"/T3 {SIZE} Tf", "14 TL", f"72 {792 - 90} Td"]
    for line in _lines(h):
        coded = "".join(chr(ord(c) + shift) for c in line)
        esc = coded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj T*")
    ops.append("ET")
    _append_stream(doc, page, "\n".join(ops).encode("latin-1"))
    doc.save(out)
    return _values(h)


def type3(h: Household, out: Path) -> list[str]:
    """A Type3 font (TeX bitmap fonts, some generators) with a usable encoding."""
    return _type3_page(h, out, 0)


def type3_broken_encoding(h: Household, out: Path) -> list[str]:
    """The text layer says one thing, the page shows another."""
    return _type3_page(h, out, 1)


def _flipped_page(doc: pymupdf.Document, font: int, lines: list[str], past_edge: bool) -> None:
    page = doc.new_page()
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /T3 {font} 0 R >> >>")
    _append_stream(doc, page, b"q")
    _append_stream(doc, page, f"1 0 0 -1 0 {page.rect.height:g} cm q 1 0 0 1 10 10 cm Q".encode())
    ops = ["BT", f"/T3 {SIZE} Tf", "18 TL"]
    for i, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"1 0 0 1 72 {72 + i * 18} Tm ({esc}) '")
    edge = [f"0 {page.rect.height - 20:g} 40 26 re f"] if past_edge else []
    _append_stream(doc, page, "\n".join([*ops, "ET", *edge, "Q"]).encode("latin-1"))


def flipped_coordinates(h: Household, out: Path, past_edge: bool = False,
                        plain_page: bool = False) -> list[str]:
    """Coordinates that run down the page, set in one content stream and used in a later
    one, with a font drawn upside down to match, and lines shown with ' (next line, then
    show), as a payroll service's W-2 has them. MuPDF's redaction puts the text it keeps
    in the wrong place on such a page, and misses what it should remove. With `past_edge`,
    a box reaches past the page's edge, so cutting that away rewrites the page first.
    With `plain_page`, a second page without identifiers: cleaning and saving the file
    moves its text though nothing on it is redacted."""
    doc = pymupdf.open()
    font = _type3(doc, 0, flip=True)
    _flipped_page(doc, font, _lines(h), past_edge)
    if plain_page:
        _flipped_page(doc, font, ["Household employment summary", _lines(h)[-1]], False)
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", "<< /S /JavaScript /JS (this.print\\(\\);) >>")
    doc.save(out)
    return _values(h)


def vector_text(h: Household, out: Path) -> list[str]:
    """Text converted to outlines (filled paths), next to ordinary text: there is no
    text layer for the identifiers at all."""
    from fontTools.agl import UV2AGL
    from fontTools.cffLib import CFFFontSet
    from fontTools.pens.recordingPen import RecordingPen

    buf = pymupdf.Font(FONT).buffer
    cff = CFFFontSet()
    cff.decompile(io.BytesIO(buf), None)
    glyphs = cff[cff.fontNames[0]].CharStrings
    measure = pymupdf.Font(FONT)

    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:], y=90)
    ops, height = [], page.rect.height
    scale = SIZE / 1000
    for i, line in enumerate(lines[1:4]):
        x, base = 72.0, height - (160 + i * 18)
        for ch in line:
            name = UV2AGL.get(ord(ch), "space")
            if name in glyphs and ch != " ":
                pen = RecordingPen()
                glyphs[name].draw(pen)
                for op, pts in pen.value:
                    xy = [f"{x + px * scale:.2f} {base + py * scale:.2f}" for px, py in pts]
                    if op == "moveTo":
                        ops.append(f"{xy[0]} m")
                    elif op == "lineTo":
                        ops.append(f"{xy[0]} l")
                    elif op == "curveTo":
                        ops.append(f"{' '.join(xy)} c")
                    elif op == "closePath":
                        ops.append("h")
                ops.append("f")
            x += measure.text_length(ch, fontsize=SIZE)
    _append_stream(doc, page, ("0 g\n" + "\n".join(ops)).encode())
    doc.save(out)
    return _values(h)


def letter_spaced(h: Household, out: Path) -> list[str]:
    """Each character placed on its own, wide apart (some statement generators)."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:])
    for i, line in enumerate(lines[1:4]):
        x = 72.0
        for ch in line:
            page.insert_text((x, 160 + i * 18), ch, fontname=FONT, fontsize=SIZE)
            x += pymupdf.Font(FONT).text_length(ch, fontsize=SIZE) + 3.5
    doc.save(out)
    return _values(h)


# ---------------------------------------------------------------- hidden content

def hidden_layer(h: Household, out: Path) -> list[str]:
    """Identifiers in an optional content group that is switched off."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:])
    ocg = doc.add_ocg("Internal", on=False)
    _write(page, lines[1:4], y=200, oc=ocg)
    doc.save(out)
    return _values(h)


def invisible_text(h: Household, out: Path) -> list[str]:
    """Identifiers in render mode 3 (invisible) on a page with ordinary text."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:])
    _write(page, lines[1:4], y=200, render_mode=3)
    doc.save(out)
    return _values(h)


def white_and_tiny(h: Household, out: Path) -> list[str]:
    """White-on-white and 1-point text."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:])
    _write(page, lines[1:4], y=200, color=(1, 1, 1))
    for i, line in enumerate(lines[1:4]):
        page.insert_text((72, 400 + i * 2), line, fontname=FONT, fontsize=1)
    doc.save(out)
    return _values(h)


def barcodes(h: Household, out: Path, rotation: int = 0) -> list[str]:
    """A state return's 2D barcode holding the return's data, as a picture, and a QR code
    and a linear barcode of the SSN drawn as bars (a payment voucher)."""
    import numpy as np
    import zxingcpp

    p = h.taxpayer
    doc = pymupdf.open()
    page = doc.new_page()
    _write(page, _lines(h))
    data = f"{p.name}|{p.ssn}|{_account(h)}|1234.56"
    pdf417 = np.asarray(zxingcpp.create_barcode(data, zxingcpp.PDF417).to_image(scale=2))
    page.insert_image(pymupdf.Rect(72, 300, 72 + pdf417.shape[1] * 0.6, 300 + pdf417.shape[0] * 0.6),
                      stream=_png(pdf417))
    for fmt, x, y, module in [(zxingcpp.QRCode, 72, 450, 1.6), (zxingcpp.Code128, 300, 450, 1.0)]:
        bits = np.asarray(zxingcpp.create_barcode(p.ssn, fmt).to_image(scale=1)) < 128
        shape = page.new_shape()
        for row, line in enumerate(bits):  # each run of dark modules on a row is one rectangle
            col = 0
            while col < len(line):
                if line[col]:
                    end = col
                    while end < len(line) and line[end]:
                        end += 1
                    shape.draw_rect(pymupdf.Rect(x + col * module, y + row * module,
                                                 x + end * module, y + (row + 1) * module))
                    col = end
                else:
                    col += 1
        shape.finish(fill=(0, 0, 0), color=None, width=0)
        shape.commit()
    page.set_rotation(rotation)
    doc.save(out)
    return _values(h)


def _png(gray) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(gray).save(buf, "PNG")
    return buf.getvalue()


def covered_by_image(h: Household, out: Path) -> list[str]:
    """Identifiers under an opaque picture (a lazy 'redaction')."""
    doc = pymupdf.open()
    page = doc.new_page()
    _write(page, _lines(h))
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 10), False)
    pix.clear_with(0)
    page.insert_image(pymupdf.Rect(60, 100, 400, 160), pixmap=pix)
    doc.save(out)
    return _values(h)


def form_xobject(h: Household, out: Path) -> list[str]:
    """The identifiers live in a Form XObject shown on two pages (a letterhead/template)."""
    src = pymupdf.open()
    _write(src.new_page(), _lines(h)[1:4])
    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page()
        page.show_pdf_page(pymupdf.Rect(0, 0, 612, 792), src, 0)
        _write(page, _lines(h)[:1] + _lines(h)[-1:], y=500)
    doc.save(out)
    return _values(h)


def shared_contents(h: Household, out: Path) -> list[str]:
    """Two pages that point at the same content stream."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    doc.new_page()
    doc.xref_set_key(doc[1].xref, "Contents", doc.xref_get_key(doc[0].xref, "Contents")[1])
    doc.xref_set_key(doc[1].xref, "Resources", doc.xref_get_key(doc[0].xref, "Resources")[1])
    doc.save(out)
    return _values(h)


def actual_text(h: Household, out: Path) -> list[str]:
    """Marked content whose /ActualText (what a reader copies) holds identifiers that the
    page itself doesn't show."""
    doc = pymupdf.open()
    page = doc.new_page()
    lines = _lines(h)
    _write(page, lines[:1] + lines[-1:])
    spans = []
    for i, line in enumerate(lines[1:4]):
        y = page.rect.height - (200 + i * 18)
        esc = line.replace("(", "\\(").replace(")", "\\)")
        spans.append(f"/Span << /ActualText ({esc}) >> BDC 0.5 g 72 {y} 120 8 re f EMC")
    _append_stream(doc, page, "\n".join(spans).encode("latin-1"))
    doc.save(out)
    return _values(h)


# ---------------------------------------------------------------- file structure

def bookmarks(h: Household, out: Path) -> list[str]:
    """Bookmarks (the outline) and page labels named after the account holder."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    p = h.taxpayer
    doc.set_toc([[1, f"{p.name} statement", 1], [2, f"Account {_account(h)}", 1]])
    doc.set_page_labels([{"startpage": 0, "prefix": f"{p.ssn}-", "style": "D", "firstpagenum": 1}])
    doc.save(out)
    return _values(h)


def orphan_object(h: Household, out: Path) -> list[str]:
    """An object nothing points at (left over by an editor), holding identifiers."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    xref = doc.get_new_xref()
    p = h.taxpayer
    doc.update_object(xref, f"<< /Note ({p.name} {p.ssn} {_account(h)}) >>")
    doc.save(out, garbage=0)
    return _values(h)


def incremental_update(h: Household, out: Path) -> list[str]:
    """The first revision shows the SSN; an incremental update replaced the page.
    The old revision is still in the file's bytes."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    doc.save(out)
    doc = pymupdf.open(out)
    page = doc[0]
    page.add_redact_annot(page.search_for(h.taxpayer.ssn)[0])
    page.apply_redactions()
    doc.saveIncr()
    return _values(h)


def owner_password(h: Household, out: Path) -> list[str]:
    """Encrypted with only an owner password: opens without one, permissions restricted."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret",
             permissions=pymupdf.PDF_PERM_PRINT)
    return _values(h)


def user_password(h: Household, out: Path) -> list[str]:
    """Needs a password to open: refused with a clear message, never half-processed."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw="user-secret")
    return _values(h)


def truncated(h: Household, out: Path) -> list[str]:
    """A damaged file: the end (xref table, trailer) is cut off."""
    doc = pymupdf.open()
    _write(doc.new_page(), _lines(h))
    data = doc.tobytes()
    out.write_bytes(data[: int(len(data) * 0.8)])
    return _values(h)


BUILDERS: dict[str, Callable[[Household, Path], list[str]]] = {
    "rotate_90.pdf": lambda h, o: rotated(h, o, 90),
    "rotate_180.pdf": lambda h, o: rotated(h, o, 180),
    "rotate_270.pdf": lambda h, o: rotated(h, o, 270),
    "rotated_text.pdf": rotated_text,
    "off_page.pdf": off_page,
    "outside_cropbox.pdf": outside_cropbox,
    "type3.pdf": type3,
    "type3_broken_encoding.pdf": type3_broken_encoding,
    "flipped_coordinates.pdf": flipped_coordinates,
    "flipped_past_edge.pdf": lambda h, o: flipped_coordinates(h, o, past_edge=True),
    "flipped_plain_page.pdf": lambda h, o: flipped_coordinates(h, o, plain_page=True),
    "vector_text.pdf": vector_text,
    "letter_spaced.pdf": letter_spaced,
    "hidden_layer.pdf": hidden_layer,
    "invisible_text.pdf": invisible_text,
    "white_and_tiny.pdf": white_and_tiny,
    "covered_by_image.pdf": covered_by_image,
    "barcodes.pdf": barcodes,
    "barcodes_rotated.pdf": lambda h, o: barcodes(h, o, rotation=90),
    "actual_text.pdf": actual_text,
    "bookmarks.pdf": bookmarks,
    "orphan_object.pdf": orphan_object,
    "form_xobject.pdf": form_xobject,
    "shared_contents.pdf": shared_contents,
    "incremental_update.pdf": incremental_update,
    "owner_password.pdf": owner_password,
    "user_password.pdf": user_password,
    "truncated.pdf": truncated,
}

REFUSED = {"user_password.pdf"}  # must be held back with an error, not redacted


def build_all(h: Household, out_dir: Path) -> dict[str, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return {name: build(h, out_dir / name) for name, build in BUILDERS.items()}
