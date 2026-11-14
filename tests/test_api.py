"""M5 acceptance: the local API, with every outbound connection blocked."""

import json
import re
import shutil
import socket
from collections import Counter
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from taxonymous import api as api_mod
from taxonymous.api import TOKEN_HEADER, check_loopback, create_app, serve
from taxonymous.pipeline import Workspace
from taxonymous.redact import Overrides


def without_ids(profile: dict) -> dict:
    """A saved profile with its entries' permanent ids checked (one each, well formed,
    no two alike) and then left out, to compare the rest."""
    ids = [r["id"] for key in ("people", "addresses", "accounts", "eins", "cards", "numbers", "preparers")
           for r in profile.get(key, [])]
    assert all(re.fullmatch(r"\d{1,3}|e[0-9a-f]{8}", i) for i in ids) and len(ids) == len(set(ids)), ids
    return {k: [{f: v for f, v in r.items() if f != "id"} if isinstance(r, dict) else r for r in rows]
            if isinstance(rows, list) else rows for k, rows in profile.items()}


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
    assert client.get("/api/status").json() == {"running": [], "queued": [], "idle": True, "lanes": 1}


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
    assert "<TAXPAYER_SSN>" in client.get(f"{W2}/text").text


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
    ph, _ = _repeated_box(client, W2, "NAME")
    values = json.loads((ws.vault_dir / "placeholders.json").read_text())["placeholders"][ph]["values"]

    assert client.delete(f"{W2}/redactions", params={"placeholder": ph}).status_code == 202
    settle(app)
    detail = client.get(W2).json()
    assert detail["kept"] == [ph] and detail["status"] == "passed", detail["report"]["issues"]
    assert readable(values, client.get(f"{W2}/text").text)

    assert client.delete(f"{W2}/kept", params={"placeholder": ph}).status_code == 202
    settle(app)
    detail = client.get(W2).json()
    assert detail["kept"] == [] and not readable(values, client.get(f"{W2}/text").text)


def readable(values: list[str], text: str) -> bool:
    flat = " ".join(text.split())
    return any(" ".join(v.split()) in flat for v in values)


def _repeated_box(client, doc: str, kind: str = "SSN") -> tuple[str, list[dict]]:
    regions = [r for r in client.get(doc).json()["regions"] if r["source"] != "manual" and r["kind"] == kind]
    counts = Counter(r["placeholder"] for r in regions)
    ph = next(p for p, n in counts.most_common() if n >= 3)
    return ph, [r for r in regions if r["placeholder"] == ph]


def test_show_a_single_box_and_redact_it_again(server):
    client, app, ws, *_ = server
    ph, before = _repeated_box(client, W2)
    one = before[0]
    assert client.post(f"{W2}/shown", json={"page": one["page"], "rect": one["rect"]}).status_code == 202
    settle(app)
    detail = client.get(W2).json()
    assert detail["status"] == "passed" and detail["kept"] == [], detail["report"]["issues"]
    assert detail["shown"] == [{"page": one["page"], "rect": one["rect"], "placeholder": ph}]
    after = [r for r in detail["regions"] if r["placeholder"] == ph]
    assert len(after) == len(before) - 1  # only that box: the others stay redacted
    page = pymupdf.open(ws.outputs("w2")["pdf"])[one["page"]]
    assert page.get_textbox(pymupdf.Rect(one["rect"])).strip() not in ("", ph)  # readable there

    assert client.delete(f"{W2}/shown/0").status_code == 202
    settle(app)
    detail = client.get(W2).json()
    assert detail["shown"] == [] and detail["status"] == "passed"
    assert len([r for r in detail["regions"] if r["placeholder"] == ph]) == len(before)


