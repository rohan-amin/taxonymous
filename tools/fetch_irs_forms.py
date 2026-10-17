"""Download blank IRS fillable forms into tests/fixtures/irs_blank/.

Only pages that contain form fields are kept (the instruction pages are
dropped) to keep the repo small. IRS forms are US government works and are
in the public domain.

Usage: uv run python -m tools.fetch_irs_forms
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

import pymupdf

BASE = "https://www.irs.gov/pub/irs-pdf/"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "irs_blank"

# local name -> candidate file names on irs.gov (first that works wins)
FORMS = {
    "fw2": ["fw2.pdf"],
    "f1099int": ["f1099int.pdf"],
    "f1099div": ["f1099div.pdf"],
    "f1099b": ["f1099b.pdf", "f1099b--2026.pdf", "f1099b--2025.pdf"],
    "f1099msc": ["f1099msc.pdf"],
    "f1099nec": ["f1099nec.pdf"],
    "f1099r": ["f1099r.pdf"],
    "f1099g": ["f1099g.pdf"],
    "f1065sk1": ["f1065sk1.pdf"],
    "f1120ssk": ["f1120ssk.pdf"],
    "f1041sk1": ["f1041sk1.pdf"],
    "f1040sh": ["f1040sh.pdf"],
    "f5498": ["f5498.pdf"],
}


def fetch(candidates: list[str]) -> tuple[str, bytes]:
    for name in candidates:
        try:
            with urllib.request.urlopen(BASE + name, timeout=60) as resp:
                return name, resp.read()
        except OSError:
            continue
    raise RuntimeError(f"none of {candidates} could be downloaded")


def trim(data: bytes) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    keep = [p.number for p in doc if p.first_widget is not None]
    doc.select(keep)
    doc.set_metadata({})
    return doc.tobytes(garbage=4, deflate=True, no_new_id=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# Blank IRS forms (public domain)", "", "| File | Source | SHA-256 |", "|---|---|---|"]
    for local, candidates in FORMS.items():
        src, data = fetch(candidates)
        out = trim(data)
        (OUT / f"{local}.pdf").write_bytes(out)
        digest = hashlib.sha256(out).hexdigest()[:16]
        lines.append(f"| {local}.pdf | {BASE}{src} | `{digest}…` |")
        print(f"{local}: {len(out):,} bytes from {src}")
    (OUT / "SOURCES.md").write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
