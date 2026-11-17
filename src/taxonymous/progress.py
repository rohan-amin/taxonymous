"""How far a run has got, for the review UI: the stage, how many of its pages are done,
and the whole job as one fraction that only ever grows, with the time it has left.

The engine reports what it actually finished (a page read, a page checked), so the bar
only moves when work does. OCR runs pages in parallel threads, so updates are locked.

The stages are parts of one bar, weighted by the work they usually take. When a document
needs a second look (a page rebuilt from what it shows, then checked again), the stages
start over in what is left of the bar instead of sending it backwards.
"""

import threading
import time
from collections.abc import Callable

STAGES = {
    "reading": "Reading pages",
    "redacting": "Redacting pages",
    "checking": "Checking the redacted pages",
}
WEIGHTS = {"reading": 0.4, "redacting": 0.15, "checking": 0.45}
AGAIN = "Taking a closer look"  # the label while a second look runs
FIRST_SHARE = 0.9  # of the bar, for the first look: full only when the job is done
AGAIN_SHARE = 0.85  # of what is left of the bar, for a second look (more may follow)


class Cancelled(BaseException):
    """A job stopped because its document was removed. Not an Exception: nothing on the way
    (verification records what goes wrong as a failure) may catch it and carry on."""


class Progress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cancelled = False  # set by cancel(): the job stops at its next step
        self.stage: str | None = None
        self.done = self.total = 0
        self.started = time.monotonic()
        self.rounds = 0  # second looks so far
        self._base, self._top = 0.0, FIRST_SHARE  # the part of the bar this round fills
        self._first: str | None = None  # the round's first stage (a re-check starts at checking)
        self._shown = 0.0
        self.remote: dict | None = None  # what a lane's own process last reported

    def cancel(self) -> None:
        self.cancelled = True

    def show(self, snapshot: dict) -> None:
        """Report what another process's Progress says (a lane's)."""
        with self._lock:
            self.remote = snapshot

    def start(self, stage: str, total: int) -> None:
        if self.cancelled:
            raise Cancelled
        with self._lock:
            order = list(STAGES)
            if self.stage is not None and order.index(stage) <= order.index(self.stage):
                # back to an earlier stage: a second look, in what is left of the bar
                self.rounds += 1
                self._base = self._shown
                self._top = self._base + (1 - self._base) * AGAIN_SHARE
                self._first = stage
            if self._first is None:
                self._first = stage
            self.stage, self.done, self.total = stage, 0, total

    def step(self, n: int = 1) -> None:
        if self.cancelled:
            raise Cancelled
        with self._lock:
            self.done = min(self.total, self.done + n)

    def _fraction(self) -> float:
        if self.stage is None:
            return 0.0
        order = list(STAGES)
        stages = order[order.index(self._first):]
        weight = sum(WEIGHTS[s] for s in stages)
        before = sum(WEIGHTS[s] for s in stages[:stages.index(self.stage)])
        inside = self.done / self.total if self.total else 0.0
        part = (before + WEIGHTS[self.stage] * inside) / weight
        self._shown = max(self._shown, self._base + (self._top - self._base) * part)
        return self._shown

    def snapshot(self) -> dict | None:
        with self._lock:
            if self.remote is not None:
                return self.remote
            if self.stage is None:
                return None
            fraction = self._fraction()
            elapsed = time.monotonic() - self.started
            # time left at this document's own pace, once there is a pace to go by; none during a
            # second look, whose pace says nothing about what is left
            known = fraction >= 0.1 and elapsed >= 1.5 and not self.rounds
            eta = round(elapsed * (FIRST_SHARE - fraction) / fraction) if known else None
            return {"stage": self.stage, "label": AGAIN if self.rounds else STAGES[self.stage],
                    "done": self.done, "total": self.total, "fraction": round(fraction, 3), "eta": eta}

    def per_page(self, jobs_per_page: list) -> Callable[[int], None]:
        """For a list of OCR jobs laid out page by page: a callback for each finished job
        that steps once a page's last job is done. `jobs_per_page` holds counts, or each
        page's passes; it may still be growing (pages added as they are rendered), as long
        as a page is in it before its jobs finish."""
        owner: list[int] = []
        left: list[int] = []
        lock = threading.Lock()

        def done(job: int) -> None:
            with lock:
                while len(owner) <= job:  # pages added since
                    n = len(left)
                    count = jobs_per_page[n] if isinstance(jobs_per_page[n], int) else len(jobs_per_page[n])
                    owner.extend([n] * count)
                    left.append(count)
                left[owner[job]] -= 1
                finished = left[owner[job]] == 0
            if finished:
                self.step()
        return done


class Silent(Progress):
    """For the CLI and tests: tracks nothing."""

    def start(self, stage: str, total: int) -> None:
        pass

    def step(self, n: int = 1) -> None:
        pass


NONE = Silent()
