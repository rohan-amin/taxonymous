"""What the engine needs from the page, in the browser: OCR (Tesseract and PaddleOCR, on
pools of workers), barcodes (zxing-wasm) and the vault's lock. The engine calls them as
ordinary functions; each is a synchronous call to the page (worker.js syncCall, bridge.js),
which does the work and wakes the engine with the answer.

Imported first, by home.py and lane.py: it puts these in place before the engine loads.
"""

import io
import json
import sys
import types
from pathlib import Path


def call(kind: str, payload: dict, blobs: list[bytes] = ()):
    """A synchronous call to the page."""
    from js import syncCall
    from pyodide.ffi import to_js
    return json.loads(syncCall(kind, json.dumps(payload), to_js(list(blobs))))


# ---- the vault's lock: the page holds the vault (the open Taxonymous file's) and lends it to
# one engine at a time, taking it back with whatever that engine named; as the app's lanes
# share the vault file. vault.py locks ".placeholders.json.lock" beside the file it guards.

def _flock(lock_file, op: int) -> None:
    name = Path(lock_file.name)
    vault = name.with_name(name.name.removeprefix(".").removesuffix(".lock"))
    if op == 2:
        vault.parent.mkdir(parents=True, exist_ok=True)
        vault.write_text(call("vault_lock", {}) or '{"placeholders": {}}')
    elif op == 8:
        call("vault_unlock", {"vault": vault.read_text() if vault.exists() else ""})


sys.modules["fcntl"] = types.SimpleNamespace(flock=_flock, LOCK_EX=2, LOCK_UN=8)


# ---- barcodes: zxingcpp as the engine calls it, read by zxing-wasm (the same ZXing C++)

BARCODES = ["PDF417", "QRCode", "MicroQRCode", "rMQRCode", "DataMatrix", "Aztec", "MaxiCode", "Code128",
            "Code39", "Code93", "ITF"]  # barcode.py's formats, as zxing-wasm names them


class _Barcodes(types.ModuleType):
    def __getattr__(self, name):  # the format constants: the reader is told its own list
        return name

    @staticmethod
    def read_barcodes(img, formats=None):
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=1)
        found = call("barcodes", {"formats": BARCODES}, [buf.getvalue()])
        point = lambda p: types.SimpleNamespace(x=p["x"], y=p["y"])  # noqa: E731
        return [types.SimpleNamespace(position=types.SimpleNamespace(
            top_left=point(p["topLeft"]), top_right=point(p["topRight"]),
            bottom_left=point(p["bottomLeft"]), bottom_right=point(p["bottomRight"]))) for p in found]


sys.modules["zxingcpp"] = _Barcodes("zxingcpp")


# ---- OCR: every (picture, psm) read on the page; psm "vision" is the second engine's read
# (PaddleOCR, where the Mac has Vision), handled by the engine exactly as Vision's

from taxonymous import ocr  # noqa: E402


def _run_all(jobs, tsv=False, done=None):
    jobs = list(jobs)
    out = call("ocr", {"psms": [psm for _, psm in jobs], "tsv": tsv}, [pic.data for pic, _ in jobs])
    if done is not None:
        for i in range(len(jobs)):
            done(i)
    return out


def _read(pic, psm, tsv=False):
    return _run_all([(pic, psm)], tsv)[0]


ocr.run_all, ocr.read, ocr.vision_on, ocr.require = _run_all, _read, (lambda: True), (lambda: None)


# ---- the vault, opened: read from the page first (another engine may have named labels since
# this one last had it), as the app's lanes read the shared file

from taxonymous import vault as vault_mod  # noqa: E402

_open = vault_mod.Vault.__init__


def _fresh(self, path=None):
    if path is not None and Path(path).name == "placeholders.json":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        text = call("vault_read", {})
        if text:
            Path(path).write_text(text)
    _open(self, path)


vault_mod.Vault.__init__ = _fresh
