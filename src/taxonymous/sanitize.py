"""Make a PDF hold only what its pages show, before anything is detected.

A PDF can carry text that no viewer shows but that a reader (or Claude) can
still extract: bookmarks, page labels, the tagged-PDF structure tree, hidden
layers, /ActualText and /Alt on marked content, and anything drawn outside
the visible page. Rather than chase each one, the document and page
dictionaries are cut down to an allow-list, hidden layers are made visible
(so what is in them is detected and redacted like everything else), marked
content loses its replacement text, and each page is clipped to its visible
area.
"""

import re

import pymupdf

CATALOG_KEYS = {"Type", "Pages", "Version"}
PAGES_KEYS = {"Type", "Parent", "Kids", "Count", "MediaBox", "CropBox", "Rotate", "Resources"}
PAGE_KEYS = {"Type", "Parent", "MediaBox", "CropBox", "Rotate", "Resources", "Contents", "Group", "UserUnit"}
REPLACEMENT_KEYS = ("ActualText", "Alt", "E")  # text a reader uses in place of what is drawn
REPLACEMENT_RE = re.compile(rb"/(?:ActualText|Alt|E)(?=[\s(<])\s*")
EDGE = 1.0  # points: content this far past the edge still counts as on the page


def sanitize(doc: pymupdf.Document) -> None:
    """Call after flattening annotations and form fields (their values are page text by then)."""
    allow_list(doc)
    own_resources(doc)
    _strip_replacement_text(doc)
    for page in doc:
        clip_to_visible(page)


def allow_list(doc: pymupdf.Document) -> None:
    """Cut the document and page dictionaries down to the keys a page needs to display."""
    catalog = doc.pdf_catalog()
    for key in doc.xref_get_keys(catalog):
        if key not in CATALOG_KEYS:  # outline, page labels, layers, names, structure tree, forms...
            doc.xref_set_key(catalog, key, "null")
    for xref in range(1, doc.xref_length()):
        kind = doc.xref_get_key(xref, "Type")[1]
        allowed = PAGE_KEYS if kind == "/Page" else PAGES_KEYS if kind == "/Pages" else None
        if allowed is None:
            continue
        for key in doc.xref_get_keys(xref):
            if key not in allowed:
                doc.xref_set_key(xref, key, "null")


XOBJECT_RE = re.compile(r"/[^\s/<>\[\]()]+\s+(\d+)\s+0\s+R")


def _resources(doc: pymupdf.Document, xref: int) -> str | None:
    """A page's or form's /Resources as an object to point at, from its parents if it has none."""
    while xref:
        kind, value = doc.xref_get_key(xref, "Resources")
        if kind == "xref":
            return value
        if kind == "dict":  # inline: make it an object of its own so forms can share it
            res = doc.get_new_xref()
            doc.update_object(res, value)
            doc.xref_set_key(xref, "Resources", f"{res} 0 R")
            return f"{res} 0 R"
        kind, parent = doc.xref_get_key(xref, "Parent")
        xref = int(parent.split()[0]) if kind == "xref" else 0
    return None


def own_resources(doc: pymupdf.Document) -> None:
    """Give every form XObject without /Resources the ones it borrows from the page (or
    form) that draws it. Redaction rewrites a page's resources down to what its own content
    uses, so a borrowed font disappears: MuPDF draws that text in a stand-in font, macOS
    doesn't draw it at all."""
    seen: set[int] = set()

    def visit(res: str) -> None:
        kind, xobjects = doc.xref_get_key(int(res.split()[0]), "XObject")
        if kind == "xref":
            xobjects = doc.xref_object(int(xobjects.split()[0]))
        elif kind != "dict":
            return
        for m in XOBJECT_RE.finditer(xobjects):
            form = int(m.group(1))
            if form in seen or doc.xref_get_key(form, "Subtype")[1] != "/Form":
                continue
            seen.add(form)
            if doc.xref_get_key(form, "Resources")[0] == "null":
                copy = doc.get_new_xref()  # its own: redacting the page rewrites the page's
                doc.update_object(copy, doc.xref_object(int(res.split()[0])))
                doc.xref_set_key(form, "Resources", f"{copy} 0 R")
            own = _resources(doc, form)
            if own:
                visit(own)

    for page in doc:
        res = _resources(doc, page.xref)
        if res:
            visit(res)


