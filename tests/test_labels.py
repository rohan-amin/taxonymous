"""The placeholders are what an AI reads in place of who you are, so they must be right:
one label per identity (no merging two people, no splitting one), the same label in every
document and in scans, and a label that says who the person is on the return (<TAXPAYER>,
<SPOUSE_SSN>, <DEPENDENT_2>, <SUCCESSOR_TRUSTEE_1>), each of their details under it."""

import json
import re

import pymupdf
from fastapi.testclient import TestClient

from taxonymous.api import TOKEN_HEADER, create_app
from taxonymous.detect import ProfileMatcher
from taxonymous.pipeline import Workspace
from taxonymous.profile import Profile
from taxonymous.redact import redact_file
from taxonymous.vault import Vault

ROLES = {"ea0000001": "taxpayer", "eb0000002": "spouse", "ec0000003": "dependent", "ed0000004": "dependent",
         "ee0000005": "successor_trustee",
         "preparer:ef0000006": "preparer"}  # ids as profile.new_id makes them


def test_a_persons_label_is_their_role_in_every_kind_whoever_is_seen_first():
    v = Vault()
    v.roles = dict(ROLES)
    assert v.placeholder("SSN", "ssn:eb0000002", "987-65-4324") == "<SPOUSE_SSN>"  # seen first
    assert v.placeholder("NAME", "person:ec0000003", "Anneliese") == "<DEPENDENT_1>"
    assert v.placeholder("DOB", "dob:ed0000004", "01/02/2015") == "<DEPENDENT_2_DOB>"
    assert v.placeholder("NAME", "person:ea0000001", "Avery") == "<TAXPAYER>"
    assert v.placeholder("SSN", "ssn:ea0000001", "987-65-4323") == "<TAXPAYER_SSN>"
    assert v.placeholder("ORG", "employer:ea0000001", "Contoso") == "<TAXPAYER_EMPLOYER>"
    assert v.placeholder("NAME", "person:ee0000005", "Jordan") == "<SUCCESSOR_TRUSTEE_1>"
    assert v.placeholder("NAME", "preparer:ef0000006:name", "Okafor") == "<PREPARER_1>"
    assert v.placeholder("ID", "preparer:ef0000006:ptin", "P01234567") == "<PREPARER_1_PTIN>"
    # not the profile's: an SSN shape, a family surname alone; everything else is numbered
    assert v.placeholder("SSN", "ssn:987654320", "987-65-4320") == "<UNKNOWN_SSN_1>"
    assert v.placeholder("NAME", "surname:abernathy", "Abernathy") == "<SURNAME_1>"
    assert v.placeholder("ACCT", "acct:e00000001", "0047812") == "<ACCT_1>"


def test_a_role_change_relabels_that_person_and_never_hands_out_a_number_twice():
    v = Vault()
    v.roles = dict(ROLES)
    v.placeholder("NAME", "person:ec0000003", "Anneliese")
    v.placeholder("SSN", "ssn:ec0000003", "987-65-4325")
    v.roles["ec0000003"] = "beneficiary"  # was a dependent
    assert v.relabel() is True and v.relabel() is False
    assert {v.by_key["person:ec0000003"], v.by_key["ssn:ec0000003"]} == \
        {"<BENEFICIARY_1>", "<BENEFICIARY_1_SSN>"}
    assert v.entries["<BENEFICIARY_1_SSN>"]["values"] == ["987-65-4325"]  # the values come along
    assert v.placeholder("NAME", "person:ed0000004", "Morgan") == "<DEPENDENT_2>"  # DEPENDENT_1 was someone's


