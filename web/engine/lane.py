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


# ---- how fast this computer cleans: timed once, on two pages made here (a made-up person,
# a vault in memory: nothing of yours is read or touched). A page with text and a scanned
# page differ by ten times or more, so each is timed.

SAMPLE = {"people": [{"id": "e00000001", "name": "Pat Sample", "ssn": "987-65-4321", "dob": "03/14/1952",
                      "role": "taxpayer"}],
          "addresses": [{"id": "e00000002", "street": "12 Example Road", "city": "Springfield", "state": "MA",
                         "zip": "01101", "type": "home"}]}


def _sample_text() -> bytes:
    """A page about as full as a W-2 or a 1099: labels, amounts, and the made-up person."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    lines = ["Form W-2 Wage and Tax Statement 2025",
             "Copy B - To Be Filed With Employee's FEDERAL Tax Return",
             "a Employee's social security number 987-65-4321",
             "e Employee's first name and initial Last name", "Pat Sample",
             "f Employee's address and ZIP code", "12 Example Road", "Springfield, MA 01101"]
    labels = ["Wages, tips, other compensation", "Federal income tax withheld", "Social security wages",
              "Social security tax withheld", "Medicare wages and tips", "Medicare tax withheld",
              "Dependent care benefits", "Nonqualified plans", "State wages, tips, etc.", "State income tax"]
    for n in range(36):
        lines.append(f"{n + 1}  {labels[n % len(labels)]}  {(n * 7919) % 90000 + 1000:,}.{n * 37 % 100:02d}")
    for i, line in enumerate(lines):
        page.insert_text((48, 52 + i * 16), line, fontsize=9.5)
    return doc.tobytes()


def _sample_scan(text: bytes) -> bytes:
    """The same page as a scanner gives it: a picture, at 200 dpi."""
    import pymupdf
    pix = pymupdf.open("pdf", text)[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    return doc.tobytes()


def calibrate() -> str:
    """{"text": seconds, "picture": seconds}: one page of each, cleaned and checked as a
    document is. The scanned page runs first untimed: it starts every OCR reader (that
    happens once, and would count against whichever page came first)."""
    import time

    from taxonymous.detect import ProfileMatcher
    from taxonymous.pipeline import process
    from taxonymous.profile import Profile, clean
    from taxonymous.vault import Vault

    root = Path("/calibrate")
    shutil.rmtree(root, ignore_errors=True)
    ws = Workspace(root)
    ws.init()
    profile, _ = clean(SAMPLE)
    ws.profile_path.write_text(json.dumps(profile))
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    text = _sample_text()
    (ws.inbox / "text.pdf").write_bytes(text)
    (ws.inbox / "scan.pdf").write_bytes(_sample_scan(text))
    took = {}
    for name, kind in (("scan.pdf", None), ("text.pdf", "text"), ("scan.pdf", "picture")):
        started = time.monotonic()
        process(ws.inbox / name, ws, matcher, Vault(), ocr=True, release=False)
        if kind:
            took[kind] = round(time.monotonic() - started, 2)
    shutil.rmtree(root, ignore_errors=True)
    return json.dumps(took)
