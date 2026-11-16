"""Local HTTP API for the review UI.

Binds to loopback only (`serve` refuses anything else), answers only requests
addressed to a loopback host name (so a web page can't reach it through DNS
rebinding), and only with the token printed at launch (so another site open in
the same browser can't drive it). It makes no outbound connections.

  GET    /api/profile                           your identifiers (vault/profile.json), as a form edits them
  PUT    /api/profile                           save them (validated; 422 names the fields to fix)
  POST   /api/profile/items                     {"kind": "person"|"address"|"account"|..., "item": {...},
                                                 "rerun": id | null} add one detail seen in a document
                                                (422 names the fields); with rerun, that document is
                                                cleaned again first, then every other cleaned one.
                                                A person can be the first detail (it is you)
  POST   /api/changes                           {"details": [{"kind", "item"}], "boxes": {id: [box]},
                                                 "first": id | null, "check": bool} what the reviewer
                                                collected, applied at once: the profile saved once, each
                                                document cleaned again once (`first` first, then those
                                                with boxes, then every cleaned one if details were
                                                added). With check, only says whether the details are
                                                fine (422 names the fields of the one that isn't)
  GET    /api/profile/found                    where each detail in your profile was removed:
                                                {"entries": {"person:0": {"total", "placeholders",
                                                "documents": [{"id", "name", "places", "pages"}]}}}
  GET    /api/documents                         every document in inbox/ and its folders, with its status
  POST   /api/documents                         upload a file into inbox/ (multipart "file", and
                                                "folder" to put it in one: "2024")
  GET    /api/documents/{id}                    status, report, boxes, reviewer decisions, page sizes
  DELETE /api/documents/{id}                    remove it from the workspace: its copy in inbox/, its
                                                cleaned files, your boxes on it, its saved safe copy
                                                (409 while it is being cleaned); never your original
  GET    /api/documents/{id}/pages/{n}/original.png | redacted.png   (?dpi=)
  GET    /api/documents/{id}/pages/{n}/words    the words on the original page, with their boxes
                                                (page points), read by OCR on a scan; kept in memory
  POST   /api/documents/{id}/pages/{n}/suggest  {"words": [i, ...]} what those words probably are:
                                                {"words", "text", "kind", "item", "note"} to add to your
                                                details (one word grows to the whole name or phone)
  GET    /api/documents/{id}/text               the redacted placeholder text
  POST   /api/run?folder=<F>                    redact + verify everything, or everything in one
                                                folder and the folders inside it (queued)
  POST   /api/documents/{id}/run                redact + verify one document (queued)
  GET    /api/status                            what the worker is doing
  POST   /api/documents/{id}/redactions         {"boxes": [{"page": n, "rect": [x0, y0, x1, y1]}]}
                                                add boxes (page points), one re-run for all of them
  DELETE /api/documents/{id}/redactions?placeholder=<P>   remove a box: a drawn one goes,
                                                a detected one is kept in the clear everywhere
                                                in this document
  DELETE /api/documents/{id}/kept?placeholder=<P>         undo a keep
  POST   /api/documents/{id}/shown              {"page": n, "rect": [...]} leave one detected box
                                                in the clear (the rect of a box in `regions`)
  DELETE /api/documents/{id}/shown/{i}          redact a shown box again (its index in `shown`)
  POST   /api/documents/{id}/verify             verify the current output again (queued)
  POST   /api/documents/{id}/export             copy to ready-to-upload/, only if it passed
  POST   /api/reveal                            show the safe copies folder (ready-to-upload/) in Finder
  POST   /api/merge?folder=<F>                  {"max_mb": n | null} merge the folder's passed documents
                                                into one PDF in ready-to-upload/ (or parts under n MB),
                                                checked again first (queued)
  GET    /api/merge?folder=<F>                  that merge: queued, running, or its last result

The review UI (ui/, built into ui/dist) is served at /. Its files hold no data,
so they need no token; every /api request does.

Every change to a document re-runs it. Document ids are its path in inbox/ as an
output stem (`w2` for w2.pdf, `receipt.jpg` for receipt.jpg, `2024/w2` for
2024/w2.pdf), so they can hold slashes. Real values never appear in any response
except the original page images and words, a suggestion for words you chose,
/api/profile, and the file and folder names you gave your documents.
"""

import hmac
import io
import json
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import pymupdf
from fastapi import Body, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import lanes as lanes_mod
from .detect import ProfileMatcher
from .merge import last as last_merge
from .merge import merge
from .pipeline import Workspace, export
from .profile import Profile, add_item, clean
from .profile import save as save_profile
from .progress import Progress
from .redact import IMAGE_SUFFIXES, SUFFIXES, Overrides, load_image, manual_key
from .suggest import grow, label_for, suggest
from .vault import Vault
from .words import page_words

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
TOKEN_HEADER = "X-Taxonymous-Token"
DEFAULT_PORT = 8765
MAX_UPLOAD = 200 * 2**20
DPI_RANGE = (36, 300)
UI_DIR = Path(__file__).resolve().parents[2] / "ui" / "dist"


