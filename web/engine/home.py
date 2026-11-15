"""The home engine in the browser: the app's own server (taxonymous.api, unchanged) over a
workspace in memory, answering the UI's requests. The service worker hands each /api/
request to the page, the page to this worker, and it runs here as an ASGI call: no network.

Documents are cleaned in the page's other engines (lane.py), as the app cleans them in its
lanes: a job takes the files it reads there and brings the document's outputs back here.
Nothing here starts a thread (a browser worker can't): the app's worker is replaced by one
that runs jobs as tasks on this worker's event loop.
"""

import asyncio
import json
import time
import urllib.parse
from pathlib import Path

import anyio.to_thread
import bridge  # noqa: F401  (OCR, barcodes and the vault's lock, before the engine loads)


async def _direct(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
    return func(*args)


anyio.to_thread.run_sync = _direct  # Starlette runs plain handlers "in a thread": here, in place

from taxonymous import api  # noqa: E402
from taxonymous.pipeline import Workspace  # noqa: E402
from taxonymous.progress import Progress  # noqa: E402

ROOT = Path("/ws")
APP = None
_jobs: dict[int, Progress] = {}  # a lane's job, by its id: where its progress goes


class BrowserWorker(api.Worker):
    """The app's worker (its queue, batches, progress and errors), with jobs run as tasks
    and each document's cleaning in a lane of the page's."""

    def __init__(self, ws: Workspace, lanes: int):
        import threading
        self.ws, self.ocr, self.lanes = ws, True, lanes
        self.cv = threading.Condition()  # one thread here: never waited on, only kept in step
        self.pending, self.running, self.errors = [], {}, {}
        self.batch, self.pages, self.per_page, self.unfinished = set(), {}, None, 0
        self.free = lanes
        self.next_id = 0

    def submit(self, job: api.Job) -> None:
        super().submit(job)
        self._start()

    def _start(self) -> None:
        while self.free > 0:
            job = self._next()
            if job is None:
                return
            self.free -= 1
            progress = Progress()
            self.running[job.stem] = progress
            asyncio.ensure_future(self._go(job, progress))

    async def _go(self, job: api.Job, progress: Progress) -> None:
        started = time.monotonic()
        try:
            if job.action == "merge":
                self._run(job, None, progress)  # here: it joins the outputs that are here
            else:
                await self._lane(job, progress)
            self.errors.pop(job.stem, None)
        except Exception as e:  # reported on the document, never stops the queue
            self.errors[job.stem] = f"{type(e).__name__}: {str(e)[:300]}"
        finally:
            with self.cv:
                del self.running[job.stem]
                pages = self.pages.pop(job.stem, 0) if not self.busy_unlocked(job.stem) else 0
                if job.action == "process" and pages:
                    took = (time.monotonic() - started) / pages
                    self.per_page = took if self.per_page is None else 0.7 * self.per_page + 0.3 * took
                self.unfinished -= 1
            self.free += 1
            self._start()

    async def _lane(self, job: api.Job, progress: Progress) -> None:
        from js import laneJob
        from pyodide.ffi import to_js
        ws, stem = self.ws, job.stem
        src = ws.source(stem)
        if src is None:
            raise FileNotFoundError(f"no document {stem!r}")
        reads = [ws.profile_path, ws.vault_dir / "placeholders.json", src, ws.overrides_path(stem),
                 *ws.outputs(stem).values()]
        files = {str(p.relative_to(ws.root)): p.read_bytes() for p in reads if p.exists()}
        self.next_id += 1
        job_id = self.next_id
        _jobs[job_id] = progress
        try:
            payload = {"id": job_id, "action": job.action, "stem": stem, "forget": job.forget}
            result = (await laneJob(json.dumps(payload), to_js(files))).to_py()
        finally:
            del _jobs[job_id]
        for name, path in ws.outputs(stem).items():
            data = result["outputs"].get(name)
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.tmp")
                tmp.write_bytes(bytes(data))
                tmp.replace(path)
        # the labels the lane named, into this workspace's copy of the vault (read directly by
        # some of the server's answers, as the app reads the shared file)
        vault = ws.vault_dir / "placeholders.json"
        text = bridge.call("vault_read", {})
        if text:
            vault.write_text(text)
        if result.get("error"):
            raise RuntimeError(result["error"])


def progress(job_id: int, snapshot: str) -> None:
    """A lane's progress on a job (the page passes it on)."""
    p = _jobs.get(job_id)
    if p is not None:
        p.show(json.loads(snapshot))


def start(token: str, lanes: int) -> None:
    global APP
    ws = Workspace(ROOT)
    ws.init()
    APP = api.create_app(ws, token, ocr=True, ui_dir=None, worker=BrowserWorker(ws, lanes))


async def request(method: str, path: str, query: str, headers_json: str, body):
    """One request, as the app's server would have it: (status, headers, body)."""
    from pyodide.ffi import to_js
    headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in json.loads(headers_json)]
    headers.append((b"host", b"127.0.0.1"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": urllib.parse.unquote(path), "raw_path": path.encode(),
             "query_string": query.encode(), "root_path": "", "headers": headers,
             "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80)}
    data = bytes(body.to_py()) if body is not None else b""
    given = False

    async def receive():
        nonlocal given
        if not given:
            given = True
            return {"type": "http.request", "body": data, "more_body": False}
        await asyncio.Event().wait()  # the page never goes away mid-request
        return {"type": "http.disconnect"}

    out = {"status": 500, "headers": [], "body": []}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            out["body"].append(msg.get("body", b""))

    await APP(scope, receive, send)
    return to_js([out["status"], json.dumps(out["headers"]), b"".join(out["body"])])
