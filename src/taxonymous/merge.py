"""Merge a folder's passed documents into one PDF (or a few, under a size limit) for an
upload that takes only so many files.

Each file starts with a contents page listing its documents by their public names (your
details taken out, as on export) and the pages each one is on. No bookmarks: an outline is
text that isn't on any page, which verification refuses in every file. Pages are copied as
they are, without links or annotations, and the file gets no metadata.

Every document in it passed its own check; the merged file is checked again,
for your details, SSN shapes and barcodes, with what you left readable in each document
still readable on its pages. Only a file that passes goes to ready-to-upload/.

The last merge of a folder is recorded in work/<folder>/.merge.json (a name no document's
outputs can have).
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .detect import ProfileMatcher
from .pipeline import Workspace, readable
from .progress import NONE, Progress
from .redact import Overrides
from .sanitize import allow_list
from .vault import Vault
from .verify import Shown, verify

PAGE = pymupdf.paper_rect("letter")
MARGIN = 72
ROWS = 40  # contents lines per page
NOTE = "Personal details in these documents were removed on purpose before upload."


@dataclass
class Part:
    stems: list[str]
    file: Path | None = None  # in ready-to-upload/, once it passed
    pages: int = 0
    issues: list[dict] = field(default_factory=list)


def record_path(ws: Workspace, folder: str) -> Path:
    return ws.work / folder / ".merge.json"


def last(ws: Workspace, folder: str) -> dict | None:
    path = record_path(ws, folder)
    return json.loads(path.read_text()) if path.exists() else None


def in_folder(ws: Workspace, folder: str) -> list[str]:
    """Every document in a folder and the folders inside it ("" is all of inbox/), in the
    sidebar's order: at each level the folders first, then the files."""
    stems = [ws.stem(p) for p in ws.pending()]
    stems = [s for s in stems if not folder or s.startswith(folder + "/")]

    def place(stem: str) -> list[tuple[int, str]]:
        *dirs, name = stem.lower().split("/")
        return [(0, d) for d in dirs] + [(1, name)]
    return sorted(stems, key=place)


def passed(ws: Workspace, stem: str) -> bool:
    report = ws.outputs(stem)["report"]
    return report.exists() and bool(json.loads(report.read_text()).get("passed"))


def title(ws: Workspace, folder: str, stem: str) -> str:
    """A document's name in the contents and bookmarks: its path below the folder, public."""
    rel = stem[len(folder) + 1:] if folder else stem
    return " / ".join(ws.public_name(part) for part in rel.split("/"))