def test_an_older_vault_gets_role_labels_with_its_values(tmp_path):
    path = tmp_path / "placeholders.json"
    old = {"<NAME_A>": {"kind": "NAME", "key": "person:0", "values": ["Avery F Abernathy"]},
           "<SSN_A>": {"kind": "SSN", "key": "ssn:0", "values": ["987-65-4323"]},
           "<NAME_B>": {"kind": "NAME", "key": "person:1", "values": ["Quinn L Abernathy"]},
           "<SSN_C>": {"kind": "SSN", "key": "ssn:987654320", "values": ["987-65-4320"]},
           "<NAME_D>": {"kind": "NAME", "key": "surname:abernathy", "values": ["Abernathy"]},
           "<ACCT_1>": {"kind": "ACCT", "key": "acct:0", "values": ["0047812"]}}
    path.write_text(json.dumps({"placeholders": old}))
    v = Vault(path)
    v.roles = {"0": "taxpayer", "1": "spouse"}
    assert v.relabel() is True
    saved = json.loads(path.read_text())["placeholders"]
    assert {ph: e["key"] for ph, e in saved.items()} == {
        "<TAXPAYER>": "person:0", "<TAXPAYER_SSN>": "ssn:0", "<SPOUSE>": "person:1",
        "<UNKNOWN_SSN_1>": "ssn:987654320", "<SURNAME_1>": "surname:abernathy", "<ACCT_1>": "acct:0"}
    assert saved["<TAXPAYER_SSN>"]["values"] == ["987-65-4323"]


def test_one_person_however_their_name_is_printed_is_one_label(corpus, tmp_path):
    """Every way a form prints a name comes out as that person's one label; two people
    with one surname stay two."""
    _, _, profile = corpus
    forms = ["Avery F Abernathy", "AVERY F ABERNATHY", "ABERNATHY, AVERY F", "Abernathy, Avery F.",
             "Avery F. Abernathy", "Recipient: Avery Abernathy", "Quinn L Abernathy",
             "Avery", "Abernathy, Quinn L"]  # a name ending a line, the spouse's starting the next
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    for i, text in enumerate(forms):
        page.insert_text((20, 30 + 40 * i), text, fontsize=11)
    doc.save(tmp_path / "names.pdf")
    vault = Vault(tmp_path / "vault.json")
    matcher = ProfileMatcher(Profile.from_dict(profile))
    result = redact_file(tmp_path / "names.pdf", tmp_path / "out", matcher, vault)
    lines = [" ".join(ln.split()) for ln in result.text.read_text().splitlines() if ln.strip()]
    lines = lines[2:]  # past the header
    # the same person on lines one after another may come out as one box: still one label
    assert set(lines[:lines.index("Recipient: <TAXPAYER>")]) == {"<TAXPAYER>"}, lines
    after = lines[lines.index("Recipient: <TAXPAYER>") + 1:]
    assert after == ["<SPOUSE>", "<TAXPAYER>", "<SPOUSE>"], lines  # the spouse never takes Avery's label
    keys = [e["key"] for e in vault.entries.values()]
    assert set(keys) == {"person:0", "person:1"} and len(keys) == 2


def test_every_document_and_scan_shares_one_set_of_labels(corpus, tmp_path):
    """Text documents and scans through one vault: each label stands for one identity,
    each identity has one label, the people's labels say their roles, and a scan uses the
    same labels as the text version of the same form."""
    src, _, profile = corpus
    matcher = ProfileMatcher(Profile.from_dict(profile))
    vault = Vault(tmp_path / "vault" / "placeholders.json")
    names = ["w2.pdf", "w2_scan.pdf", "1099_int.pdf", "1099_int_scan.pdf", "k1_statement.pdf",
             "k1_statement_scan.pdf", "receipt_photo.jpg", "brokerage_consolidated.pdf"]
    used = {n: set(redact_file(src / n, tmp_path / "out", matcher, vault).placeholders) for n in names}
    entries = json.loads((tmp_path / "vault" / "placeholders.json").read_text())["placeholders"]
    keys = [e["key"] for e in entries.values()]
    assert len(keys) == len(set(keys))  # no identity split across two labels, none merged into one
    roles = [p["role"] for p in profile["people"]]
    assert roles == ["taxpayer", "spouse", "dependent"]  # the test household
    base = {"0": "TAXPAYER", "1": "SPOUSE", "2": "DEPENDENT_1"}
    for ph, e in entries.items():
        if m := re.fullmatch(r"(person|ssn):(\d)", e["key"]):
            assert ph == f"<{base[m.group(2)]}{'_SSN' if m.group(1) == 'ssn' else ''}>", (ph, e["key"])
    for text, scan in [("w2.pdf", "w2_scan.pdf"), ("1099_int.pdf", "1099_int_scan.pdf"),
                       ("k1_statement.pdf", "k1_statement_scan.pdf")]:
        people = {ph for ph in used[text] if re.fullmatch(r"<(TAXPAYER|SPOUSE|DEPENDENT_\d)(_SSN)?>", ph)}
        assert people and people <= used[scan], (text, people - used[scan])


