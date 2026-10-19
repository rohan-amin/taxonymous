"""How far a run has got, for the review UI: the stage, and how many of its pages are done.

The engine reports what it actually finished (a page read, a page checked), so the bar
only moves when work does. OCR runs pages in parallel threads, so updates are locked.
"""

import threading
from collections.abc import Callable

STAGES = {
    "reading": "Reading pages",
    "redacting": "Redacting pages",
    "checking": "Checking the redacted pages",
}


class Progress:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stage: str | None = None
        self.done = self.total = 0

    def start(self, stage: str, total: int) -> None:
        with self._lock:
            self.stage, self.done, self.total = stage, 0, total

    def step(self, n: int = 1) -> None:
        with self._lock:
            self.done = min(self.total, self.done + n)

    def snapshot(self) -> dict | None:
        with self._lock:
            if self.stage is None:
                return None
            return {"stage": self.stage, "label": STAGES[self.stage], "done": self.done, "total": self.total}

    def per_page(self, jobs_per_page: list[int]) -> Callable[[int], None]:
        """For a list of OCR jobs laid out page by page: a callback for each finished job
        that steps once a page's last job is done."""
        owner = [n for n, count in enumerate(jobs_per_page) for _ in range(count)]
        left = list(jobs_per_page)
        lock = threading.Lock()

        def done(job: int) -> None:
            with lock:
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
