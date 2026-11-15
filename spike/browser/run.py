"""The engine's entry point in the browser worker: clean and check one PDF with a profile.

OCR runs on the page: Tesseract (tesseract.js, on a pool of workers) and barcodes (zxing-wasm),
reached through a synchronous bridge (worker.js, bridge.js). The second OCR engine, where the
Mac has Vision, is PaddleOCR v5 (paddle-worker.js).
"""

import hashlib
import json
import os
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, "/engine")


def _flock(_file, op: int) -> None:
    """The vault's lock, as several engines on the page share it: the page holds the vault
    (the open Taxonymous file's), lends it to one engine at a time, and takes it back with
    whatever that engine named. As the app's lanes share the vault file."""
    if op == 2:
        VAULT.parent.mkdir(parents=True, exist_ok=True)
        VAULT.write_text(_call("vault_lock", {}) or '{"placeholders": {}}')
    elif op == 8:
        _call("vault_unlock", {"vault": VAULT.read_text() if VAULT.exists() else ""})


if sys.platform == "emscripten":  # in the browser (natively, the real lock)
    sys.modules["fcntl"] = types.SimpleNamespace(flock=_flock, LOCK_EX=2, LOCK_UN=8)


def _call(kind: str, payload: dict, blobs: list[bytes] = ()):
    """A synchronous call to the page (worker.js syncCall, bridge.js): OCR or barcodes."""
    from js import syncCall
    from pyodide.ffi import to_js
    return json.loads(syncCall(kind, json.dumps(payload), to_js(list(blobs))))


# barcode.py's formats, as zxing-wasm names them
BARCODES = ["PDF417", "QRCode", "MicroQRCode", "rMQRCode", "DataMatrix", "Aztec", "MaxiCode", "Code128",
            "Code39", "Code93", "ITF"]


class _Barcodes(types.ModuleType):
    """zxingcpp, as the engine calls it, read by zxing-wasm (the same ZXing C++) on the page."""

    def __getattr__(self, name):  # the format constants: the reader is told its own list
        return name

    @staticmethod
    def read_barcodes(img, formats=None):
        import io
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        found = _call("barcodes", {"formats": BARCODES}, [buf.getvalue()])
        point = lambda p: types.SimpleNamespace(x=p["x"], y=p["y"])  # noqa: E731
        return [types.SimpleNamespace(position=types.SimpleNamespace(
            top_left=point(p["topLeft"]), top_right=point(p["topRight"]),
            bottom_left=point(p["bottomLeft"]), bottom_right=point(p["bottomRight"]))) for p in found]


sys.modules["zxingcpp"] = _Barcodes("zxingcpp")

from taxonymous import ocr  # noqa: E402

reads = {"ocr": 0, "ocr_s": 0.0}  # pictures read by OCR for the document being cleaned, and the wait


def _ocr(jobs, tsv=False, done=None):
    """Every (picture, psm) read by Tesseract on the page's pool, in order."""
    jobs = list(jobs)
    reads["ocr"] += len(jobs)
    t = time.perf_counter()
    out = _call("ocr", {"psms": [psm for _, psm in jobs], "tsv": tsv}, [pic.data for pic, _ in jobs])
    reads["ocr_s"] += time.perf_counter() - t
    if done is not None:
        for i in range(len(jobs)):
            done(i)
    return out


# the second engine is on (Paddle on the page reads where the Mac's Vision does): its reads are
# psm "vision", and the engine handles them exactly as Vision's
ocr.run_all, ocr.vision_on, ocr.require = _ocr, (lambda: True), (lambda: None)

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
    reads["ocr"], reads["ocr_s"] = 0, 0.0
    matcher = ProfileMatcher(Profile.from_dict(profile))
    VAULT.parent.mkdir(parents=True, exist_ok=True)
    VAULT.write_text(vault_json or '{"placeholders": {}}')  # from the open file: labels as before
    vault = Vault(VAULT)
    fingerprint = fingerprint or hashlib.sha256(pdf).hexdigest()  # never relies on the page for it
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
    # pictures read again by OCR, as the app does
    report = verify(result.pdf, matcher, known, txt=result.text, ocr=True)
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
        "ocr_reads": reads["ocr"],
        "ocr_s": round(reads["ocr_s"], 2),
        "vault": VAULT.read_text(),
    }


def pages(pdf: bytes, dpi: int = 96) -> list[dict]:
    """Each page as a PNG, with its size in points: for the review view."""
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return [{"png": p.get_pixmap(dpi=dpi, alpha=False).tobytes("png"), "w": p.rect.width, "h": p.rect.height}
            for p in doc]