def test_a_shown_box_does_not_excuse_the_same_value_elsewhere(server):
    """Two copies left visible, but only one of them recorded as the reviewer's choice:
    verification must still find the other."""
    client, app, ws, *_ = server
    ph, before = _repeated_box(client, W2)
    for r in before[:2]:
        client.post(f"{W2}/shown", json={"page": r["page"], "rect": r["rect"]})
        settle(app)
    assert client.get(W2).json()["status"] == "passed"
    o = Overrides.load(ws.overrides_path("w2"))
    o.show = o.show[:1]
    o.save(ws.overrides_path("w2"))
    client.post(f"{W2}/verify")
    settle(app)
    detail = client.get(W2).json()
    assert detail["status"] == "failed"
    assert {i["check"] for i in detail["report"]["issues"]} >= {"text", "txt"}
    client.delete(f"{W2}/shown/0")
    settle(app)
    assert client.get(W2).json()["status"] == "passed"


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
    monkeypatch.setattr(app.state.worker, "_run", lambda *job: (hold.wait(30), real(*job)))
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
    "people": [{"name": " Avery  F Abernathy ", "ssn": "987654323", "dob": "4/7/1984", "role": "taxpayer",
                "occupation": " Software  engineer "},
               {"name": "", "ssn": "", "dob": "", "role": "spouse"}],  # an empty row
    "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "wa", "zip": "98458"}],
    "phones": ["(555) 555-0188", ""],
    "emails": ["avery.abernathy@example.com"],
    "eins": [{"ein": "004869454", "description": "Household employer"}],
    "accounts": [{"institution": "Contoso Bank", "number": "8320968305", "name": "Joint checking"},
                 {"institution": "", "number": "", "name": ""}],  # an empty row
    "cards": [{"number": "4111111111111111", "description": "Travel card"}],
    "numbers": [{"number": " 54321 ", "description": "Signature PIN"}],
    "preparers": [{"name": "Jordan Q Okafor", "firm": "Okafor & Co CPAs", "street": "100 Main St",
                   "city": "Seattle", "state": "wa", "zip": "98101", "phone": "5555550123",
                   "ptin": "p 0001 2345", "ein": "001234567"}],
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
    saved = without_ids(json.loads(ws.profile_path.read_text()))
    assert saved["people"] == [{"name": "Avery F Abernathy", "ssn": "987-65-4323", "dob": "04/07/1984",
                                "role": "taxpayer", "dod": "", "occupation": "Software engineer",
                                "employer": "", "deceased": False}]
    assert saved["addresses"][0]["state"] == "WA"
    assert saved["eins"] == [{"ein": "00-4869454", "description": "Household employer"}]
    assert saved["accounts"] == [{"institution": "Contoso Bank", "number": "8320968305", "routing": "",
                                  "name": "Joint checking", "type": "", "owner": ""}]
    assert saved["cards"] == [{"number": "4111 1111 1111 1111", "description": "Travel card"}]
    assert saved["phones"] == ["(555) 555-0188"]
    assert saved["numbers"] == [{"number": "54321", "description": "Signature PIN"}]
    assert saved["preparers"] == [{"name": "Jordan Q Okafor", "firm": "Okafor & Co CPAs",
                                   "street": "100 Main St", "city": "Seattle", "state": "WA", "zip": "98101",
                                   "phone": "(555) 555-0123", "ptin": "P00012345", "ein": "00-1234567"}]
    assert ws.profile_path.stat().st_mode & 0o777 == 0o600
    # read back as saved, the same ids included (none made up on the way)
    assert client.get("/api/profile").json()["profile"] == json.loads(ws.profile_path.read_text())
    assert client.post("/api/run").status_code == 202


def test_bad_details_name_the_fields_and_save_nothing(fresh):
    client, ws = fresh
    bad = {**FORM, "people": [{"name": "Avery", "ssn": "987-65-432", "dob": "13/40/1984"}],
           "addresses": [{"street": "", "city": "Tacoma", "state": "Wash", "zip": "984"}],
           "eins": [{"ein": "12345", "description": "x"}], "phones": ["", "555-0188"],
           "accounts": [{"institution": "Contoso Bank", "number": "", "name": ""},
                        {"number": "8320968305", "routing": "0210000"}],
           "numbers": [{"number": "12", "description": "PIN"}],
           "preparers": [{"street": "100 Main St", "ptin": "P123", "phone": "555-0123"}]}
    r = client.put("/api/profile", json=bad)
    assert r.status_code == 422
    fields = r.json()["detail"]["fields"]
    assert set(fields) == {"people.0.name", "people.0.ssn", "people.0.dob", "addresses.0.street",
                           "addresses.0.state", "addresses.0.zip", "eins.0.ein", "phones.1",
                           "accounts.0.number", "accounts.1.routing", "numbers.0.number",
                           "preparers.0.name", "preparers.0.ptin", "preparers.0.phone"}
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
    # each entry got its place as its permanent id: what the vault already knew it by
    assert p["accounts"] == [{"institution": "", "number": "8320968305", "routing": "", "name": "",
                              "type": "", "owner": "", "id": "0"}]
    assert p["eins"] == [{"ein": "00-4869454", "description": "", "id": "0"}]
    assert p["cards"] == [{"number": "4111 1111 1111 1111", "description": "", "id": "0"}]
    assert p["people"][0]["id"] == "0" and json.loads(ws.profile_path.read_text())["accounts"][0]["id"] == "0"


