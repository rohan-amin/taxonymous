"""M5 acceptance: the local API, with every outbound connection blocked."""

import json
import shutil
import socket
from collections import Counter

import pymupdf
import pytest
from fastapi.testclient import TestClient

from taxonymous import api as api_mod
from taxonymous.api import TOKEN_HEADER, check_loopback, create_app, serve
from taxonymous.pipeline import Workspace
from taxonymous.redact import Overrides

TOKEN = "test-token"
K1, W2 = "/api/documents/k1_statement", "/api/documents/w2"
FILES = ["k1_statement.pdf", "w2.pdf", "1099_int_scan.pdf", "receipt_photo.jpg"]


def boxes(page: int, rect) -> dict:
    return {"boxes": [{"page": page, "rect": list(rect)}]}


@pytest.fixture(scope="module")
def network_attempts():
    """Any socket connection or name lookup from here on is refused and recorded."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args[1:] if args and isinstance(args[0], socket.socket) else args)
        raise OSError("network access is disabled in this test")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", refuse)
        mp.setattr(socket.socket, "connect_ex", refuse)
        mp.setattr(socket, "create_connection", refuse)
        mp.setattr(socket, "getaddrinfo", refuse)
        yield attempts


@pytest.fixture(scope="module")
def server(corpus, network_attempts, tmp_path_factory):
    """A workspace with a few documents uploaded through the API and processed."""
    src, manifest, profile = corpus
    ws = Workspace(tmp_path_factory.mktemp("ws"))
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    app = create_app(ws, TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    for name in FILES:
        with open(src / name, "rb") as f:
            assert client.post("/api/documents", files={"file": (name, f)}).status_code == 201
    assert client.post("/api/run").status_code == 202
    app.state.worker.wait()
    docs = {d["file"]: d for d in manifest["documents"]}
    return client, app, ws, src, docs


def stem_of(name: str) -> str:
    return name.removesuffix(".pdf")


def settle(app) -> None:
    app.state.worker.wait()


# ---------------------------------------------------------------- binding and access

@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "example.com", ""])
def test_refuses_to_bind_anything_but_loopback(host, monkeypatch):
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("server started"))
    with pytest.raises(ValueError):
        check_loopback(host)
    with pytest.raises(ValueError):
        serve(Workspace("/nonexistent"), host=host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_binds_loopback(host, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, **k: started.append(host))
    serve(Workspace(tmp_path), host=host, token=TOKEN)
    assert started == [host]


def test_requests_need_the_token(server):
    client, *_ = server
    assert client.get("/api/documents", headers={TOKEN_HEADER: ""}).status_code == 401
    assert client.get("/api/documents", headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert client.get("/api/documents").status_code == 200


def test_requests_for_other_host_names_are_refused(server):
    """A page that rebinds its own domain to 127.0.0.1 still sends its own Host header."""
    client, *_ = server
    assert client.get("/api/documents", headers={"Host": "evil.example"}).status_code == 400


# ---------------------------------------------------------------- upload, run, status

def test_uploaded_documents_are_processed(server):
    client, *_ = server
    docs = client.get("/api/documents").json()
    assert sorted(d["name"] for d in docs) == sorted(FILES)
    assert {d["status"] for d in docs} == {"passed"}, docs
    assert not any(d["exported"] for d in docs)  # nothing is released until export
    assert client.get("/api/status").json() == {"running": None, "queued": [], "idle": True}


def test_upload_rules(server, tmp_path):
    client, _, ws, *_ = server
    assert client.post("/api/documents", files={"file": ("notes.txt", b"hi")}).status_code == 400
    assert client.post("/api/documents", files={"file": ("w2.pdf", b"%PDF")}).status_code == 409
    assert client.post("/api/documents", files={"file": (".hidden.pdf", b"%PDF")}).status_code == 400
    blank = pymupdf.open()
    blank.new_page()
    r = client.post("/api/documents", files={"file": ("../../escape.pdf", blank.tobytes())})
    assert r.status_code == 201 and (ws.inbox / "escape.pdf").exists()  # the name only, in the inbox
    (ws.inbox / "escape.pdf").unlink()


def test_document_detail_has_boxes_but_no_values(server):
    client, _, _, _, docs = server
    detail = client.get(K1).json()
    assert detail["pages"] and detail["regions"] and detail["report"]["passed"]
    body = json.dumps(detail)
    assert not [i["value"] for i in docs["k1_statement.pdf"]["redact"] if i["value"] in body]
    assert {"page", "rect", "placeholder", "kind", "source"} <= set(detail["regions"][0])


def test_unknown_documents_and_pages(server):
    client, *_ = server
    assert client.get("/api/documents/nope").status_code == 404
    assert client.get(f"{W2}/pages/99/redacted.png").status_code == 404
    assert client.get(f"{W2}/pages/0/sideways.png").status_code == 404


@pytest.mark.parametrize("stem", ["w2", "1099_int_scan", "receipt_photo.jpg"])
def test_page_images(server, stem):
    client, *_ = server
    detail = client.get(f"/api/documents/{stem}").json()
    for which in ("original", "redacted"):
        r = client.get(f"/api/documents/{stem}/pages/0/{which}.png", params={"dpi": 72})
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        pix = pymupdf.Pixmap(r.content)
        width, height = detail["pages"][0]
        assert abs(pix.width - width) <= 2 and abs(pix.height - height) <= 2  # same geometry, both sides


def test_image_urls_can_carry_the_token(server):
    """<img> tags can't send headers, so GETs take ?token= too."""
    client, *_ = server
    r = client.get(f"{W2}/pages/0/redacted.png", params={"token": TOKEN},
                   headers={TOKEN_HEADER: ""})
    assert r.status_code == 200


