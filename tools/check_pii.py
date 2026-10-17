"""Pre-commit guard: refuse to commit anything that looks like real PII.

Checks staged files (or the paths given on the command line):
  - SSN-shaped strings outside the SSA advertising block 987-65-4320..4329
  - EIN-shaped strings without the never-issued 00- prefix
  - documents/images outside tests/fixtures/, and any profile.json
PDF text (including form-field values) is extracted before checking.

Usage:
  uv run python -m tools.check_pii            # staged files (pre-commit hook)
  uv run python -m tools.check_pii FILE...    # specific files
"""

import re
import subprocess
import sys
from pathlib import Path

SSN_RE = re.compile(r"(?<![\d-])(\d{3})[- ](\d{2})[- ](\d{4})(?![\d-])")
EIN_RE = re.compile(r"(?<![\d-])(\d{2})-(\d{7})(?![\d-])")
DOC_EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
FIXTURE_DIR = "tests/fixtures/"


def ssn_allowed(area: str, group: str, serial: str) -> bool:
    return area == "987" and group == "65" and 4320 <= int(serial) <= 4329


def text_of(path: str, data: bytes) -> str:
    if path.lower().endswith(".pdf"):
        import pymupdf

        doc = pymupdf.open(stream=data, filetype="pdf")
        parts = [page.get_text() for page in doc]
        parts += [w.field_value or "" for page in doc for w in page.widgets()]
        return "\n".join(str(p) for p in parts)
    return data.decode("utf-8", errors="ignore")


def problems(path: str, data: bytes) -> list[str]:
    found = []
    p = Path(path)
    if p.suffix.lower() in DOC_EXTS and not path.startswith(FIXTURE_DIR):
        found.append(f"{path}: document/image outside {FIXTURE_DIR}")
    if p.name == "profile.json" or p.name.endswith(".profile.json"):
        found.append(f"{path}: profile files must never be committed")
    if p.suffix.lower() in DOC_EXTS - {".pdf"}:
        return found  # can't read text from images; location check above is the guard
    text = text_of(path, data)
    for m in SSN_RE.finditer(text):
        if not ssn_allowed(*m.groups()):
            found.append(f"{path}: SSN-like value {m.group(1)}-**-**** (not in reserved fake range)")
    for m in EIN_RE.finditer(text):
        if m.group(1) != "00":
            found.append(f"{path}: EIN-like value {m.group(1)}-******* (fake EINs must start with 00-)")
    return found


def staged() -> list[tuple[str, bytes]]:
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
                           check=True, capture_output=True).stdout.decode().split("\0")
    return [(n, subprocess.run(["git", "show", f":{n}"], check=True, capture_output=True).stdout)
            for n in names if n]


def main(argv: list[str]) -> int:
    files = [(a, Path(a).read_bytes()) for a in argv] if argv else staged()
    found = [msg for path, data in files for msg in problems(path, data)]
    for msg in found:
        print(f"check_pii: {msg}", file=sys.stderr)
    if found:
        print("check_pii: commit blocked. Use only fake values (see tools/fixtures/fake.py).",
              file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