def check_loopback(host: str) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError(f"refusing to listen on {host!r}: only 127.0.0.1, ::1 or localhost")


# ---------------------------------------------------------------- worker

@dataclass
class Job:
    stem: str
    action: str  # "process" | "verify" | "merge"
    forget: list[str] = field(default_factory=list)  # vault keys of boxes the reviewer removed
    folder: str = ""  # merge: the folder, and the size of each file
    max_mb: float | None = None


def merge_id(folder: str) -> str:
    """A merge's place in the worker's queue: no document's id starts with a dot."""
    return f".merge:{folder}"


class Worker:
    """Runs jobs in lanes (see `lanes`), as many at once as the computer has room for. A
    document is only ever in one lane at a time, and a merge runs alone, once everything
    queued before it is done (it joins their outputs); nothing queued after it starts first."""

    def __init__(self, ws: Workspace, ocr: bool = True, lanes: int | None = None):
        self.ws, self.ocr = ws, ocr
        self.lanes = lanes or lanes_mod.count()
        self.cv = threading.Condition()
        self.pending: list[Job] = []
        self.running: dict[str, Progress] = {}  # in the order they started
        self.errors: dict[str, str] = {}
        # the documents in the current batch (since the worker was last idle): one each, however
        # often it's queued; it's done when it's neither waiting nor being cleaned
        self.batch: set[str] = set()
        self.pages: dict[str, int] = {}  # each waiting or running job's pages, for the time left
        self.per_page: float | None = None  # seconds a page takes a lane on this computer, as measured
        self.unfinished = 0
        helpers = [None] if self.lanes == 1 else [lanes_mod.Lane(lanes_mod.ocr_share(self.lanes))
                                                  for _ in range(self.lanes)]
        for n, helper in enumerate(helpers):
            threading.Thread(target=self._loop, args=(helper,), daemon=True,
                             name=f"taxonymous-lane-{n}").start()

    def submit(self, job: Job) -> None:
        pages = _pages(self.ws, job.stem) if job.action != "merge" else 0
        with self.cv:
            if self.idle():
                self.batch = set()
            self.batch.add(job.stem)
            self.unfinished += 1
            self.pages[job.stem] = max(self.pages.get(job.stem, 0), pages)
            self.pending.append(job)
            self.cv.notify_all()

    def busy(self, stem: str) -> bool:
        with self.cv:
            return stem in self.running or any(j.stem == stem for j in self.pending)

    def waiting(self, stem: str) -> bool:
        """Queued and not started: queuing it again would only clean it twice. (One being
        cleaned may be queued again: it started with what is out of date now.)"""
        with self.cv:
            return any(j.stem == stem for j in self.pending)

    def state(self, stem: str) -> str | None:
        """Whether the job is "running" or "queued", or None."""
        with self.cv:
            if stem in self.running:
                return "running"
            return "queued" if any(j.stem == stem for j in self.pending) else None

    def status(self) -> dict:
        with self.cv:
            return {"running": list(self.running), "queued": [j.stem for j in self.pending],
                    "idle": self.idle(), "lanes": self.lanes}

    def progress_of(self, stem: str) -> dict | None:
        """A running job's stage, pages done, fraction and time left; its place in the batch
        ("2 of 5"), and the time the whole batch has left."""
        with self.cv:
            progress = self.running.get(stem)
            if progress is None:
                return None
            active = set(self.running) | {j.stem for j in self.pending}
            done, total = len(self.batch - active), len(self.batch)
            place = done + 1 + list(self.running).index(stem)
            left = self._batch_left()
        snap = progress.snapshot() or {"stage": None, "label": "Starting", "done": 0, "total": 0,
                                       "fraction": 0, "eta": None}
        return {**snap, "job": place, "jobs": total, "batch_eta": left}

    def _batch_left(self) -> int | None:
        """Seconds until everything submitted is done: the running jobs' own estimates, then
        the waiting pages at this computer's measured pace, shared between the lanes."""
        if self.per_page is None:
            return None
        running = [p.snapshot() for p in self.running.values()]
        if any(s is None or s["eta"] is None for s in running):
            return None
        now = max((s["eta"] for s in running), default=0)
        waiting = sum(self.pages.get(j.stem, 1) for j in self.pending) * self.per_page / self.lanes
        return round(now + waiting)

    def idle(self) -> bool:
        return not self.running and not self.pending

    def wait(self, timeout: float = 600) -> None:
        """Block until every submitted job is done (tests and scripts)."""
        with self.cv:
            if not self.cv.wait_for(lambda: self.unfinished == 0, timeout):
                raise TimeoutError("worker still busy")

    def _next(self) -> Job | None:
        if any(stem.startswith(".merge:") for stem in self.running):
            return None
        for i, job in enumerate(self.pending):
            if job.action == "merge":
                return self.pending.pop(0) if i == 0 and not self.running else None
            if job.stem not in self.running:
                return self.pending.pop(i)
        return None

    def _loop(self, helper: lanes_mod.Lane | None) -> None:
        while True:
            with self.cv:
                job = self.cv.wait_for(self._next)
                progress = Progress()
                self.running[job.stem] = progress
            started = time.monotonic()
            try:
                self._run(job, helper, progress)
                self.errors.pop(job.stem, None)
            except Exception as e:  # reported on the document, never stops the lane
                self.errors[job.stem] = f"{type(e).__name__}: {str(e)[:300]}"
            finally:
                with self.cv:
                    del self.running[job.stem]
                    pages = self.pages.pop(job.stem, 0) if not self.busy_unlocked(job.stem) else 0
                    if job.action == "process" and pages:
                        took = (time.monotonic() - started) / pages
                        self.per_page = took if self.per_page is None else 0.7 * self.per_page + 0.3 * took
                    self.unfinished -= 1
                    self.cv.notify_all()

    def busy_unlocked(self, stem: str) -> bool:
        return stem in self.running or any(j.stem == stem for j in self.pending)

    def _run(self, job: Job, helper: lanes_mod.Lane | None, progress: Progress) -> None:
        if job.action == "merge":
            if not self.ws.profile_path.exists():
                raise FileNotFoundError(f"no profile at {self.ws.profile_path}")
            matcher = ProfileMatcher(Profile.load(self.ws.profile_path))
            vault = Vault(self.ws.vault_dir / "placeholders.json")
            for key in job.forget:
                vault.forget(key)
            merge(self.ws, job.folder, matcher, vault, job.max_mb, ocr=self.ocr, progress=progress)
        elif helper is None:
            lanes_mod.run_job(self.ws, job.action, job.stem, job.forget, self.ocr, progress)
        else:
            helper.run(self.ws, job.action, job.stem, job.forget, self.ocr, progress.show)


