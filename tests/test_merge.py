"""Merging a folder's passed documents into one PDF (or parts under a size limit)."""

import json
import shutil

import pymupdf
import pytest
from fastapi.testclient import TestClient

from taxonymous.api import TOKEN_HEADER, create_app
from taxonymous.cli import main as cli
from taxonymous.pipeline import Workspace

TOKEN = "test-token"


@pytest.fixture(scope="module")
def server(corpus, tmp_path_factory):
    src, _, profile = corpus
    ws = Workspace(tmp_path_factory.mktemp("mergews"))
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    last = profile["people"][0]["name"].split()[-1]
    for rel, name in [("2024/1099_int.pdf", "1099_int.pdf"), (f"2024/{last} W-2.pdf", "w2.pdf"),
                      ("2024/brokerage/1099_div.pdf", "1099_div.pdf"), ("2023/w2.pdf", "w2.pdf")]:
        (ws.inbox / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / name, ws.inbox / rel)
    app = create_app(ws, TOKEN, ocr=False)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    client.post("/api/run", params={"folder": "2024"})
    app.state.worker.wait()
    return client, app, ws, profile


def merge(client, app, folder: str, max_mb=None) -> dict:
    assert client.post("/api/merge", params={"folder": folder}, json={"max_mb": max_mb}).status_code == 202
    app.state.worker.wait()
    state = client.get("/api/merge", params={"folder": folder}).json()
    assert state["state"] is None and state["error"] is None
    return state["last"]


def pages_of(ws, stem) -> int:
    return pymupdf.open(ws.outputs(stem)["pdf"]).page_count


def test_one_file_with_a_contents_page_and_nothing_else(server):
    client, app, ws, profile = server
    last = merge(client, app, "2024")
    assert last["passed"] and last["files"] == ["2024/2024 merged.pdf"] and last["skipped"] == []
    doc = pymupdf.open(ws.ready / last["files"][0])
    last_name = profile["people"][0]["name"].split()[-1]
    stems = ["2024/brokerage/1099_div", "2024/1099_int", f"2024/{last_name} W-2"]
    assert last["parts"][0]["documents"] == stems
    assert doc.page_count == 1 + sum(pages_of(ws, s) for s in stems)
    contents = doc[0].get_text()
    n_div, n_int = pages_of(ws, stems[0]), pages_of(ws, stems[1])
    assert f"brokerage / 1099_div\npages 2-{1 + n_div}" in contents
    assert f"1099_int\npages {2 + n_div}-{1 + n_div + n_int}" in contents
    assert "NAME W-2" in contents  # its public name: your name is not on the contents page
    assert profile["people"][0]["name"].split()[-1] not in contents
    assert doc.get_toc() == [] and not any(doc.metadata.get(k) for k in ("title", "author", "producer"))
    # its pages are the redacted documents' pages, as they are
    first = pymupdf.open(ws.outputs(stems[0])["pdf"])
    assert doc[1].get_text() == first[0].get_text()


def test_split_under_a_size_limit_never_splits_a_document(server):
    client, app, ws, _ = server
    sizes = [ws.outputs(s)["pdf"].stat().st_size for s in ("2024/brokerage/1099_div", "2024/1099_int")]
    limit = (sum(sizes) + 30_000) / 1_000_000
    last = merge(client, app, "2024", max_mb=limit)
    assert last["passed"]
    assert last["files"] == ["2024/2024 merged (part 1 of 2).pdf", "2024/2024 merged (part 2 of 2).pdf"]
    assert [len(p["documents"]) for p in last["parts"]] == [2, 1]
    assert not (ws.ready / "2024" / "2024 merged.pdf").exists()  # the merge before is replaced
    assert "Contents (part 2 of 2)" in pymupdf.open(ws.ready / last["files"][1])[0].get_text()
    tiny = merge(client, app, "2024", max_mb=0.1)
    assert len(tiny["parts"]) == 3 and tiny["too_big"]  # one document per part, some still over


def test_only_documents_that_passed_go_in(server):
    client, app, ws, _ = server
    last = merge(client, app, "2023")
    assert not last["passed"] and last["files"] == [] and last["skipped"] == ["2023/w2"]
    assert not (ws.ready / "2023").exists()


def test_a_merged_file_that_shows_your_details_goes_nowhere(server):
    client, app, ws, profile = server
    stem = "2024/1099_int"
    pdf = ws.outputs(stem)["pdf"]
    kept = pdf.read_bytes()
    try:
        doc = pymupdf.open(pdf)
        doc[0].insert_text((72, 72), profile["people"][0]["name"], fontsize=9)
        doc.save(pdf.with_suffix(".tmp"))
        pdf.with_suffix(".tmp").replace(pdf)
        last = merge(client, app, "2024")
        assert not last["passed"] and last["files"] == []
        assert any(i["kind"] == "NAME" for p in last["parts"] for i in p["issues"])
        assert not list((ws.ready / "2024").glob("*merged*"))
    finally:
        pdf.write_bytes(kept)


def test_a_box_you_left_readable_stays_readable_where_it_moved(server):
    client, app, ws, profile = server
    w2 = next(s for s in (ws.stem(p) for p in ws.pending()) if s.endswith("W-2"))
    detail = client.get(f"/api/documents/{w2}").json()
    box = next(r for r in detail["regions"] if r["kind"] == "NAME" and r["source"] != "manual")
    r = client.post(f"/api/documents/{w2}/shown", json={"page": box["page"], "rect": box["rect"]})
    assert r.status_code == 202
    app.state.worker.wait()
    assert client.get(f"/api/documents/{w2}").json()["status"] == "passed"
    last = merge(client, app, "2024")
    assert last["passed"], last["parts"]
    client.delete(f"/api/documents/{w2}/shown/0")
    app.state.worker.wait()


def test_merge_asks_for_a_real_folder_and_size(server):
    client, _, _, _ = server
    assert client.post("/api/merge", params={"folder": "2031"}, json={}).status_code == 404
    assert client.post("/api/merge", params={"folder": ".."}, json={}).status_code == 400
    assert client.post("/api/merge", params={"folder": "2024"}, json={"max_mb": 0}).status_code == 400


def test_merge_from_the_command_line(server, capsys):
    _, _, ws, _ = server
    assert cli(["merge", "--workspace", str(ws.root), "2024", "--no-ocr"]) == 0
    assert "ready-to-upload/2024/2024 merged.pdf" in capsys.readouterr().out
    assert cli(["merge", "--workspace", str(ws.root), "2023", "--no-ocr"]) == 1
    assert cli(["merge", "--workspace", str(ws.root), "nope"]) == 2
