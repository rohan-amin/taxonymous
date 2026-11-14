"""The engine's entry point in the browser worker: clean and check one PDF with a profile.

OCR is not in this spike: a page with pictures (or a scan) has its pictures counted and
reported, never passed as checked.
"""

import json
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
from taxonymous.redact import redact_file  # noqa: E402
from taxonymous.vault import Vault  # noqa: E402
from taxonymous.verify import verify  # noqa: E402


def run(profile_json: str, name: str, pdf: bytes) -> dict:
    """One document: its safe copy, its text, its labels, and what was checked."""
    # a profile without permanent ids gets them as the app gives them (each entry its place),
    # so every document in the session sees the same people: one label each
    profile, errors = clean(with_ids(json.loads(profile_json))[0])
    if errors:
        return {"error": "The profile has details to fix: " + ", ".join(sorted(errors))}
    work = Path("/work") / str(time.monotonic_ns())
    work.mkdir(parents=True)
    src = work / name
    src.write_bytes(pdf)
    unread["images"] = 0
    matcher = ProfileMatcher(Profile.from_dict(profile))
    vault = Vault(Path("/work/vault.json"))  # one vault for the session: labels shared across documents
    t0 = time.perf_counter()
    result = redact_file(src, work / "out", matcher, vault)
    t1 = time.perf_counter()
    report = verify(result.pdf, matcher, [], txt=result.text, ocr=False)
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
    }