def _pages(ws: Workspace, stem: str) -> int:
    src = ws.source(stem)
    if src is None or src.suffix.lower() != ".pdf":
        return 1
    try:
        with pymupdf.open(src) as doc:
            return doc.page_count
    except Exception:  # an unreadable file: its own job says so
        return 1


# ---------------------------------------------------------------- helpers

def _source(ws: Workspace, stem: str) -> Path:
    src = ws.source(stem)
    if src is None:
        raise HTTPException(404, f"no document {stem!r}")
    return src


def _folder(folder: str) -> str:
    """A folder inside inbox/ as the UI names it ("2024", "2024/brokerage"), checked."""
    parts = folder.replace("\\", "/").strip("/").split("/") if folder.strip("/") else []
    if any(not p or p.startswith(".") or p != p.strip() for p in parts):
        raise HTTPException(400, f"not a folder name: {folder!r}")
    return "/".join(parts)


def _read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _status(ws: Workspace, worker: Worker, stem: str) -> str:
    state = worker.state(stem)
    if state:
        return state
    if stem in worker.errors:
        return "error"
    report = _read_json(ws.outputs(stem)["report"], None)
    if report is None:
        return "new"
    if any(i["check"] == "error" for i in report["issues"]):
        return "error"
    return "passed" if report["passed"] else "failed"


# a vault key -> the profile entry it comes from: a person's SSN, birth date, job and
# employer are that person's ("ssn:e3fa91c2b" -> "person:e3fa91c2b"); a preparer's parts are
# the preparer's. An SSN-shaped number not in the profile is keyed by its own digits
# ("ssn:987654323") and is no entry's.
# An entry's id is a short number (from before ids: its place then) or e + 8 hex digits;
# phones and emails are known by their value; an account's routing number is the account's.
_ID = r"(\d{1,3}|e[0-9a-f]{8})"
_ENTRY = [(re.compile(rf"(?:person|ssn|dob|occupation|employer):{_ID}$"), "person:{}"),
          (re.compile(rf"preparer:{_ID}(?::.*)?$"), "preparer:{}"),
          (re.compile(rf"routing:{_ID}$"), "acct:{}"),
          (re.compile(rf"((?:addr|acct|card|ein|id):(?:{_ID[1:-1]}))$"), "{}"),
          (re.compile(r"((?:phone|email):.+)$"), "{}")]


