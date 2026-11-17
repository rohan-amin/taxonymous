"""Cleaning several documents at once, as many as the computer has room for.

A lane cleans one document at a time. With one lane (a computer with fewer than 8 cores,
or little memory) documents are cleaned in the app's own process, one after another.
With more, each lane is a helper process (`python -m taxonymous.lanes --serve`): the PDF
library can't be used from several threads at once, and a process also runs its Python
work on a core of its own. The cores' OCR reads are shared out between the lanes.

A helper is given one job at a time as a line of JSON and answers with lines that start
with MARK: progress as it goes, then the outcome. Anything else it prints is ignored.
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .detect import ProfileMatcher
from .pipeline import Workspace, process, reverify
from .profile import Profile
from .progress import Progress
from .vault import Vault

MARK = "\x1etaxonymous "
GIB = 2**30


def count() -> int:
    """Lanes for this computer: TAXONYMOUS_LANES, or 1 below 8 cores, 2 from 8, 3 from 12,
    and no more than one for every 4 GB of memory (a lane holds a few rendered pages)."""
    try:
        return max(1, int(os.environ["TAXONYMOUS_LANES"]))
    except (KeyError, ValueError):
        pass
    cores = os.cpu_count() or 1
    n = 1 if cores < 8 else 2 if cores < 12 else 3
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        n = min(n, max(1, memory // (4 * GIB)))
    except (ValueError, OSError, AttributeError):
        pass
    return n


def ocr_share(lanes: int) -> int:
    """OCR reads at a time for each lane: the cores shared out, with one over so a lane's
    reads keep going while another does its Python work."""
    return max(1, (os.cpu_count() or 1) // lanes + 1)


def run_job(ws: Workspace, action: str, stem: str, forget: list[str], ocr: bool, progress: Progress) -> None:
    """Clean (`process`) or check again (`verify`) one document."""
    if not ws.profile_path.exists():
        raise FileNotFoundError(f"no profile at {ws.profile_path}")
    ws.upgrade()
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    for key in forget:
        vault.forget(key)
    if action == "verify":
        reverify(ws, stem, matcher, vault, ocr=ocr, progress=progress)
        return
    src = ws.source(stem)
    if src is None:
        raise FileNotFoundError(f"no document {stem!r}")
    process(src, ws, matcher, vault, ocr=ocr, release=False, progress=progress)


class Lane:
    """A helper process that cleans documents one at a time; started on first use, and
    again if it stops."""

    def __init__(self, ocr_slots: int):
        self.ocr_slots = ocr_slots
        self.proc: subprocess.Popen | None = None

    def _start(self) -> subprocess.Popen:
        if self.proc is None or self.proc.poll() is not None:
            env = dict(os.environ, TAXONYMOUS_WORKERS=str(self.ocr_slots), TAXONYMOUS_LANES="1")
            self.proc = subprocess.Popen([sys.executable, "-m", "taxonymous.lanes", "--serve"], env=env,
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        return self.proc

    def run(self, ws: Workspace, action: str, stem: str, forget: list[str], ocr: bool,
            on_progress: Callable[[dict], None]) -> None:
        proc = self._start()
        job = {"root": str(ws.root), "action": action, "stem": stem, "forget": forget, "ocr": ocr}
        proc.stdin.write(json.dumps(job) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            if not line.startswith(MARK):
                continue  # a library talking
            msg = json.loads(line[len(MARK):])
            if "progress" in msg:
                on_progress(msg["progress"])
            elif "done" in msg:
                if msg["error"]:
                    raise RuntimeError(msg["error"])
                return
        self.proc = None
        raise RuntimeError("the cleaning process stopped unexpectedly")

    def kill(self) -> None:
        """Stop the job it's on, at once (its document was removed): the process ends where
        it is, and the next job starts a new one. What it writes is written whole or not at
        all (files.py), so it leaves nothing half-written."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)


class _Reporting(Progress):
    """A helper's progress, sent to the app as it changes (at most every tenth of a second)."""

    def __init__(self, send: Callable[[dict], None]):
        super().__init__()
        self.send, self.last = send, 0.0

    def start(self, stage: str, total: int) -> None:
        super().start(stage, total)
        self._report(force=True)

    def step(self, n: int = 1) -> None:
        super().step(n)
        self._report(force=self.done == self.total)

    def _report(self, force: bool) -> None:
        import time
        now = time.monotonic()
        if force or now - self.last >= 0.1:
            self.last = now
            snap = self.snapshot()
            if snap:
                self.send({"progress": snap})


def serve() -> None:
    from .vision import exit_with_parent
    exit_with_parent()
    out = sys.stdout

    def send(msg: dict) -> None:
        try:
            out.write(MARK + json.dumps(msg) + "\n")
            out.flush()
        except (BrokenPipeError, OSError):
            # the app is gone (quit mid-clean): stop now, writing nothing. Raising would be
            # caught by verification, which would record the document as failed
            os._exit(0)

    for line in sys.stdin:
        job = json.loads(line)
        error = None
        try:
            run_job(Workspace(Path(job["root"])), job["action"], job["stem"], job["forget"], job["ocr"],
                    _Reporting(send))
        except Exception as e:  # reported on the document, like the app's own worker does
            error = f"{type(e).__name__}: {str(e)[:300]}"
        send({"done": True, "error": error})


if __name__ == "__main__" and sys.argv[1:] == ["--serve"]:
    serve()
