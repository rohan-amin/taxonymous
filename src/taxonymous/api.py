"""Local HTTP API for the review UI.

Binds to loopback only (`serve` refuses anything else), answers only requests
addressed to a loopback host name (so a web page can't reach it through DNS
rebinding), and only with the token printed at launch (so another site open in
the same browser can't drive it). It makes no outbound connections.

  GET    /api/documents                         every document in inbox/, with its status
  POST   /api/documents                         upload a file into inbox/ (multipart "file")
  GET    /api/documents/{id}                    status, report, boxes, reviewer decisions, page sizes
  GET    /api/documents/{id}/pages/{n}/original.png | redacted.png   (?dpi=)
  GET    /api/documents/{id}/text               the redacted placeholder text
  POST   /api/run                               redact + verify everything (queued)
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

The review UI (ui/, built into ui/dist) is served at /. Its files hold no data,
so they need no token; every /api request does.

Every change to a document re-runs it. Document ids are the output stem
(`w2` for w2.pdf, `receipt.jpg` for receipt.jpg); real values never appear in
any response except the original page images.
"""

import hmac
import io
import json
import queue
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .detect import ProfileMatcher
from .pipeline import Workspace, export, process, reverify
from .profile import Profile
from .redact import IMAGE_SUFFIXES, SUFFIXES, Overrides, load_image, manual_key, output_stem
from .vault import Vault

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
    action: str  # "process" | "verify"
    forget: list[str] = field(default_factory=list)  # vault keys of boxes the reviewer removed


class Worker:
    """Runs one job at a time, so the vault and outputs are only ever written by one thread."""

    def __init__(self, ws: Workspace, ocr: bool = True):
        self.ws, self.ocr = ws, ocr
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.lock = threading.Lock()
        self.queued: list[str] = []
        self.running: str | None = None
        self.errors: dict[str, str] = {}
        threading.Thread(target=self._loop, daemon=True, name="taxonymous-worker").start()

    def submit(self, job: Job) -> None:
        with self.lock:
            self.queued.append(job.stem)
        self.jobs.put(job)

    def busy(self, stem: str) -> bool:
        with self.lock:
            return stem in self.queued or self.running == stem

    def status(self) -> dict:
        with self.lock:
            return {"running": self.running, "queued": list(self.queued), "idle": self.idle()}

    def idle(self) -> bool:
        return self.running is None and not self.queued

    def wait(self, timeout: float = 600) -> None:
        """Block until every submitted job is done (tests and scripts)."""
        end = time.monotonic() + timeout
        while self.jobs.unfinished_tasks:
            if time.monotonic() > end:
                raise TimeoutError("worker still busy")
            time.sleep(0.05)

    def _loop(self) -> None:
        while True:
            job = self.jobs.get()
            with self.lock:
                self.queued.remove(job.stem)
                self.running = job.stem
            try:
                self._run(job)
                self.errors.pop(job.stem, None)
            except Exception as e:  # reported on the document, never kills the worker
                self.errors[job.stem] = f"{type(e).__name__}: {str(e)[:300]}"
            finally:
                with self.lock:
                    self.running = None
                self.jobs.task_done()

    def _run(self, job: Job) -> None:
        if not self.ws.profile_path.exists():
            raise FileNotFoundError(f"no profile at {self.ws.profile_path}")
        matcher = ProfileMatcher(Profile.load(self.ws.profile_path))
        vault = Vault(self.ws.vault_dir / "placeholders.json")
        for key in job.forget:
            vault.forget(key)
        vault.save()
        src = _source(self.ws, job.stem)
        if job.action == "verify":
            reverify(self.ws, job.stem, matcher, vault, ocr=self.ocr)
        else:
            process(src, self.ws, matcher, vault, ocr=self.ocr, release=False)


# ---------------------------------------------------------------- helpers

def _source(ws: Workspace, stem: str) -> Path:
    for src in ws.pending():
        if output_stem(src) == stem:
            return src
    raise HTTPException(404, f"no document {stem!r}")