def test_text(server):
    client, *_ = server
    assert "<SSN_A>" in client.get(f"{W2}/text").text


# ---------------------------------------------------------------- reviewer decisions

def _amount_box(src, name: str, value: str) -> list[float]:
    [rect] = pymupdf.open(src / name)[0].search_for(value)
    return [rect.x0 - 1, rect.y0 - 1, rect.x1 + 1, rect.y1 + 1]


def test_add_and_remove_a_box(server):
    client, app, ws, src, docs = server
    amount = next(i["value"] for i in docs["k1_statement.pdf"]["keep"] if i["type"] == "AMOUNT")
    box = _amount_box(src, "k1_statement.pdf", amount)

    assert client.post(f"{K1}/redactions", json=boxes(0, box)).status_code == 202
    settle(app)
    detail = client.get(K1).json()
    [drawn] = [r for r in detail["regions"] if r["source"] == "manual"]
    assert drawn["kind"] == "REDACTED" and detail["status"] == "passed"
    assert amount not in client.get(f"{K1}/text").text
    assert amount not in pymupdf.open(ws.outputs("k1_statement")["pdf"])[0].get_text()

    r = client.delete(f"{K1}/redactions", params={"placeholder": drawn["placeholder"]})
    assert r.status_code == 202
    settle(app)
    detail = client.get(K1).json()
    assert not [r for r in detail["regions"] if r["source"] == "manual"] and detail["added"] == []
    assert amount in client.get(f"{K1}/text").text
    assert detail["status"] == "passed"  # the value under the removed box is no longer "known"


def test_a_box_on_a_scan_is_burned_into_the_pixels(server):
    client, app, ws, *_ = server
    stem = "1099_int_scan"
    width, height = client.get(f"/api/documents/{stem}").json()["pages"][0]
    box = [width * 0.55, height * 0.05, width * 0.95, height * 0.25]
    assert client.post(f"/api/documents/{stem}/redactions", json=boxes(0, box)).status_code == 202
    settle(app)
    detail = client.get(f"/api/documents/{stem}").json()
    assert detail["status"] == "passed" and any(r["source"] == "manual" for r in detail["regions"])
    page = pymupdf.open(ws.outputs(stem)["pdf"])[0]
    pix = page.get_pixmap(dpi=36, clip=pymupdf.Rect(box) + (4, 4, -4, -4))
    dark = sum(v < 40 for v in pix.samples[::pix.n]) / (pix.width * pix.height)
    assert dark > 0.95  # solid black, not text on paper
    ph = next(r["placeholder"] for r in detail["regions"] if r["source"] == "manual")
    client.delete(f"/api/documents/{stem}/redactions", params={"placeholder": ph})
    settle(app)