def _entry(key: str) -> str | None:
    for rx, entry in _ENTRY:
        if m := rx.match(key):
            return entry.format(m.group(1))
    return None


def _found(ws: Workspace) -> dict:
    """Where each profile entry was removed, from the vault (placeholder -> its identity)
    and each document's boxes (placeholder per box). Nothing is read again."""
    vault = _read_json(ws.vault_dir / "placeholders.json", {}).get("placeholders", {})
    entry_of = {ph: _entry(e["key"]) for ph, e in vault.items()}
    out: dict[str, dict] = {}
    for src in ws.pending():
        stem = ws.stem(src)
        regions = _read_json(ws.outputs(stem)["regions"], [])
        per: dict[str, dict] = {}
        spots: dict[tuple, list[list[float]]] = {}  # (page, placeholder) -> its spots so far
        for r in regions:
            entry = entry_of.get(r["placeholder"])
            if entry is None or r.get("source") == "manual":
                continue
            e = out.setdefault(entry, {"total": 0, "placeholders": set(), "documents": []})
            d = per.setdefault(entry, {"id": stem, "name": src.name, "places": 0, "pages": set()})
            e["placeholders"].add(r["placeholder"])
            d["pages"].add(r["page"] + 1)
            # one spot however many boxes cover it (a value's words, several OCR reads)
            seen = spots.setdefault((r["page"], r["placeholder"]), [])
            x0, y0, x1, y1 = r["rect"]
            near = next((b for b in seen
                         if x0 <= b[2] + 4 and b[0] <= x1 + 4 and y0 <= b[3] + 2 and b[1] <= y1 + 2), None)
            if near:
                near[:] = [min(near[0], x0), min(near[1], y0), max(near[2], x1), max(near[3], y1)]
            else:
                seen.append([x0, y0, x1, y1])
                d["places"] += 1
        for entry, d in per.items():
            out[entry]["total"] += d["places"]
            out[entry]["documents"].append({**d, "pages": sorted(d["pages"])})
    for e in out.values():
        e["placeholders"] = sorted(e["placeholders"])
    return {"entries": out}


def _page_sizes(ws: Workspace, src: Path, stem: str) -> list[list[float]]:
    pdf = ws.outputs(stem)["pdf"]
    if pdf.exists():
        return [[p.rect.width, p.rect.height] for p in pymupdf.open(pdf)]
    try:
        if src.suffix.lower() in IMAGE_SUFFIXES:
            img = load_image(src)
            return [[img.width * 72 / 200, img.height * 72 / 200]]  # until OCR has sized it
        doc = pymupdf.open(src)
        return [] if doc.needs_pass else [[p.rect.width, p.rect.height] for p in doc]
    except Exception:  # unreadable: its status says why
        return []


def _summary(ws: Workspace, worker: Worker, src: Path) -> dict:
    stem = ws.stem(src)
    regions = _read_json(ws.outputs(stem)["regions"], [])
    report = _read_json(ws.outputs(stem)["report"], None)
    pdf = ws.outputs(stem)["pdf"]
    return {
        "id": stem,
        "name": src.name,
        "folder": src.parent.relative_to(ws.inbox).as_posix().removeprefix("."),  # "" for inbox/ itself
        "status": _status(ws, worker, stem),
        "version": pdf.stat().st_mtime_ns if pdf.exists() else 0,  # changes whenever the output does
        "exported": all(p.exists() for p in ws.released(stem)),
        "redactions": dict(Counter(r["kind"] for r in {r["placeholder"]: r for r in regions}.values())),
        "issues": report["issues"] if report else [],
        "error": worker.errors.get(stem),
        "progress": worker.progress_of(stem),
    }


def _png(page: pymupdf.Page, dpi: int) -> bytes:
    return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")


class Box(BaseModel):
    page: int
    rect: tuple[float, float, float, float]


class Boxes(BaseModel):
    boxes: list[Box]


class MergeOptions(BaseModel):
    max_mb: float | None = None  # split into files of at most this many MB


class Detail(BaseModel):
    kind: str  # one of profile.ITEMS
    item: dict[str, str | bool]  # the fields of one row of that list, as Your details has them
    rerun: str | None = None  # the document it was seen in: cleaned again first, then the others


class Change(BaseModel):
    kind: str  # one of profile.ITEMS
    item: dict[str, str | bool]


class Changes(BaseModel):
    """What the reviewer collected while reviewing, applied together."""
    details: list[Change] = []  # to add to the profile: removed from every document
    boxes: dict[str, list[Box]] = {}  # drawn, by document: removed from that one only
    first: str | None = None  # the document in front of the reviewer: cleaned first
    check: bool = False  # only say whether the details are fine: nothing saved or cleaned