def _string_end(data: bytes, i: int) -> int | None:
    """End of the string token starting at data[i]: (literal), with nesting and escapes, or <hex>."""
    if data[i:i + 1] == b"<" and data[i + 1:i + 2] != b"<":
        end = data.find(b">", i)
        return None if end < 0 else end + 1
    if data[i:i + 1] != b"(":
        return None
    depth, j = 0, i
    while j < len(data):
        c = data[j:j + 1]
        if c == b"\\":
            j += 2
            continue
        if c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return None


def strip_replacement_strings(data: bytes) -> bytes:
    """Remove /ActualText, /Alt and /E entries (with their string values) from a content stream."""
    out, pos = [], 0
    for m in REPLACEMENT_RE.finditer(data):
        if m.start() < pos:
            continue
        end = _string_end(data, m.end())
        if end is None:
            continue
        out.append(data[pos:m.start()])
        pos = end
    return b"".join(out) + data[pos:] if out else data


def _strip_replacement_text(doc: pymupdf.Document) -> None:
    contents = {x for page in doc for x in page.get_contents()}
    for xref in range(1, doc.xref_length()):
        try:
            keys = doc.xref_get_keys(xref)
        except Exception:  # a free or broken object
            continue
        for key in REPLACEMENT_KEYS:  # property lists and structure elements
            if key in keys and doc.xref_get_key(xref, key)[0] == "string":
                doc.xref_set_key(xref, key, "null")
        if xref in contents or doc.xref_is_stream(xref) and doc.xref_get_key(xref, "Subtype")[1] == "/Form":
            contents.add(xref)
    for xref in contents:
        data = doc.xref_stream(xref)
        if data and b"BDC" in data:
            cleaned = strip_replacement_strings(data)
            if cleaned != data:
                doc.update_stream(xref, cleaned)


def outside(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Anything drawn (text, images, paths) that reaches past the visible page."""
    visible = page.rect + (-EDGE, -EDGE, EDGE, EDGE)
    return [pymupdf.Rect(r) for _, r in page.get_bboxlog()
            if not pymupdf.Rect(r).is_empty and pymupdf.Rect(r) not in visible]


def clip_to_visible(page: pymupdf.Page) -> bool:
    """Remove everything outside the visible area (CropBox) and make that area the whole
    page. Page coordinates of what stays are unchanged. True if anything was removed."""
    far = outside(page)
    if not far:
        return False
    doc = page.parent
    rotation = page.rotation
    page.set_rotation(0)
    to_pdf = ~page.transformation_matrix
    visible = page.rect * to_pdf
    big = pymupdf.Rect(visible)
    for r in far:
        big |= r * to_pdf
    big += (-10, -10, 10, 10)
    box = f"[{big.x0:g} {big.y0:g} {big.x1:g} {big.y1:g}]"
    for key in ("MediaBox", "CropBox"):
        doc.xref_set_key(page.xref, key, box)
    for key in ("TrimBox", "BleedBox", "ArtBox"):
        doc.xref_set_key(page.xref, key, "null")
    inside = visible * page.transformation_matrix
    whole = page.rect
    for band in (pymupdf.Rect(whole.x0, whole.y0, whole.x1, inside.y0),
                 pymupdf.Rect(whole.x0, inside.y1, whole.x1, whole.y1),
                 pymupdf.Rect(whole.x0, inside.y0, inside.x0, inside.y1),
                 pymupdf.Rect(inside.x1, inside.y0, whole.x1, inside.y1)):
        if not band.is_empty:
            page.add_redact_annot(band)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    box = f"[{visible.x0:g} {visible.y0:g} {visible.x1:g} {visible.y1:g}]"
    for key in ("MediaBox", "CropBox"):
        doc.xref_set_key(page.xref, key, box)
    page.set_rotation(rotation)
    return True