def test_details_need_the_token(fresh):
    client, _ = fresh
    assert client.get("/api/profile", headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert client.put("/api/profile", json=FORM, headers={TOKEN_HEADER: "wrong"}).status_code == 401


def test_nothing_tried_to_reach_the_network(server, network_attempts):
    assert network_attempts == []


def test_a_detail_seen_in_a_document_is_added_to_your_details(fresh):
    client, ws = fresh

    def add(kind, **item):
        return client.post("/api/profile/items", json={"kind": kind, "item": item})

    phone = {"kind": "phone", "item": {"value": "5555550199"}}
    assert client.post("/api/profile/items", json=phone).status_code == 409  # no details yet: yourself first
    assert client.put("/api/profile", json=FORM).status_code == 200
    person = {"name": " Morgan  Delacroix ", "ssn": "987654325", "dob": "", "role": "spouse"}
    r = add("person", **person)
    assert r.status_code == 200 and "Morgan" not in r.text  # real values stay out of responses
    account = {"institution": "Contoso Bank", "number": "0047812", "routing": "", "name": "Brokerage"}
    assert add("account", **account).status_code == 200
    assert add("phone", value="5555550199").status_code == 200
    saved = without_ids(json.loads(ws.profile_path.read_text()))
    assert saved["people"][-1] == {"name": "Morgan Delacroix", "ssn": "987-65-4325", "dob": "",
                                   "role": "spouse", "dod": "", "deceased": False, "occupation": "",
                                   "employer": ""}
    assert saved["accounts"][-1] == {**account, "type": "", "owner": ""}
    assert saved["phones"][-1] == "(555) 555-0199"
    assert len(saved["people"]) == 2 and len(saved["accounts"]) == 2

    r = add("ein", ein="12345", description="Trust")
    assert r.status_code == 422 and r.json()["detail"]["fields"] == {"ein": "An EIN has 9 digits."}
    r = add("person", name="Morgan", ssn="")
    assert r.json()["detail"]["fields"] == {"name": "Enter a first and last name."}
    r = add("card", number="", description="")
    assert r.status_code == 422 and r.json()["detail"]["message"] == "Fill in the details first."
    assert add("shoe size", v="9").status_code == 422
    assert len(json.loads(ws.profile_path.read_text())["eins"]) == 1  # nothing saved


def test_documents_cleaned_in_several_lanes_share_one_set_of_placeholders(corpus, tmp_path):
    """Two lanes, each its own process, clean documents at the same time: every document
    passes, and a value has one placeholder whichever lane named it first."""
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    names = ["w2.pdf", "1099_int.pdf", "k1_statement.pdf", "1099_r_scan.pdf", "receipt_photo.jpg"]
    for name in names:
        shutil.copy(src / name, ws.inbox / name)
    app = create_app(ws, TOKEN, lanes=2)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    assert client.get("/api/status").json()["lanes"] == 2
    assert client.post("/api/run").status_code == 202
    app.state.worker.wait()
    docs = client.get("/api/documents").json()
    assert {d["name"]: d["status"] for d in docs} == {name: "passed" for name in names}, docs
    entries = json.loads((ws.vault_dir / "placeholders.json").read_text())["placeholders"]
    keys = [e["key"] for e in entries.values()]
    assert len(keys) == len(set(keys))  # one placeholder per identity
    used = {r["placeholder"] for name in names
            for r in json.loads(ws.outputs(ws.stem(ws.inbox / name))["regions"].read_text())}
    assert used <= entries.keys()  # every box's placeholder is in the vault


def test_click_a_value_on_a_document_to_add_it_to_your_details(corpus, network_attempts, tmp_path):
    """No details yet: the reviewer clicks their name on a W-2, adds it (it's them), and the
    W-2 is cleaned; on a scan and a photo the words come from OCR."""
    src, _, _ = corpus
    ws = Workspace(tmp_path)
    ws.init()
    for name in ("w2.pdf", "1099_int_scan.pdf", "receipt_photo.jpg"):
        shutil.copy(src / name, ws.inbox / name)
    app = create_app(ws, TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})

    words = client.get(f"{W2}/pages/0/words").json()["words"]
    w, h = client.get(W2).json()["pages"][0]
    assert all(0 <= r[0] < r[2] <= w + 1 and 0 <= r[1] < r[3] <= h + 1 for r in (x["rect"] for x in words))
    avery = next(i for i, x in enumerate(words) if x["text"] == "Avery")
    s = client.post(f"{W2}/pages/0/suggest", json={"words": [avery]}).json()
    assert s["text"] == "Avery F Abernathy" and s["kind"] == "person" and s["item"]["role"] == "taxpayer"
    assert client.post(f"{W2}/pages/0/suggest", json={"words": []}).status_code == 422

    phone = {"kind": "phone", "item": {"value": "(555) 555-0100"}, "rerun": "w2"}
    assert client.post("/api/profile/items", json=phone).status_code == 409  # yourself first
    r = client.post("/api/profile/items", json={"kind": "person", "item": s["item"], "rerun": "w2"})
    assert r.status_code == 200
    # the W-2 first; the others had never been cleaned (no details), so they are now
    assert r.json()["queued"] == ["w2", "1099_int_scan", "receipt_photo.jpg"]
    assert json.loads(ws.profile_path.read_text())["people"][0]["role"] == "taxpayer"
    app.state.worker.wait()
    assert client.get(W2).json()["status"] == "passed"
    found = client.get("/api/profile/found").json()["entries"]  # where each profile entry was removed
    you = f"person:{json.loads(ws.profile_path.read_text())['people'][0]['id']}"  # by permanent id
    assert set(found) == {you}  # the W-2's SSN is SSN-shaped, not in the profile: no one's entry
    assert "w2" in [d["id"] for d in found[you]["documents"]] and found[you]["total"] > 0
    report = client.get(W2).json()["report"]  # what was checked, for "What was checked"
    assert len(report["engines"]) == report["pages"] and all(report["engines"])

    for doc, value in (("1099_int_scan", "555-0128"), ("receipt_photo.jpg", "555-0162")):
        words = client.get(f"/api/documents/{doc}/pages/0/words").json()["words"]
        i = next(i for i, x in enumerate(words) if x["text"] == value)
        s = client.post(f"/api/documents/{doc}/pages/0/suggest", json={"words": [i]}).json()
        assert s["kind"] == "phone" and s["text"] == f"(555) {value}"
    assert client.get(f"{W2}/pages/9/words").status_code == 404


