"""Removing a document while it's being cleaned: the clean stops where it is, nothing of it
is written after, its files go, and the documents after it are cleaned as usual."""

import json
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient

from taxonymous.api import TOKEN_HEADER, Job, create_app
from taxonymous.pipeline import Workspace

TOKEN = "t"
SLOW = "brokerage_consolidated"  # long enough to be caught in the middle


@pytest.fixture
def setup(corpus, tmp_path):
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    for name in (f"{SLOW}.pdf", "w2.pdf"):
        shutil.copy(src / name, ws.inbox / name)
    return ws


def _removed_mid_clean(ws: Workspace, lanes: int) -> float:
    app = create_app(ws, TOKEN, lanes=lanes)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    worker = app.state.worker
    worker.submit(Job(SLOW, "process"))
    worker.submit(Job("w2", "process"))
    deadline = time.monotonic() + 60
    while (worker.progress_of(SLOW) or {}).get("stage") is None:  # under way
        assert time.monotonic() < deadline
        time.sleep(0.02)
    start = time.monotonic()
    assert client.delete(f"/api/documents/{SLOW}").status_code == 200
    took = time.monotonic() - start
    worker.wait(timeout=300)
    # nothing of it: no copy, no outputs, no half-written file, no error, not in the list
    assert not any(p.exists() for p in ws.outputs(SLOW).values())
    assert not [p for p in ws.work.rglob("*") if SLOW in p.name]
    assert SLOW not in worker.errors
    docs = client.get("/api/documents").json()
    assert [(d["id"], d["status"]) for d in docs] == [("w2", "passed")]  # the next one, cleaned as usual
    return took


def test_removed_while_cleaned_in_this_process(setup):
    """One lane (in the app's own process): the job stops at its next step."""
    assert _removed_mid_clean(setup, lanes=1) < 10


def test_removed_while_cleaned_in_a_lane_of_its_own(setup):
    """Lanes as helper processes: the helper is ended at once, the next job starts a new one."""
    assert _removed_mid_clean(setup, lanes=2) < 5


def test_a_waiting_document_is_dropped_from_the_queue(setup, monkeypatch):
    """Removed before its turn: it never starts, and the batch no longer counts it."""
    from taxonymous import lanes as lanes_mod
    go = threading.Event()
    cleaned = []
    real = lanes_mod.process

    def held(src, *args, **kwargs):
        cleaned.append(src.stem)
        assert go.wait(30)
        return real(src, *args, **kwargs)

    monkeypatch.setattr(lanes_mod, "process", held)
    app = create_app(setup, TOKEN, lanes=1)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    worker = app.state.worker
    worker.submit(Job("w2", "process"))
    worker.submit(Job(SLOW, "process"))
    while "w2" not in worker.running:
        time.sleep(0.01)
    assert client.delete(f"/api/documents/{SLOW}").status_code == 200
    assert worker.progress_of("w2")["jobs"] == 1
    go.set()
    worker.wait(timeout=120)
    assert cleaned == ["w2"]