def test_labels_follow_people_through_edits_to_the_profile(corpus, tmp_path):
    """Reorder the people, remove one, add another, change a role: everyone keeps their
    label but the one whose role changed, and a removed person's number is never reused."""
    _, _, profile = corpus
    ws = Workspace(tmp_path / "ws")
    ws.init()
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=300)
    names = ["Avery F Abernathy", "Quinn L Abernathy", "Anneliese P Abernathy", "Morgan Delacroix"]
    for i, name in enumerate(names):
        page.insert_text((20, 30 + 30 * i), name, fontsize=11)
    doc.save(ws.inbox / "family.pdf")
    app = create_app(ws, "t", ocr=False)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"})

    def lines() -> list[str]:
        app.state.worker.wait()
        text = ws.outputs("family")["txt"].read_text()
        return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()][2:]

    people = [{k: p[k] for k in ("name", "ssn", "role")} for p in profile["people"]]
    assert client.put("/api/profile", json={"people": people}).status_code == 200
    client.post("/api/run")
    assert lines() == ["<TAXPAYER>", "<SPOUSE>", "<DEPENDENT_1>", "Morgan Delacroix"]

    saved = client.get("/api/profile").json()["profile"]["people"]  # with their ids, as the form keeps them
    avery, quinn, _ = saved
    morgan = {"name": "Morgan Delacroix", "role": "dependent"}  # new: no id yet
    # saving cleans the cleaned documents again by itself
    assert client.put("/api/profile", json={"people": [quinn, avery, morgan]}).status_code == 200
    # Avery and Quinn keep their labels though their places swapped. Anneliese is no longer
    # in the profile: her first names stay readable, the family's surname alone is still
    # removed. Morgan doesn't get her retired DEPENDENT_1.
    assert lines() == ["<TAXPAYER>", "<SPOUSE>", "Anneliese P <SURNAME_1>", "<DEPENDENT_2>"]

    # a role changed: saving relabels that person (the form sends everyone with their ids)
    quinn, avery, morgan = client.get("/api/profile").json()["profile"]["people"]
    quinn["role"] = "trustee"
    assert client.put("/api/profile", json={"people": [quinn, avery, morgan]}).status_code == 200
    assert lines() == ["<TAXPAYER>", "<TRUSTEE_1>", "Anneliese P <SURNAME_1>", "<DEPENDENT_2>"]
    # one taxpayer, one spouse
    r = client.put("/api/profile", json={"people": [avery, {**quinn, "role": "taxpayer"}]})
    assert r.status_code == 422 and "people.1.role" in r.json()["detail"]["fields"]


