"""The engine's entry point in the browser worker: clean and check one PDF with a profile.

OCR is not in this spike: a page with pictures (or a scan) has its pictures counted and
reported, never passed as checked.
"""

import json
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, "/engine")
try:
    import fcntl  # noqa: F401
except ImportError:  # one tab: nothing to lock against
    sys.modules["fcntl"] = types.SimpleNamespace(flock=lambda *a: None, LOCK_EX=2, LOCK_UN=8)


class _NoBarcodes(types.ModuleType):  # zxing-wasm in the real thing
    def __getattr__(self, name):
        return 0


sys.modules["zxingcpp"] = _NoBarcodes("zxingcpp")
sys.modules["zxingcpp"].read_barcodes = lambda *a, **k: []

from taxonymous import ocr  # noqa: E402

unread = {"images": 0}


def _no_ocr(jobs, tsv=False, done=None):
    jobs = list(jobs)
    unread["images"] += len(jobs)
    return ["level\tpage_num\n" if tsv else "" for _ in jobs]


ocr.run_all, ocr.vision_on, ocr.require = _no_ocr, (lambda: False), (lambda: None)

from taxonymous.detect import ProfileMatcher  # noqa: E402
from taxonymous.profile import Profile, clean, with_ids  # noqa: E402
from taxonymous.redact import Overrides, redact_file  # noqa: E402
from taxonymous.vault import Vault  # noqa: E402
from taxonymous.verify import Known, verify  # noqa: E402

WORK = Path(os.environ.get("TAXONYMOUS_SPIKE_WORK", "/work"))  # the browser's memory; a folder natively
VAULT = WORK / "vault.json"


def run(profile_json: str, vault_json: str, name: str, pdf: bytes, fingerprint: str = "",
        decisions_json: str = "") -> dict:
    """One document: its safe copy, its text, its labels, what was checked, and the vault
    after it (the labels and what they stand for: saved in the Taxonymous file, never here).
    `decisions_json`: the boxes the reviewer drew on it ({"add": [{"page", "rect"}]}), kept
    in the Taxonymous file under the document's fingerprint (SHA-256 of its bytes), which
    also names it to the engine: the same file gets the same boxes, whatever it's called."""
    # a profile without permanent ids gets them as the app gives them (each entry its place),
    # so every document in the session sees the same people: one label each
    profile, errors = clean(with_ids(json.loads(profile_json))[0])
    if errors:
        return {"error": "The profile has details to fix: " + ", ".join(sorted(errors))}
    work = WORK / str(time.monotonic_ns())
    work.mkdir(parents=True)
    src = work / name
    src.write_bytes(pdf)
    unread["images"] = 0
    matcher = ProfileMatcher(Profile.from_dict(profile))
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    VAULT.write_text(vault_json or '{"placeholders": {}}')  # from the open file: labels as before
    vault = Vault(VAULT)
    decisions = json.loads(decisions_json or "{}")
    overrides = Overrides(add=[(int(b["page"]), tuple(b["rect"])) for b in decisions.get("add", [])])
    t0 = time.perf_counter()
    result = redact_file(src, work / "out", matcher, vault, overrides, stem=f"doc:{fingerprint[:16]}")
    t1 = time.perf_counter()
    # checked as the app checks: your details, SSN shapes, and everything removed here (what
    # was under a box you drew too: it isn't in your profile, so it's looked for by value)
    known = [Known(f.kind, f.text) for f in result.findings]
    here = {r.placeholder for r in result.regions} & vault.entries.keys()
    known += [Known(vault.entries[ph]["kind"], v) for ph in here for v in vault.entries[ph]["values"]]
    report = verify(result.pdf, matcher, known, txt=result.text, ocr=False)
    t2 = time.perf_counter()
    return {
        "name": name,
        "pdf": result.pdf.read_bytes(),
        "text": result.text.read_text(),
        "labels": sorted(result.placeholders),
        "clean_s": round(t1 - t0, 2),
        "check_s": round(t2 - t1, 2),
        "passed": report.passed,
        "issues": [f"{i.kind} ({i.check}, page {i.page})" for i in report.issues],
        "pictures_unread": unread["images"],
        "vault": VAULT.read_text(),
    }


def pages(pdf: bytes, dpi: int = 96) -> list[dict]:
    """Each page as a PNG, with its size in points: for the review view."""
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return [{"png": p.get_pixmap(dpi=dpi, alpha=False).tobytes("png"), "w": p.rect.width, "h": p.rect.height}
            for p in doc]