def test_keep_a_detection_in_the_clear_and_undo_it(server):
    client, app, ws, src, docs = server
    detail = client.get(K1).json()
    name = next(i["value"] for i in docs["k1_statement.pdf"]["redact"]
                if i["type"] == "NAME" and i["third_party"])
    vault = json.loads((ws.vault_dir / "placeholders.json").read_text())["placeholders"]
    ph = next(p for p, e in vault.items() if name in e["values"])
    assert ph in {r["placeholder"] for r in detail["regions"]}

    assert client.delete(f"{K1}/redactions", params={"placeholder": ph}).status_code == 202
    settle(app)
    detail = client.get(K1).json()
    assert detail["kept"] == [ph] and detail["status"] == "passed"  # the reviewer's call, not a leak
    assert name in client.get(f"{K1}/text").text

    assert client.delete(f"{K1}/kept", params={"placeholder": ph}).status_code == 202
    settle(app)
    detail = client.get(K1).json()
    assert detail["kept"] == [] and name not in client.get(f"{K1}/text").text


def _repeated_box(client, doc: str) -> tuple[str, list[dict]]:
    regions = [r for r in client.get(doc).json()["regions"] if r["source"] != "manual"]
    counts = Counter(r["placeholder"] for r in regions)
    ph = next(p for p, n in counts.most_common() if n >= 3)
    return ph, [r for r in regions if r["placeholder"] == ph]


def test_show_a_single_box_and_redact_it_again(server):
    client, app, ws, *_ = server
    ph, before = _repeated_box(client, K1)
    one = before[0]
    assert client.post(f"{K1}/shown", json={"page": one["page"], "rect": one["rect"]}).status_code == 202
    settle(app)
    detail = client.get(K1).json()
    assert detail["status"] == "passed" and detail["kept"] == []
    assert detail["shown"] == [{"page": one["page"], "rect": one["rect"], "placeholder": ph}]
    after = [r for r in detail["regions"] if r["placeholder"] == ph]
    assert len(after) == len(before) - 1  # only that box: the others stay redacted
    page = pymupdf.open(ws.outputs("k1_statement")["pdf"])[one["page"]]
    assert page.get_textbox(pymupdf.Rect(one["rect"])).strip() not in ("", ph)  # readable there

    assert client.delete(f"{K1}/shown/0").status_code == 202
    settle(app)
    detail = client.get(K1).json()
    assert detail["shown"] == [] and detail["status"] == "passed"
    assert len([r for r in detail["regions"] if r["placeholder"] == ph]) == len(before)


def test_a_shown_box_does_not_excuse_the_same_value_elsewhere(server):
    """Two copies left visible, but only one of them recorded as the reviewer's choice:
    verification must still find the other."""
    client, app, ws, *_ = server
    ph, before = _repeated_box(client, K1)
    for r in before[:2]:
        client.post(f"{K1}/shown", json={"page": r["page"], "rect": r["rect"]})
        settle(app)
    assert client.get(K1).json()["status"] == "passed"
    o = Overrides.load(ws.overrides_path("k1_statement"))
    o.show = o.show[:1]
    o.save(ws.overrides_path("k1_statement"))
    client.post(f"{K1}/verify")
    settle(app)
    detail = client.get(K1).json()
    assert detail["status"] == "failed"
    assert {i["check"] for i in detail["report"]["issues"]} >= {"text", "txt"}
    client.delete(f"{K1}/shown/0")
    settle(app)
    assert client.get(K1).json()["status"] == "passed"


