"""A cleaning engine in the browser: one of the app's lanes (taxonymous.lanes), in a worker
of its own. It's given a job with the files it reads (the profile, the document, the boxes
drawn on it, and its outputs when it's checked again), cleans or checks it in a workspace
of its own (memory), and gives back the document's outputs. The vault is the page's,
lent under its lock (bridge.py), so labels are the same whichever engine names them.
"""

import json
import shutil
from pathlib import Path

import bridge  # noqa: F401  (OCR, barcodes and the vault's lock, before the engine loads)

from taxonymous import lanes
from taxonymous.pipeline import Workspace

ROOT = Path("/lane")


def run(job_json: str, files) -> dict:
    """`files`: {path in the workspace: bytes}. Returns {"outputs": {name: bytes | None},
    "error": str | None}, progress going to the page as it goes."""
    from js import Object, postMessage
    from pyodide.ffi import to_js

    job = json.loads(job_json)
    shutil.rmtree(ROOT, ignore_errors=True)
    ws = Workspace(ROOT)
    ws.init()
    for rel, data in files.to_py().items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(data))

    def send(msg: dict) -> None:
        if "progress" in msg:
            postMessage(to_js({"type": "progress", "id": job["id"], "progress": json.dumps(msg["progress"])},
                              dict_converter=Object.fromEntries))

    error = None
    try:
        lanes.run_job(ws, job["action"], job["stem"], job["forget"], True, lanes._Reporting(send))
    except Exception as e:  # reported on the document, as the app's own worker does
        error = f"{type(e).__name__}: {str(e)[:300]}"
    outputs = {name: (p.read_bytes() if p.exists() else None) for name, p in ws.outputs(job["stem"]).items()}
    return {"outputs": outputs, "error": error}
