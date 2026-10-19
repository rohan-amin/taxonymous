"""The progress bar: the engine reports pages as it finishes them, stage by stage, and the
API puts the running job's progress (and its place in a Run all) on its document."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from taxonymous import api as api_mod
from taxonymous.api import TOKEN_HEADER, create_app
from taxonymous.detect import ProfileMatcher
from taxonymous.pipeline import Workspace, process, reverify
from taxonymous.profile import Profile
from taxonymous.progress import Progress
from taxonymous.vault import Vault


class Recorder(Progress):
    def __init__(self):
        super().__init__()
        self.log: list[tuple[str, int, int]] = []

    def start(self, stage, total):
        super().start(stage, total)
        self.log.append((stage, 0, total))

    def step(self, n=1):
        super().step(n)
        self.log.append((self.stage, self.done, self.total))


def stages(log):
    """Each stage in order, with every (done, total) it reported."""
    out: list[tuple[str, list[tuple[int, int]]]] = []
    for stage, done, total in log:
        if done == 0:
            out.append((stage, []))
        out[-1][1].append((done, total))
    return out


@pytest.fixture
def ws(corpus, tmp_path):
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    return ws, src


@pytest.mark.parametrize("name", ["w2.pdf", "1099_int_scan.pdf", "receipt_photo.jpg"])
def test_every_stage_counts_up_to_its_last_page(ws, name):
    ws, src = ws
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    rec = Recorder()
    outcome = process(src / name, ws, matcher, Vault(ws.vault_dir / "placeholders.json"), release=False,
                      progress=rec)
    pages = outcome.report.pages
    seen = stages(rec.log)
    assert [s for s, _ in seen] == ["reading", "redacting", "checking"]
    for stage, steps in seen:
        assert all(total == pages for _, total in steps), stage
        done = [d for d, _ in steps]
        assert done == sorted(done) and done[-1] == pages, (stage, done)  # only up, and all the way


def test_reverify_reports_only_checking(ws):
    ws, src = ws
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    process(src / "w2.pdf", ws, matcher, vault, release=False)
    rec = Recorder()
    reverify(ws, "w2", matcher, vault, progress=rec)
    assert [s for s, _ in stages(rec.log)] == ["checking"]


def test_the_running_document_carries_its_progress(ws, monkeypatch):
    ws, src = ws
    for name in ("w2.pdf", "k1_statement.pdf"):
        (ws.inbox / name).write_bytes((src / name).read_bytes())
    started = [threading.Event(), threading.Event()]
    go = [threading.Event(), threading.Event()]
    calls = []

    def slow(src, ws, matcher, vault, ocr=True, release=True, progress=None):
        n = len(calls)
        calls.append(src.name)
        progress.start("reading", 4)
        progress.step(3)
        started[n].set()
        assert go[n].wait(30)

    monkeypatch.setattr(api_mod, "process", slow)
    app = create_app(ws, "t")
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"})
    assert client.post("/api/run").status_code == 202
    assert started[0].wait(30)
    docs = {d["id"]: d for d in client.get("/api/documents").json()}
    running = next(d for d in docs.values() if d["status"] == "running")
    queued = next(d for d in docs.values() if d["status"] == "queued")
    assert running["progress"] == {"stage": "reading", "label": "Reading pages", "done": 3, "total": 4,
                                   "job": 1, "jobs": 2}
    assert queued["progress"] is None
    assert client.get(f"/api/documents/{running['id']}").json()["progress"]["done"] == 3

    go[0].set()
    assert started[1].wait(30)
    second = client.get(f"/api/documents/{queued['id']}").json()["progress"]
    assert (second["job"], second["jobs"]) == (2, 2)
    go[1].set()
    app.state.worker.wait()
    assert all(d["progress"] is None for d in client.get("/api/documents").json())