def merge(ws: Workspace, folder: str, matcher: ProfileMatcher, vault: Vault, max_mb: float | None = None,
          ocr: bool = True, progress: Progress = NONE) -> dict:
    """Merge the folder's passed documents; returns (and records) what happened."""
    stems = in_folder(ws, folder)
    included = [s for s in stems if passed(ws, s)]
    skipped = [s for s in stems if s not in included]
    old = last(ws, folder)
    for rel in (old or {}).get("files", []):  # a merge replaces the one before
        (ws.ready / rel).unlink(missing_ok=True)

    limit = int(max_mb * 1_000_000) if max_mb else None
    parts = _pack(ws, included, limit)
    base = ws.public_name(folder.rsplit("/", 1)[-1]) if folder else "documents"
    dest_dir = ws.ready.joinpath(*(ws.public_name(p) for p in folder.split("/"))) if folder else ws.ready
    build_dir = ws.work / folder
    build_dir.mkdir(parents=True, exist_ok=True)
    too_big = []
    for i, part in enumerate(parts, 1):
        built = build_dir / f".merge-{i}.pdf"
        _build(ws, folder, part, built, i, len(parts))
        if limit and built.stat().st_size > limit:
            too_big.append(i)
        report = _check(ws, part, built, matcher, vault, ocr, progress)
        part.issues = [issue.__dict__ for issue in report.issues]
        if report.passed:
            name = f"{base} merged" + (f" (part {i} of {len(parts)})" if len(parts) > 1 else "") + ".pdf"
            dest_dir.mkdir(parents=True, exist_ok=True)
            part.file = dest_dir / name
            part.file.write_bytes(built.read_bytes())
        built.unlink()

    ok = bool(parts) and all(p.file for p in parts)
    if not ok:  # all or nothing: a missing part would leave documents out of the upload
        for p in parts:
            if p.file:
                p.file.unlink(missing_ok=True)
                p.file = None
    record = {
        "folder": folder,
        "passed": ok,
        "files": [p.file.relative_to(ws.ready).as_posix() for p in parts if p.file],
        "parts": [{"documents": p.stems, "pages": p.pages, "issues": p.issues} for p in parts],
        "skipped": skipped,
        "max_mb": max_mb,
        "too_big": too_big,  # parts over the limit: one document that is bigger on its own
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    record_path(ws, folder).write_text(json.dumps(record, indent=2) + "\n")
    return record


def _pack(ws: Workspace, stems: list[str], limit: int | None) -> list[Part]:
    """Documents in order, a new part whenever the next would take this one over the limit.
    A document is never split; one bigger than the limit gets a part of its own."""
    if not stems:
        return []
    if not limit:
        return [Part(stems)]
    parts: list[Part] = []
    size = 0
    for stem in stems:
        n = ws.outputs(stem)["pdf"].stat().st_size
        if parts and size + n <= limit:
            parts[-1].stems.append(stem)
            size += n
        else:
            parts.append(Part([stem]))
            size = 20_000 + n  # the contents page and the file's own structure
    return parts


def _build(ws: Workspace, folder: str, part: Part, path: Path, number: int, total: int) -> None:
    docs = [(title(ws, folder, s), pymupdf.open(ws.outputs(s)["pdf"])) for s in part.stems]
    contents_pages = max(1, -(-len(docs) // ROWS))
    out = pymupdf.open()
    starts, at = [], contents_pages + 1
    for name, doc in docs:
        starts.append((name, at, at + doc.page_count - 1))
        at += doc.page_count
    _contents(out, starts, contents_pages, number, total)
    for _, doc in docs:
        out.insert_pdf(doc, links=False, annots=False, widgets=False)
        doc.close()
    out.set_metadata({})
    out.del_xml_metadata()
    allow_list(out)
    part.pages = out.page_count
    out.save(path, garbage=4, deflate=True, clean=True, no_new_id=True)
    out.close()


def _contents(out: pymupdf.Document, starts: list[tuple[str, int, int]], pages: int, number: int,
              total: int) -> None:
    width = PAGE.width - 2 * MARGIN
    for n in range(pages):
        page = out.new_page(width=PAGE.width, height=PAGE.height)
        y = MARGIN
        if n == 0:
            head = "Contents" + (f" (part {number} of {total})" if total > 1 else "")
            page.insert_text((MARGIN, y), head, fontname="helv", fontsize=16)
            y += 22
            page.insert_text((MARGIN, y), NOTE, fontname="helv", fontsize=9, color=(0.35, 0.35, 0.35))
            y += 26
        for name, first, final in starts[n * ROWS:(n + 1) * ROWS]:
            where = f"page {first}" if first == final else f"pages {first}-{final}"
            room = width - pymupdf.get_text_length(where, "helv", 10) - 16
            page.insert_text((MARGIN, y), _fit(name, room), fontname="helv", fontsize=10)
            page.insert_text((MARGIN + width - pymupdf.get_text_length(where, "helv", 10), y), where,
                             fontname="helv", fontsize=10)
            y += 14


def _fit(text: str, width: float) -> str:
    """Cut to fit, in characters the built-in font has (it has no en dash or ellipsis)."""
    if pymupdf.get_text_length(text, "helv", 10) <= width:
        return text
    while text and pymupdf.get_text_length(text + "...", "helv", 10) > width:
        text = text[:-1]
    return text + "..."


def _check(ws: Workspace, part: Part, path: Path, matcher: ProfileMatcher, vault: Vault, ocr: bool,
           progress: Progress):
    """Your details, SSN shapes and barcodes, anywhere in the file; what you left readable
    in a document stays allowed on its pages, a single box at its place there."""
    allowed: set[str] = set()
    keys: list[str] = []
    shown: list[Shown] = []
    at = max(1, -(-len(part.stems) // ROWS))  # 0-based page each document starts on
    for stem in part.stems:
        a, k, s = readable(ws, stem, vault, Overrides.load(ws.overrides_path(stem)))
        allowed |= a
        keys += k
        shown += [Shown(x.page + at, x.rect, x.values, x.key, x.more) for x in s]
        with pymupdf.open(ws.outputs(stem)["pdf"]) as doc:
            at += doc.page_count
    return verify(path, matcher, [], ocr=ocr, allowed=allowed, shown=shown, progress=progress,
                  allowed_keys=keys)