class Chosen(BaseModel):
    words: list[int]  # indices into the page's words


# ---------------------------------------------------------------- app

def unfinished(ws: Workspace) -> list[str]:
    """Documents to clean again when the app opens: never finished (the app quit while
    cleaning them, or they were added while it was closed), or ended in an error."""
    out = []
    for src in ws.pending():
        stem = ws.stem(src)
        report = _read_json(ws.outputs(stem)["report"], None)
        if report is None or any(i["check"] == "error" for i in report["issues"]):
            out.append(stem)
    return out


def create_app(ws: Workspace, token: str, ocr: bool = True, ui_dir: Path | None = UI_DIR,
               lanes: int | None = None, resume: bool = False, worker: Worker | None = None) -> FastAPI:
    """`resume`: clean what was left unfinished (the app, not tests or the command line).
    `worker`: one made elsewhere (the browser's, whose lanes are workers of the page's)."""
    ws.init()
    relabelled = ws.upgrade()  # permanent ids, and labels as each person's role says
    worker = worker or Worker(ws, ocr, lanes)
    if resume and ws.profile_path.exists():
        stems = unfinished(ws)
        if relabelled:  # every cleaned document still has the old labels
            stems += [ws.stem(src) for src in ws.pending()
                      if ws.stem(src) not in stems and ws.outputs(ws.stem(src))["report"].exists()]
        for stem in stems:
            worker.submit(Job(stem, "process"))
    app = FastAPI(title="taxonymous", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "::1"])

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)  # the UI's own files
        given = request.headers.get(TOKEN_HEADER) or request.query_params.get("token") or ""
        if not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"detail": "missing or wrong token"}, status_code=401)
        return await call_next(request)

    def overrides(stem: str) -> Overrides:
        return Overrides.load(ws.overrides_path(stem))

    def need_profile() -> None:
        if not ws.profile_path.exists():
            raise HTTPException(409, "Add your details first, so they can be found and removed.")

    @app.get("/api/profile")
    def get_profile():
        ws.upgrade()  # one put in place while the app runs (copied in by hand) gets its ids first
        exists = ws.profile_path.exists()
        profile, errors = clean(json.loads(ws.profile_path.read_text()) if exists else {})
        return {"exists": exists, "profile": profile, "errors": errors, "path": str(ws.profile_path)}

    @app.put("/api/profile")
    def put_profile(body: Annotated[dict, Body()]):
        profile, errors = clean(body)
        if not errors and not profile["people"]:
            errors["people.0.name"] = "Add at least yourself."
        if errors:
            raise HTTPException(422, {"message": "Some details need fixing.", "fields": errors})
        before = clean(json.loads(ws.profile_path.read_text()))[0] if ws.profile_path.exists() else None
        if profile == before:  # saved as it was: nothing to write, relabel or clean again
            return {"exists": True, "profile": profile, "errors": {}, "path": str(ws.profile_path),
                    "processed": [], "changed": False}
        save_profile(profile, ws.profile_path)
        ws.upgrade()  # a person whose role changed gets their new labels
        stems = [ws.stem(src) for src in ws.pending()]
        processed = [stem for stem in stems if ws.outputs(stem)["report"].exists()]
        for stem in processed:  # cleaned with the earlier details or labels: cleaned again
            if not worker.waiting(stem):  # one being cleaned now goes again after
                worker.submit(Job(stem, "process"))
        return {"exists": True, "profile": profile, "errors": {}, "path": str(ws.profile_path),
                "processed": processed, "changed": True}

    @app.post("/api/profile/items")
    def add_to_profile(body: Detail):
        """One detail the reviewer saw left readable in a document, added to their details.
        With no details yet, the first can be a person (clicked on a W-2): it is you."""
        first = not ws.profile_path.exists()
        if first and body.kind != "person":
            need_profile()
        profile, errors = add_item({} if first else json.loads(ws.profile_path.read_text()),
                                   body.kind, {"role": "taxpayer", **body.item} if first else body.item)
        if errors:
            message = errors.pop("", "Some details need fixing.")
            raise HTTPException(422, {"message": message, "fields": errors})
        save_profile(profile, ws.profile_path)
        queued = []
        if body.rerun is not None:
            _source(ws, body.rerun)
            stems = [ws.stem(src) for src in ws.pending()]
            # everything, the first time (nothing could be cleaned before); else what was cleaned
            again = [s for s in stems if s != body.rerun and (first or ws.outputs(s)["report"].exists())]
            for stem in [body.rerun, *again]:
                if not worker.waiting(stem):  # one being cleaned now goes again after
                    worker.submit(Job(stem, "process"))
                    queued.append(stem)
        return {"added": body.kind, "queued": queued}

    @app.post("/api/changes")
    def apply_changes(body: Changes):
        first = not ws.profile_path.exists()
        details = body.details
        if first:  # no details yet: a person comes first, and the first person is you
            details = sorted(details, key=lambda d: d.kind != "person")
            if (details and details[0].kind != "person") or (not details and body.boxes):
                raise HTTPException(409, "Start with your own name, so Taxonymous knows who you are.")
        profile = {} if first else json.loads(ws.profile_path.read_text())
        for n, d in enumerate(details):
            item = {"role": "taxpayer", **d.item} if first and n == 0 else d.item
            profile, errors = add_item(profile, d.kind, item)
            if errors:
                message = errors.pop("", "Some details need fixing.")
                raise HTTPException(422, {"message": message, "fields": errors})
        boxes: dict[str, list[tuple[int, tuple[float, float, float, float]]]] = {}
        for stem, drawn in body.boxes.items():
            sizes = _page_sizes(ws, _source(ws, stem), stem)
            for box in drawn:
                x0, y0, x1, y1 = box.rect
                if not 0 <= box.page < len(sizes) or not (x0 < x1 and y0 < y1):
                    raise HTTPException(400, "rect must be [x0, y0, x1, y1] on an existing page")
                boxes.setdefault(stem, []).append((box.page, (x0, y0, x1, y1)))
        if body.first is not None:
            _source(ws, body.first)
        if body.check:
            return {"ok": True}
        if details:
            save_profile(profile, ws.profile_path)
        for stem, drawn in boxes.items():
            o = overrides(stem)
            o.add.extend(drawn)
            o.save(ws.overrides_path(stem))
        stems = [ws.stem(src) for src in ws.pending()]
        again = [*boxes]
        if details:  # everything the first time (nothing could be cleaned before), else what was cleaned
            again += [s for s in stems if first or ws.outputs(s)["report"].exists()]
        if body.first is not None and (details or body.first in boxes):
            again.insert(0, body.first)
        queued = []
        for stem in dict.fromkeys(again):
            if not worker.waiting(stem):  # one being cleaned now goes again after
                worker.submit(Job(stem, "process"))
            queued.append(stem)
        return {"added": len(details), "queued": queued}

    @app.get("/api/profile/found")
    def found():
        return _found(ws)

    @app.get("/api/documents")
    def documents():
        return [_summary(ws, worker, src) for src in ws.pending()]

    @app.post("/api/documents", status_code=201)
    async def upload(file: UploadFile, folder: Annotated[str, Form()] = ""):
        name = Path(file.filename or "").name
        if not name or name.startswith(".") or Path(name).suffix.lower() not in SUFFIXES:
            raise HTTPException(400, "only PDFs and images (JPG, PNG, HEIC, TIFF, WebP)")
        folder = _folder(folder)
        dest = ws.inbox / folder / name
        if dest.exists():
            raise HTTPException(409, f"{name} is already in {folder or 'the inbox'}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
        tmp = dest.with_name(f".{name}.part")
        tmp.write_bytes(data)
        tmp.rename(dest)
        return _summary(ws, worker, dest)

    @app.get("/api/documents/{stem:path}/pages/{n}/{which}.png")
    def page_image(stem: str, n: int, which: str, dpi: int = 110):
        src = _source(ws, stem)
        dpi = max(DPI_RANGE[0], min(DPI_RANGE[1], dpi))
        if which == "redacted":
            pdf = ws.outputs(stem)["pdf"]
            if not pdf.exists():
                raise HTTPException(404, "not redacted yet")
            doc = pymupdf.open(pdf)
        elif which == "original" and src.suffix.lower() in IMAGE_SUFFIXES:
            img = load_image(src)
            width, height = _page_sizes(ws, src, stem)[0]
            img = img.resize((max(1, round(width * dpi / 72)), max(1, round(height * dpi / 72))))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            if n != 0:
                raise HTTPException(404, "a photo has one page")
            return Response(buf.getvalue(), media_type="image/png")
        elif which == "original":
            doc = pymupdf.open(src)
            if doc.needs_pass:
                raise HTTPException(409, "password-protected")
        else:
            raise HTTPException(404, "original or redacted")
        if not 0 <= n < doc.page_count:
            raise HTTPException(404, "no such page")
        return Response(_png(doc[n], dpi), media_type="image/png", headers={"Cache-Control": "no-store"})

    def words_of(stem: str, n: int):
        src = _source(ws, stem)
        size = None
        if src.suffix.lower() in IMAGE_SUFFIXES:
            sizes = _page_sizes(ws, src, stem)
            size = tuple(sizes[0]) if sizes else None
        try:
            return page_words(src, n, size)
        except IndexError as e:
            raise HTTPException(404, str(e)) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/documents/{stem:path}/pages/{n}/words")
    def words(stem: str, n: int):
        return {"words": [w.json() for w in words_of(stem, n)]}

    @app.post("/api/documents/{stem:path}/pages/{n}/suggest")
    def suggestion(stem: str, n: int, body: Chosen):
        found = words_of(stem, n)
        chosen = sorted({i for i in body.words if 0 <= i < len(found)})
        if not chosen:
            raise HTTPException(422, "Choose a word first.")
        chosen = grow(found, chosen)
        text = " ".join(found[i].text for i in chosen)
        people = ws.profile_path.exists() and bool(json.loads(ws.profile_path.read_text()).get("people"))
        kind, item, note = suggest(text, label_for(found, chosen), has_people=people)
        return {"words": chosen, "text": text, "kind": kind, "item": item, "note": note}

    @app.get("/api/documents/{stem:path}/text", response_class=PlainTextResponse)
    def text(stem: str):
        if ws.source(stem) is None and ws.source(f"{stem}/text") is not None:
            return JSONResponse(document(f"{stem}/text"))  # a document named text, in a folder
        _source(ws, stem)
        txt = ws.outputs(stem)["txt"]
        if not txt.exists():
            raise HTTPException(404, "not redacted yet")
        return txt.read_text()

    @app.post("/api/run", status_code=202)
    def run_all(folder: str = ""):
        need_profile()
        folder = _folder(folder)
        stems = [ws.stem(src) for src in ws.pending()]
        stems = [s for s in stems if not folder or s.startswith(folder + "/")]
        for stem in stems:
            if not worker.busy(stem):
                worker.submit(Job(stem, "process"))
        return {"queued": stems}

    @app.post("/api/documents/{stem:path}/run", status_code=202)
    def run_one(stem: str):
        _source(ws, stem)
        need_profile()
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.get("/api/status")
    def status():
        return worker.status()

    @app.post("/api/documents/{stem:path}/redactions", status_code=202)
    def add_redactions(stem: str, body: Boxes):
        src = _source(ws, stem)
        sizes = _page_sizes(ws, src, stem)
        if not body.boxes:
            raise HTTPException(400, "no boxes")
        o = overrides(stem)
        for box in body.boxes:
            x0, y0, x1, y1 = box.rect
            if not 0 <= box.page < len(sizes) or not (x0 < x1 and y0 < y1):
                raise HTTPException(400, "rect must be [x0, y0, x1, y1] on an existing page")
            o.add.append((box.page, (x0, y0, x1, y1)))
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.delete("/api/documents/{stem:path}/redactions", status_code=202)
    def remove_redaction(stem: str, placeholder: str):
        _source(ws, stem)
        regions = _read_json(ws.outputs(stem)["regions"], [])
        match = [r for r in regions if r["placeholder"] == placeholder]
        if not match:
            raise HTTPException(404, f"no redaction {placeholder} in this document")
        o = overrides(stem)
        forget = []
        drawn = {manual_key(stem, n, r): (n, r) for n, r in o.add}
        vault = Vault(ws.vault_dir / "placeholders.json")
        keys = {key for key, ph in vault.by_key.items() if ph == placeholder}
        if keys & drawn.keys():  # a box the reviewer drew: take it away
            for key in keys & drawn.keys():
                o.add.remove(drawn[key])
                forget.append(key)
        else:  # something the engine found: leave it in the clear in this document
            o.keep.add(placeholder)
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process", forget))
        return {"queued": [stem]}

    @app.post("/api/documents/{stem:path}/shown", status_code=202)
    def show_one(stem: str, body: Box):
        _source(ws, stem)
        regions = _read_json(ws.outputs(stem)["regions"], [])
        match = next((r for r in regions if r["page"] == body.page
                      and all(abs(a - b) < 0.6 for a, b in zip(r["rect"], body.rect, strict=True))), None)
        if match is None:
            raise HTTPException(404, "no redaction box there in this document")
        if match["source"] == "manual":
            raise HTTPException(400, "that is a box you drew: remove it instead")
        o = overrides(stem)
        o.show.append((body.page, tuple(match["rect"]), match["placeholder"]))
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.delete("/api/documents/{stem:path}/shown/{index}", status_code=202)
    def unshow(stem: str, index: int):
        _source(ws, stem)
        o = overrides(stem)
        if not 0 <= index < len(o.show):
            raise HTTPException(404, "no such shown box in this document")
        del o.show[index]
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.delete("/api/documents/{stem:path}/kept", status_code=202)
    def unkeep(stem: str, placeholder: str):
        _source(ws, stem)
        o = overrides(stem)
        if placeholder not in o.keep:
            raise HTTPException(404, f"{placeholder} is not kept in this document")
        o.keep.discard(placeholder)
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.post("/api/documents/{stem:path}/verify", status_code=202)
    def verify_again(stem: str):
        _source(ws, stem)
        if not ws.outputs(stem)["pdf"].exists():
            raise HTTPException(409, "not redacted yet")
        worker.submit(Job(stem, "verify"))
        return {"queued": [stem]}

    @app.post("/api/documents/{stem:path}/export")
    def export_one(stem: str):
        _source(ws, stem)
        state = _status(ws, worker, stem)
        if state != "passed":
            raise HTTPException(409, f"cannot export: document is {state}")
        released = export(ws, stem)
        if not released:
            raise HTTPException(409, "cannot export: verification has not passed")
        # "paths": where each is in ready-to-upload/ (the browser app writes them where you choose)
        return {"released": [p.name for p in released],
                "paths": [p.relative_to(ws.ready).as_posix() for p in released]}

    @app.post("/api/reveal", status_code=204)
    def reveal():
        """Open the safe copies folder (ready-to-upload/) in Finder."""
        if sys.platform != "darwin":
            raise HTTPException(501, "only on a Mac")
        ws.ready.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(ws.ready)], check=False)

    @app.post("/api/merge", status_code=202)
    def merge_folder(body: MergeOptions, folder: str = ""):
        need_profile()
        folder = _folder(folder)
        if body.max_mb is not None and not 0.1 <= body.max_mb <= 10_000:
            raise HTTPException(400, "the size limit is in MB, from 0.1")
        if not any(ws.stem(p).startswith(folder + "/") or not folder for p in ws.pending()):
            raise HTTPException(404, f"no documents in {folder or 'the inbox'}")
        if not worker.busy(merge_id(folder)):
            worker.submit(Job(merge_id(folder), "merge", folder=folder, max_mb=body.max_mb))
        return {"queued": [merge_id(folder)]}

    @app.get("/api/merge")
    def merge_status(folder: str = ""):
        folder = _folder(folder)
        job = merge_id(folder)
        state = worker.state(job)
        return {"folder": folder, "state": state, "error": worker.errors.get(job),
                "progress": worker.progress_of(job), "last": last_merge(ws, folder)}

    # last: an id holds slashes, so this would take every /api/documents/... GET above
    @app.delete("/api/documents/{stem:path}")  # last but one: "/redactions" etc. are matched first
    def remove(stem: str):
        """A document taken out of Taxonymous entirely. Its placeholders stay in the vault:
        other documents may share them, and a value keeps its label if it comes back."""
        src = _source(ws, stem)
        if worker.busy(stem):
            raise HTTPException(409, "It's being cleaned. Remove it when that's done.")
        for path in [src, *ws.outputs(stem).values(), ws.overrides_path(stem), *ws.stale(stem)]:
            path.unlink(missing_ok=True)
        worker.errors.pop(stem, None)
        # folders it leaves empty go too (inbox/2024/ with nothing left in it)
        for root in (ws.inbox, ws.work, ws.ready, ws.overrides_path("x").parent):
            folder = (root / stem).parent
            while folder != root and folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        return {"removed": stem}

    @app.get("/api/documents/{stem:path}")
    def document(stem: str):
        src = _source(ws, stem)
        o = overrides(stem)
        out = ws.outputs(stem)
        return {**_summary(ws, worker, src),
                "pages": _page_sizes(ws, src, stem),
                "regions": _read_json(out["regions"], []),
                "report": _read_json(out["report"], None),
                "kept": sorted(o.keep),
                "shown": [{"page": n, "rect": list(r), "placeholder": ph} for n, r, ph in o.show],
                "added": [{"page": n, "rect": list(r)} for n, r in o.add]}

    if ui_dir is not None and (ui_dir / "index.html").exists():
        root = ui_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        def ui(path: str):
            target = (root / path).resolve()
            if path and target.is_file() and target.is_relative_to(root):
                return FileResponse(target)
            return FileResponse(root / "index.html")  # the app routes everything else itself

    return app


def serve(ws: Workspace, host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str | None = None,
          ocr: bool = True, open_browser: bool = False) -> None:
    check_loopback(host)
    import uvicorn
    token = token or secrets.token_urlsafe(32)
    app = create_app(ws, token, ocr)
    shown = f"[{host}]" if ":" in host else host
    url = f"http://{shown}:{port}/?token={token}"
    print(f"taxonymous on {url}", flush=True)
    if not (UI_DIR / "index.html").exists():
        print("(API only: build the review UI with `cd ui && npm install && npm run build`)", flush=True)
    elif open_browser:
        import webbrowser
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