def _read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _status(ws: Workspace, worker: Worker, stem: str) -> str:
    if worker.busy(stem):
        return "queued" if worker.running != stem else "running"
    if stem in worker.errors:
        return "error"
    report = _read_json(ws.outputs(stem)["report"], None)
    if report is None:
        return "new"
    if any(i["check"] == "error" for i in report["issues"]):
        return "error"
    return "passed" if report["passed"] else "failed"


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
    stem = output_stem(src)
    regions = _read_json(ws.outputs(stem)["regions"], [])
    report = _read_json(ws.outputs(stem)["report"], None)
    pdf = ws.outputs(stem)["pdf"]
    return {
        "id": stem,
        "name": src.name,
        "status": _status(ws, worker, stem),
        "version": pdf.stat().st_mtime_ns if pdf.exists() else 0,  # changes whenever the output does
        "exported": all(p.exists() for p in ws.released(stem)),
        "redactions": dict(Counter(r["kind"] for r in {r["placeholder"]: r for r in regions}.values())),
        "issues": report["issues"] if report else [],
        "error": worker.errors.get(stem),
    }


def _png(page: pymupdf.Page, dpi: int) -> bytes:
    return page.get_pixmap(dpi=dpi, alpha=False).tobytes("png")


class Box(BaseModel):
    page: int
    rect: tuple[float, float, float, float]


class Boxes(BaseModel):
    boxes: list[Box]


# ---------------------------------------------------------------- app

def create_app(ws: Workspace, token: str, ocr: bool = True, ui_dir: Path | None = UI_DIR) -> FastAPI:
    ws.init()
    worker = Worker(ws, ocr)
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

    @app.get("/api/documents")
    def documents():
        return [_summary(ws, worker, src) for src in ws.pending()]

    @app.post("/api/documents", status_code=201)
    async def upload(file: UploadFile):
        name = Path(file.filename or "").name
        if not name or name.startswith(".") or Path(name).suffix.lower() not in SUFFIXES:
            raise HTTPException(400, "only PDFs and images (JPG, PNG, HEIC, TIFF, WebP)")
        dest = ws.inbox / name
        if dest.exists():
            raise HTTPException(409, f"{name} is already in the inbox")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
        tmp = dest.with_name(f".{name}.part")
        tmp.write_bytes(data)
        tmp.rename(dest)
        return _summary(ws, worker, dest)

    @app.get("/api/documents/{stem}")
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

    @app.get("/api/documents/{stem}/pages/{n}/{which}.png")
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

    @app.get("/api/documents/{stem}/text", response_class=PlainTextResponse)
    def text(stem: str):
        _source(ws, stem)
        txt = ws.outputs(stem)["txt"]
        if not txt.exists():
            raise HTTPException(404, "not redacted yet")
        return txt.read_text()

    @app.post("/api/run", status_code=202)
    def run_all():
        stems = [output_stem(src) for src in ws.pending()]
        for stem in stems:
            if not worker.busy(stem):
                worker.submit(Job(stem, "process"))
        return {"queued": stems}

    @app.post("/api/documents/{stem}/run", status_code=202)
    def run_one(stem: str):
        _source(ws, stem)
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.get("/api/status")
    def status():
        return worker.status()

    @app.post("/api/documents/{stem}/redactions", status_code=202)
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

    @app.delete("/api/documents/{stem}/redactions", status_code=202)
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

    @app.post("/api/documents/{stem}/shown", status_code=202)
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

    @app.delete("/api/documents/{stem}/shown/{index}", status_code=202)
    def unshow(stem: str, index: int):
        _source(ws, stem)
        o = overrides(stem)
        if not 0 <= index < len(o.show):
            raise HTTPException(404, "no such shown box in this document")
        del o.show[index]
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.delete("/api/documents/{stem}/kept", status_code=202)
    def unkeep(stem: str, placeholder: str):
        _source(ws, stem)
        o = overrides(stem)
        if placeholder not in o.keep:
            raise HTTPException(404, f"{placeholder} is not kept in this document")
        o.keep.discard(placeholder)
        o.save(ws.overrides_path(stem))
        worker.submit(Job(stem, "process"))
        return {"queued": [stem]}

    @app.post("/api/documents/{stem}/verify", status_code=202)
    def verify_again(stem: str):
        _source(ws, stem)
        if not ws.outputs(stem)["pdf"].exists():
            raise HTTPException(409, "not redacted yet")
        worker.submit(Job(stem, "verify"))
        return {"queued": [stem]}

    @app.post("/api/documents/{stem}/export")
    def export_one(stem: str):
        _source(ws, stem)
        state = _status(ws, worker, stem)
        if state != "passed":
            raise HTTPException(409, f"cannot export: document is {state}")
        released = export(ws, stem)
        if not released:
            raise HTTPException(409, "cannot export: verification has not passed")
        return {"released": [p.name for p in released]}

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
