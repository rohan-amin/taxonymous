"""Folders in inbox/ (one per tax year, say): each document is known by its path, its
outputs and export keep the folders, and the API lists, runs and takes uploads by folder."""

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from taxonymous.api import TOKEN_HEADER, create_app
from taxonymous.cli import main as cli
from taxonymous.pipeline import Workspace

TOKEN = "test-token"


@pytest.fixture(scope="module")
def server(corpus, tmp_path_factory):
    src, _, profile = corpus
    ws = Workspace(tmp_path_factory.mktemp("ws"))
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    for rel in ["2023/w2.pdf", "2024/w2.pdf", "2024/brokerage/text.pdf", "1099_int.pdf"]:
        (ws.inbox / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / ("1099_int.pdf" if rel.endswith("text.pdf") else rel.rsplit("/", 1)[-1]),
                        ws.inbox / rel)
    (ws.inbox / ".hidden").mkdir()
    shutil.copyfile(src / "w2.pdf", ws.inbox / ".hidden" / "w2.pdf")
    shutil.copyfile(src / "w2.pdf", ws.inbox / "2024" / ".w2.pdf.part")  # an upload being written
    app = create_app(ws, TOKEN, ocr=False)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    return client, app, ws, src, profile


def test_documents_in_folders_are_listed_by_path(server):
    client, _, ws, _, _ = server
    docs = client.get("/api/documents").json()
    assert [(d["id"], d["folder"], d["name"]) for d in docs] == [
        ("1099_int", "", "1099_int.pdf"),
        ("2023/w2", "2023", "w2.pdf"),
        ("2024/brokerage/text", "2024/brokerage", "text.pdf"),
        ("2024/w2", "2024", "w2.pdf"),
    ]


def test_run_one_folder_runs_it_and_the_folders_inside(server):
    client, app, ws, _, _ = server
    r = client.post("/api/run", params={"folder": "2024"})
    assert sorted(r.json()["queued"]) == ["2024/brokerage/text", "2024/w2"]
    app.state.worker.wait()
    status = {d["id"]: d["status"] for d in client.get("/api/documents").json()}
    assert status == {"1099_int": "new", "2023/w2": "new",
                      "2024/brokerage/text": "passed", "2024/w2": "passed"}
    assert (ws.work / "2024" / "w2.redacted.pdf").exists()
    assert (ws.work / "2024" / "brokerage" / "text.redacted.pdf").exists()
    assert not (ws.work / "w2.redacted.pdf").exists()


def test_two_years_of_the_same_file_stay_apart(server):
    client, app, ws, _, _ = server
    client.post("/api/run")
    app.state.worker.wait()
    w23, w24 = (client.get(f"/api/documents/{y}/w2").json() for y in ("2023", "2024"))
    assert w23["id"] == "2023/w2" and w23["status"] == "passed" and w24["status"] == "passed"
    # a box drawn on one year's W-2 is on that one only
    rect = [300.0, 700.0, 360.0, 712.0]
    assert client.post("/api/documents/2023/w2/redactions",
                       json={"boxes": [{"page": 0, "rect": rect}]}).status_code == 202
    app.state.worker.wait()
    w23, w24 = (client.get(f"/api/documents/{y}/w2").json() for y in ("2023", "2024"))
    assert w23["added"] == [{"page": 0, "rect": rect}] and w24["added"] == []
    drawn = [r for r in w23["regions"] if r["source"] == "manual"]
    assert drawn and not [r for r in w24["regions"] if r["source"] == "manual"]
    assert (ws.vault_dir / "overrides" / "2023" / "w2.json").exists()
    # and removing it again forgets that box alone
    assert client.delete("/api/documents/2023/w2/redactions",
                         params={"placeholder": drawn[0]["placeholder"]}).status_code == 202
    app.state.worker.wait()
    assert client.get("/api/documents/2023/w2").json()["added"] == []


def test_every_route_takes_a_path_id(server):
    client, app, _, _, _ = server
    assert client.get("/api/documents/2024/w2/pages/0/original.png").status_code == 200
    assert client.get("/api/documents/2024/w2/pages/0/redacted.png").status_code == 200
    assert "[redacted by taxonymous]" in client.get("/api/documents/2024/w2/text").text
    # a document named "text" in a folder is still that document
    assert client.get("/api/documents/2024/brokerage/text").json()["id"] == "2024/brokerage/text"
    assert client.get("/api/documents/2024/brokerage/text/text").status_code == 200
    assert client.post("/api/documents/2024/w2/verify").status_code == 202
    app.state.worker.wait()
    for bad in ("/api/documents/2024", "/api/documents/2025/w2", "/api/documents/..%2Fvault%2Fprofile",
                "/api/documents/.hidden/w2"):
        assert client.get(bad).status_code == 404, bad


def test_export_keeps_the_folders(server):
    client, _, ws, _, _ = server
    assert client.post("/api/documents/2023/w2/export").json()["released"] == ["w2.redacted.pdf"]
    assert client.post("/api/documents/2024/w2/export").status_code == 200
    assert (ws.ready / "2023" / "w2.redacted.pdf").exists()
    assert (ws.ready / "2024" / "w2.redacted.pdf").exists()
    assert {d["id"]: d["exported"] for d in client.get("/api/documents").json()}["2024/w2"]


def test_a_folder_named_after_you_is_renamed_in_ready_to_upload(server):
    client, app, ws, src, profile = server
    last = profile["people"][0]["name"].split()[-1]
    (ws.inbox / last).mkdir()
    shutil.copyfile(src / "w2.pdf", ws.inbox / last / "w2.pdf")
    client.post(f"/api/documents/{last}/w2/run")
    app.state.worker.wait()
    assert client.post(f"/api/documents/{last}/w2/export").status_code == 200
    assert (ws.ready / "NAME" / "w2.redacted.pdf").exists()
    assert not (ws.ready / last).exists()


def test_upload_into_a_folder(server):
    client, _, ws, src, _ = server
    with open(src / "1099_div.pdf", "rb") as f:
        r = client.post("/api/documents", files={"file": ("1099_div.pdf", f)}, data={"folder": "2022/broker"})
    assert r.status_code == 201 and r.json()["id"] == "2022/broker/1099_div"
    assert (ws.inbox / "2022" / "broker" / "1099_div.pdf").exists()
    with open(src / "1099_div.pdf", "rb") as f:
        again = client.post("/api/documents", files={"file": ("1099_div.pdf", f)},
                            data={"folder": "2022/broker"})
    assert again.status_code == 409
    for bad in ("..", "2022/../..", ".hidden", "a//b"):
        with open(src / "1099_div.pdf", "rb") as f:
            r = client.post("/api/documents", files={"file": ("x.pdf", f)}, data={"folder": bad})
        assert r.status_code == 400, bad
    assert client.post("/api/run", params={"folder": "../x"}).status_code == 400


def test_explain_asks_which_folder_when_a_name_is_in_two(server, capsys):
    _, _, ws, _, _ = server
    assert cli(["explain", "--workspace", str(ws.root), "w2.pdf"]) == 2
    assert "2023/w2, 2024/w2" in capsys.readouterr().err
    assert cli(["explain", "--workspace", str(ws.root), "2024/w2.pdf", "--no-ocr"]) == 0
    assert cli(["explain", "--workspace", str(ws.root), "2024/w2", "--no-ocr"]) == 0