def test_accounts_and_addresses_say_what_they_are(corpus, tmp_path):
    """An account's label is its owner and type, an address's its type; the state stays
    readable in an address; clearing a type makes it a plain number again."""
    _, _, profile = corpus
    ws = Workspace(tmp_path / "ws")
    ws.init()
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=300)
    for i, line in enumerate(["Roth IRA 2287441903", "Checking 8320968305 routing 021000021",
                              "5842 Fenwick Ave", "Tacoma, WA 98458"]):
        page.insert_text((20, 30 + 30 * i), line, fontsize=11)
    doc.save(ws.inbox / "accounts.pdf")
    app = create_app(ws, "t", ocr=False)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"})
    you = {"id": "ea0000001", "name": "Avery F Abernathy", "role": "taxpayer"}
    accounts = [{"number": "2287441903", "type": "roth_ira", "owner": you["id"]},
                {"number": "8320968305", "routing": "021000021", "type": "checking", "owner": "joint"}]
    home = {"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA", "zip": "98458", "type": "home"}
    r = client.put("/api/profile", json={"people": [you], "accounts": accounts, "addresses": [home]})
    assert r.status_code == 200
    client.post("/api/run")
    app.state.worker.wait()

    def lines() -> list[str]:
        text = ws.outputs("accounts")["txt"].read_text()
        return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()][2:]

    def labels(line: str) -> list[str]:  # what a line says, label by label (the text puts them in place)
        return sorted(re.findall(r"<[A-Z0-9_]+>|\bWA\b", line))

    got = lines()
    assert [labels(ln) for ln in got] == [["<TAXPAYER_ROTH_IRA_1>"],
                                         ["<JOINT_CHECKING_1>", "<JOINT_CHECKING_1_ROUTING>"],
                                         ["<HOME_ADDR>"], ["<HOME_ADDR>", "<HOME_ADDR>", "WA"]], got
    assert not any(v in " ".join(got) for v in ("2287441903", "8320968305", "021000021", "Tacoma", "98458"))
    saved = client.get("/api/profile").json()["profile"]
    saved["accounts"][1]["type"], saved["accounts"][1]["owner"] = "", ""  # cleared: a plain number again
    assert client.put("/api/profile", json=saved).status_code == 200
    app.state.worker.wait()
    assert labels(lines()[1]) == ["<ACCT_1>", "<ROUTING_1>"]


def test_two_addresses_in_one_town_keep_their_own_labels(tmp_path):
    """A city and ZIP two addresses share go with the street printed before them (they all
    went to the first address listed), and a label reads in its place on its line."""
    profile = {"people": [{"name": "Avery F Abernathy", "role": "taxpayer"}],
               "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA", "zip": "98458"},
                             {"street": "12 Harbor Rd", "city": "Seaside", "state": "OR", "zip": "97138"},
                             {"street": "77 Pine St", "city": "Tacoma", "state": "WA", "zip": "98458"}]}
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=300)
    for i, line in enumerate(["5842 Fenwick Ave", "Tacoma, WA 98458", "12 Harbor Rd", "Seaside, OR 97138",
                              "Mailing address 77 Pine St", "Tacoma, WA 98458"]):
        page.insert_text((20, 30 + 30 * i), line, fontsize=11)
    doc.save(tmp_path / "homes.pdf")
    result = redact_file(tmp_path / "homes.pdf", tmp_path / "out", ProfileMatcher(Profile.from_dict(profile)),
                         Vault(tmp_path / "vault.json"))
    lines = [" ".join(ln.split()) for ln in result.text.read_text().splitlines() if ln.strip()][2:]
    assert lines == ["<ADDR_1>", "<ADDR_1> , WA <ADDR_1>", "<ADDR_2>", "<ADDR_2> , OR <ADDR_2>",
                     "Mailing address <ADDR_3>", "<ADDR_3> , WA <ADDR_3>"]


def test_one_label_for_a_value_covered_by_two_touching_boxes():
    """A second look that blacks out the edge of the digits under the first box adds a box,
    not a second label; the same value elsewhere, or another value touching it, keeps its own."""
    import pymupdf

    from taxonymous.redact import _add_label

    labels = []
    _add_label(labels, pymupdf.Rect(100, 100, 200, 112), "<TAXPAYER_SSN>")
    _add_label(labels, pymupdf.Rect(98, 111, 200, 117), "<TAXPAYER_SSN>")   # the bottom of the digits
    _add_label(labels, pymupdf.Rect(100, 113, 200, 118), "<TAXPAYER_SSN>")  # 1 pt below: still that value
    assert [ph for _, ph in labels] == ["<TAXPAYER_SSN>"]
    _add_label(labels, pymupdf.Rect(100, 160, 200, 172), "<TAXPAYER_SSN>")  # printed again, lower down
    _add_label(labels, pymupdf.Rect(100, 126, 200, 138), "<SPOUSE_SSN>")    # someone else's, right under it
    _add_label(labels, pymupdf.Rect(203, 100, 300, 112), "<TAXPAYER_SSN>")  # beside it, not over it
    assert [ph for _, ph in labels] == ["<TAXPAYER_SSN>", "<TAXPAYER_SSN>", "<SPOUSE_SSN>", "<TAXPAYER_SSN>"]