def test_a_document_can_be_removed_entirely(corpus, network_attempts, tmp_path):
    """Removing a document takes everything Taxonymous holds for it: its copy, its cleaned
    files, the reviewer's boxes and its saved safe copy; nothing else."""
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    (ws.inbox / "2024").mkdir()
    for name in ("2024/w2.pdf", "k1_statement.pdf"):
        shutil.copy(src / Path(name).name, ws.inbox / name)
    app = create_app(ws, TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    client.post("/api/run")
    app.state.worker.wait()
    doc = "/api/documents/2024/w2"
    assert client.post(f"{doc}/redactions", json=boxes(0, (40, 40, 90, 60))).status_code == 202
    app.state.worker.wait()
    assert client.post(f"{doc}/export").status_code == 200
    held = [*ws.outputs("2024/w2").values(), ws.overrides_path("2024/w2"), *ws.released("2024/w2")]
    assert all(p.exists() for p in held if p.suffix != ".json" or "shown" not in p.name)

    assert client.delete(doc).status_code == 200
    assert not any(p.exists() for p in held) and not (ws.inbox / "2024").exists()
    assert [d["id"] for d in client.get("/api/documents").json()] == ["k1_statement"]  # the other stays
    assert ws.outputs("k1_statement")["pdf"].exists()
    assert client.delete(doc).status_code == 404


def test_quitting_while_cleaning_records_nothing_and_the_app_resumes_it(corpus, network_attempts, tmp_path):
    """The app quits while a lane cleans a document: the lane stops without writing a
    result (it used to record the document as failed: "This file couldn't be opened"). The
    next time the app opens, it cleans what was left unfinished, or ended in an error,
    and leaves what passed alone."""
    import subprocess
    import sys
    import time

    from taxonymous import lanes as lanes_mod
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    for name in ("brokerage_consolidated.pdf", "w2.pdf", "k1_statement.pdf"):
        shutil.copy(src / name, ws.inbox / name)

    lane = subprocess.Popen([sys.executable, "-m", "taxonymous.lanes", "--serve"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    job = {"root": str(ws.root), "action": "process", "stem": "brokerage_consolidated", "forget": [],
           "ocr": True}
    lane.stdin.write(json.dumps(job) + "\n")
    lane.stdin.flush()
    while not lane.stdout.readline().startswith(lanes_mod.MARK):  # it has started
        pass
    lane.stdout.close()  # the app is gone
    assert lane.wait(timeout=120) == 0
    assert not ws.outputs("brokerage_consolidated")["report"].exists()

    # one passed, one ended in an error, one never finished
    from taxonymous.api import unfinished
    app = create_app(ws, TOKEN)  # no resume: only the app does
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    client.post("/api/documents/w2/run")
    app.state.worker.wait()
    report = ws.outputs("k1_statement")["report"]
    report.write_text(json.dumps({"passed": False, "issues": [{"check": "error", "kind": "OSError"}]}))
    assert set(unfinished(ws)) == {"brokerage_consolidated", "k1_statement"}
    passed_at = ws.outputs("w2")["report"].stat().st_mtime_ns
    time.sleep(0.01)
    app = create_app(ws, TOKEN, resume=True)
    app.state.worker.wait()
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    assert {d["id"]: d["status"] for d in client.get("/api/documents").json()} == {
        "brokerage_consolidated": "passed", "k1_statement": "passed", "w2": "passed"}
    assert ws.outputs("w2")["report"].stat().st_mtime_ns == passed_at  # not cleaned again


def test_saving_an_unchanged_profile_cleans_nothing_again(corpus, network_attempts, tmp_path):
    src, _, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    shutil.copy(src / "w2.pdf", ws.inbox / "w2.pdf")
    app = create_app(ws, TOKEN, ocr=False)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: TOKEN})
    assert client.put("/api/profile", json=profile).json()["changed"] is True
    client.post("/api/run")
    app.state.worker.wait()
    saved = client.get("/api/profile").json()["profile"]
    file_at, report_at = ws.profile_path.stat().st_mtime_ns, ws.outputs("w2")["report"].stat().st_mtime_ns
    # as the form sends it back: the same details, typed differently, plus an empty row
    form = {**saved, "people": [{**p, "ssn": p["ssn"].replace("-", "")} for p in saved["people"]],
            "addresses": [*saved["addresses"], {"street": "", "city": "", "state": "", "zip": ""}]}
    r = client.put("/api/profile", json=form).json()
    assert r["changed"] is False and r["processed"] == []
    app.state.worker.wait()
    assert ws.profile_path.stat().st_mtime_ns == file_at  # not even written
    assert ws.outputs("w2")["report"].stat().st_mtime_ns == report_at  # not cleaned again
    # a real change is saved, and the cleaned document is cleaned again
    form["people"][0]["occupation"] = "Pilot"
    r = client.put("/api/profile", json=form).json()
    assert r["changed"] is True and r["processed"] == ["w2"]
    app.state.worker.wait()
    assert ws.outputs("w2")["report"].stat().st_mtime_ns != report_at
