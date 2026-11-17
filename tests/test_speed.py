"""The time left before anything of yours has been cleaned: from how long a page with text
and a picture page took on this computer (the browser app times two sample pages once)."""

import threading
import time

import pymupdf

from taxonymous import api
from taxonymous.pipeline import Workspace
from taxonymous.progress import Progress


def _worker(ws: Workspace, lanes: int = 1) -> api.Worker:
    w = object.__new__(api.Worker)  # its fields only: no lanes running
    w.ws, w.lanes, w.cv = ws, lanes, threading.Condition()
    w.pending, w.running, w.batch, w.pages, w.mix = [], {}, set(), {}, {}
    w.per_page, w.speed = None, None
    return w


def test_pages_with_text_and_picture_pages_are_counted_apart(tmp_path):
    ws = Workspace(tmp_path)
    ws.init()
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Wages, tips, other compensation 52,000.00")
    doc.new_page()  # nothing to read: a scan, as far as timing goes
    doc.save(ws.inbox / "mixed.pdf")
    (ws.inbox / "photo.jpg").write_bytes(b"not read")
    assert api._mix(ws, "mixed") == (1, 1)
    assert api._mix(ws, "photo") == (0, 1)


def test_time_left_comes_from_the_timed_pages_until_there_is_a_pace(tmp_path):
    w = _worker(Workspace(tmp_path), lanes=1)
    assert w._batch_left() is None  # nothing timed, nothing measured: no guess
    w.speed = {"text": 3.0, "picture": 30.0}
    w.mix = {"a": (2, 0), "b": (1, 2)}
    w.pending = [api.Job("b", "process")]
    running = Progress()
    running.started = time.monotonic() - 2  # two seconds in, no pace of its own yet
    w.running = {"a": running}
    w.batch = {"a", "b"}
    assert w._guess_left("a", running) == 4  # 2 text pages x 3 s, 2 s gone
    assert w._batch_left() == 4 + 63  # then b: 1 x 3 s + 2 x 30 s
    assert w.progress_of("a")["eta"] == 4
    w.lanes = 3  # what waits is shared between the lanes
    assert w._batch_left() == 4 + 21
    w.per_page = 10.0  # measured on documents of yours: that wins
    assert w._batch_left() == 4 + round(w.pages.get("b", 1) * 10.0 / 3)
