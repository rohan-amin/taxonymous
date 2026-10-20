"""How is a PDF built? Its structure, without any of its text.

    uv run python -m tools.pdf_shape FILE.pdf [FILE.pdf ...]
    uv run python -m tools.pdf_shape --ops PAGE FILE.pdf [FILE.pdf ...]

--ops prints how one page (1-based PAGE) places its text: the text, position and
graphics-state operators, with coordinates as they are and every letter and digit inside
a text string masked (a/A, #). Lines, fills and colours are left out.

For comparing an original with its redacted copy when redaction didn't take: how the file
was saved (updates, repairs, encryption), what its catalog holds (forms, JavaScript), and
per page how its text is drawn (fields, form objects, invisible or off-page text). Prints
key names, counts and sizes only, never strings, so the output is safe to share.
"""

import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

EDGE = 1.0


def _keys(doc: pymupdf.Document, xref: int) -> list[str]:
    return [k for k in doc.xref_get_keys(xref) if doc.xref_get_key(xref, k)[0] != "null"]


def shape(path: Path, label: str) -> list[str]:
    raw = path.read_bytes()
    doc = pymupdf.open(path)
    out = [f"{label} · {doc.page_count} pages · PDF {raw[5:8].decode(errors='replace')} · "
           f"{raw.count(b'%%EOF')} × %%EOF · repaired: {doc.is_repaired} · encrypted: {doc.is_encrypted} · "
           f"{doc.xref_length()} objects"]
    catalog = doc.pdf_catalog()
    out.append(f"  catalog (object {catalog}): {', '.join(_keys(doc, catalog))}")
    catalogs, kinds = [], Counter()
    for xref in range(1, doc.xref_length()):
        try:
            kind = doc.xref_get_key(xref, "Type")[1]
            sub = doc.xref_get_key(xref, "S")[1]
        except Exception:
            continue
        if kind == "/Catalog":
            catalogs.append(xref)
        if sub in ("/JavaScript", "/Launch", "/SubmitForm", "/ImportData"):
            kinds[sub] += 1
    if catalogs != [catalog]:
        out.append(f"  objects of type Catalog: {catalogs}")
    if kinds:
        out.append("  actions: " + ", ".join(f"{n} × {k}" for k, n in kinds.items()))
    kind, form = doc.xref_get_key(catalog, "AcroForm")
    if kind != "null":
        xref = int(form.split()[0]) if kind == "xref" else None
        text = doc.xref_object(xref) if xref else form
        out.append(f"  form: {'XFA, ' if '/XFA' in text else ''}"
                   f"{'NeedAppearances, ' if re.search(r'/NeedAppearances\s+true', text) else ''}"
                   f"keys {', '.join(re.findall(r'/(\w+)', text)[:12])}")
    for page in doc:
        widgets = list(page.widgets())
        no_ap = sum(1 for w in widgets if doc.xref_get_key(w.xref, "AP")[0] == "null")
        annots = Counter(a.type[1] for a in page.annots())
        forms = [x for x in page.get_xobjects() if doc.xref_get_key(x[0], "Subtype")[1] == "/Form"]
        trace = page.get_texttrace()
        visible = page.rect + (-EDGE, -EDGE, EDGE, EDGE)
        how = Counter(t["type"] for t in trace)
        off = [t for t in trace if pymupdf.Rect(t["bbox"]) not in visible]
        far = max((max(visible.x0 - t["bbox"][0], visible.y0 - t["bbox"][1], t["bbox"][2] - visible.x1,
                       t["bbox"][3] - visible.y1) for t in off), default=0)
        opacity = Counter(round(t["opacity"], 2) for t in trace)
        sizes = Counter(round(t["size"]) for t in trace)
        out.append(f"  page {page.number + 1}: {page.rect.width:.0f} × {page.rect.height:.0f}, rotated "
                   f"{page.rotation}, {len(page.get_contents())} content stream(s), {len(forms)} form "
                   f"object(s), {len(page.get_images())} image(s)")
        if widgets or annots:
            out.append(f"    fields: {len(widgets)} ({no_ap} without appearance)"
                       + "".join(f" · {n} × {k} annotation(s)" for k, n in annots.items()))
        modes = ", ".join(f"{n} × {k}" for k, n in how.items())
        out.append(f"    text spans: {len(trace)} (render mode: {modes}) · "
                   f"opacity {dict(opacity)} · sizes {dict(sorted(sizes.items())[:8])}")
        if off:
            out.append(f"    {len(off)} span(s) past the edge, up to {far:.0f} pt; their sizes "
                       f"{sorted({round(t['size']) for t in off})[:8]}, "
                       f"render modes {sorted({t['type'] for t in off})}")
        boxes = ", ".join(f"{k} {doc.xref_get_key(page.xref, k)[1]}" for k in ("MediaBox", "CropBox")
                          if doc.xref_get_key(page.xref, k)[0] != "null")
        out.append(f"    {boxes}; page keys: {', '.join(_keys(doc, page.xref))}")
    return out


TOKEN_RE = re.compile(rb"""
    (?P<str>\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\))   # (string), one level of nesting
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<other><<|>>|\[|\]|/[^\s/<>\[\]()%]*|[^\s/<>\[\]()%]+)
  | (?P<comment>%[^\r\n]*)
""", re.X | re.S)


def _masked(token: bytes) -> str:
    text = token.decode("latin-1")
    return re.sub(r"\d", "#", re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", text)))


TEXT_OPS = {"q", "Q", "cm", "BT", "ET", "Tf", "Td", "TD", "Tm", "T*", "TL", "Tc", "Tw", "Tz", "Ts", "Tr",
            "Tj", "TJ", "'", '"', "Do", "ID"}


def ops(data: bytes) -> list[str]:
    """A content stream as lines of operands and operator, strings masked."""
    lines, operands = [], []
    pos = 0
    while (m := TOKEN_RE.search(data, pos)):
        pos = m.end()
        if m.group("str") or m.group("hex"):
            operands.append(_masked(m.group()) if m.group("str") else f"<{len(m.group()) // 2} bytes>")
        elif m.group("other"):
            t = m.group().decode("latin-1")
            if re.fullmatch(r"[A-Za-z'\"*]+\d?\*?", t) and t not in ("true", "false", "null"):
                if t in TEXT_OPS:
                    lines.append(" ".join([*operands, t]))
                operands = []
                if t == "ID":  # inline image data up to EI
                    end = data.find(b"EI", pos)
                    pos = len(data) if end < 0 else end + 2
                    lines.append("<inline image> EI")
            else:
                operands.append(t)
    return lines


def page_ops(path: Path, number: int) -> list[str]:
    doc = pymupdf.open(path)
    page = doc[number - 1]
    out = []
    for i, xref in enumerate(page.get_contents()):
        out.append(f"--- content stream {i + 1} of {len(page.get_contents())} (object {xref})")
        out += ops(doc.xref_stream(xref) or b"")
    return out


if __name__ == "__main__":
    if sys.argv[1] == "--ops":
        for i, arg in enumerate(sys.argv[3:]):
            print(f"=== file {i + 1}, page {sys.argv[2]}")
            print("\n".join(page_ops(Path(arg), int(sys.argv[2]))))
        sys.exit()
    for i, arg in enumerate(sys.argv[1:]):  # not by name: a file name can hold yours
        print("\n".join(shape(Path(arg), f"file {i + 1}")))
        print()