def test_bad_reviewer_requests(server):
    client, *_ = server
    assert client.post(f"{W2}/shown", json={"page": 0, "rect": [1, 1, 2, 2]}).status_code == 404
    assert client.delete(f"{W2}/shown/5").status_code == 404
    assert client.post(f"{W2}/redactions", json=boxes(7, [0, 0, 9, 9])).status_code == 400
    assert client.post(f"{W2}/redactions", json=boxes(0, [9, 9, 0, 0])).status_code == 400
    assert client.delete(f"{W2}/redactions", params={"placeholder": "<NAME_ZZ>"}).status_code == 404
    assert client.delete(f"{W2}/kept", params={"placeholder": "<NAME_A>"}).status_code == 404


# ---------------------------------------------------------------- verify and export

def test_export_releases_only_a_passing_document(server):
    client, app, ws, src, _ = server
    r = client.post(f"{W2}/export")
    assert r.status_code == 200 and sorted(r.json()["released"]) == ["w2.redacted.pdf"]
    assert (ws.ready / "w2.redacted.pdf").exists()

    # the output is swapped for the original behind the API's back: re-verify catches it
    shutil.copyfile(src / "1099_int_scan.pdf", ws.outputs("1099_int_scan")["pdf"])
    assert client.post("/api/documents/1099_int_scan/verify").status_code == 202
    settle(app)
    assert client.get("/api/documents/1099_int_scan").json()["status"] == "failed"
    r = client.post("/api/documents/1099_int_scan/export")
    assert r.status_code == 409 and not (ws.ready / "1099_int_scan.redacted.pdf").exists()

    # running it again repairs it
    client.post("/api/documents/1099_int_scan/run")
    settle(app)
    assert client.post("/api/documents/1099_int_scan/export").status_code == 200


def test_an_unreadable_file_is_an_error_and_cannot_be_exported(server):
    client, app, *_ = server
    locked = pymupdf.open()
    locked.new_page()
    data = locked.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    assert client.post("/api/documents", files={"file": ("locked.pdf", data)}).status_code == 201
    client.post("/api/documents/locked/run")
    settle(app)
    assert client.get("/api/documents/locked").json()["status"] == "error"
    assert client.post("/api/documents/locked/export").status_code == 409


def test_a_change_is_not_exportable_until_it_has_run(server, monkeypatch):
    client, app, *_ = server
    hold = api_mod.threading.Event()
    real = app.state.worker._run
    monkeypatch.setattr(app.state.worker, "_run", lambda job: (hold.wait(30), real(job)))
    client.post(f"{W2}/run")
    assert client.post(f"{W2}/export").status_code == 409  # queued or running
    hold.set()
    settle(app)
    assert client.post(f"{W2}/export").status_code == 200


# ---------------------------------------------------------------- your details

@pytest.fixture
def fresh(network_attempts, tmp_path):
    """A workspace that has never been set up: no profile yet."""
    ws = Workspace(tmp_path / "fresh")
    app = create_app(ws, TOKEN, ocr=False)
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN}), ws


FORM = {
    "people": [{"name": " Avery  F Abernathy ", "ssn": "987654323", "dob": "4/7/1984", "role": "taxpayer"},
               {"name": "", "ssn": "", "dob": "", "role": "spouse"}],  # an empty row
    "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "wa", "zip": "98458"}],
    "phones": ["(555) 555-0188", ""],
    "emails": ["avery.abernathy@example.com"],
    "eins": [{"ein": "004869454", "description": "Household employer"}],
    "accounts": [{"institution": "Contoso Bank", "number": "8320968305", "name": "Joint checking"},
                 {"institution": "", "number": "", "name": ""}],  # an empty row
    "cards": [{"number": "4111111111111111", "description": "Travel card"}],
}


def test_details_start_empty_and_running_waits_for_them(fresh):
    client, ws = fresh
    r = client.get("/api/profile").json()
    assert r["exists"] is False and r["profile"]["people"] == []
    assert client.post("/api/run").status_code == 409


def test_details_are_cleaned_saved_and_private(fresh):
    client, ws = fresh
    r = client.put("/api/profile", json=FORM)
    assert r.status_code == 200
    saved = json.loads(ws.profile_path.read_text())
    assert saved["people"] == [{"name": "Avery F Abernathy", "ssn": "987-65-4323", "dob": "04/07/1984",
                                "role": "taxpayer", "dod": "", "deceased": False}]
    assert saved["addresses"][0]["state"] == "WA"
    assert saved["eins"] == [{"ein": "00-4869454", "description": "Household employer"}]
    assert saved["accounts"] == [{"institution": "Contoso Bank", "number": "8320968305",
                                  "name": "Joint checking"}]
    assert saved["cards"] == [{"number": "4111 1111 1111 1111", "description": "Travel card"}]
    assert saved["phones"] == ["(555) 555-0188"]
    assert ws.profile_path.stat().st_mode & 0o777 == 0o600
    assert client.get("/api/profile").json()["profile"] == saved
    assert client.post("/api/run").status_code == 202


def test_bad_details_name_the_fields_and_save_nothing(fresh):
    client, ws = fresh
    bad = {**FORM, "people": [{"name": "Avery", "ssn": "987-65-432", "dob": "13/40/1984"}],
           "addresses": [{"street": "", "city": "Tacoma", "state": "Wash", "zip": "984"}],
           "eins": [{"ein": "12345", "description": "x"}], "phones": ["", "555-0188"],
           "accounts": [{"institution": "Contoso Bank", "number": "", "name": ""}]}
    r = client.put("/api/profile", json=bad)
    assert r.status_code == 422
    fields = r.json()["detail"]["fields"]
    assert set(fields) == {"people.0.name", "people.0.ssn", "people.0.dob", "addresses.0.street",
                           "addresses.0.state", "addresses.0.zip", "eins.0.ein", "phones.1",
                           "accounts.0.number"}
    assert not any("987" in m or "Avery" in m for m in fields.values())  # messages never echo values
    assert not ws.profile_path.exists()
    assert client.put("/api/profile", json={}).status_code == 422  # at least one person


def test_a_person_can_be_marked_deceased_with_a_date_of_death(fresh):
    client, ws = fresh
    spouse = {"name": "Quinn L Abernathy", "dob": "9/24/1976", "role": "spouse", "deceased": True}
    def with_spouse(**extra):
        return {**FORM, "people": [FORM["people"][0], {**spouse, **extra}]}

    r = client.put("/api/profile", json=with_spouse())
    assert r.status_code == 422 and set(r.json()["detail"]["fields"]) == {"people.1.dod"}  # it's required
    for dod, problem in [("3/14/2999", "future"), ("3/14/1970", "before the date of birth")]:
        r = client.put("/api/profile", json=with_spouse(dod=dod))
        assert problem in r.json()["detail"]["fields"]["people.1.dod"]
    r = client.put("/api/profile", json=with_spouse(dod="3/14/2025"))
    assert r.status_code == 200
    people = json.loads(ws.profile_path.read_text())["people"]
    assert people[1]["deceased"] is True and people[1]["dod"] == "03/14/2025"
    assert people[0]["deceased"] is False and people[0]["dod"] == ""


def test_an_older_profile_with_plain_numbers_still_loads(fresh):
    client, ws = fresh
    ws.profile_path.parent.mkdir(parents=True, exist_ok=True)
    older = {"people": [{"name": "Avery F Abernathy"}], "accounts": ["8320968305"],
             "eins": ["00-4869454"], "cards": ["4111111111111111"]}
    ws.profile_path.write_text(json.dumps(older))
    p = client.get("/api/profile").json()["profile"]
    assert p["accounts"] == [{"institution": "", "number": "8320968305", "name": ""}]
    assert p["eins"] == [{"ein": "00-4869454", "description": ""}]
    assert p["cards"] == [{"number": "4111 1111 1111 1111", "description": ""}]


def test_details_need_the_token(fresh):
    client, _ = fresh
    assert client.get("/api/profile", headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert client.put("/api/profile", json=FORM, headers={TOKEN_HEADER: "wrong"}).status_code == 401


def test_nothing_tried_to_reach_the_network(server, network_attempts):
    assert network_attempts == []
