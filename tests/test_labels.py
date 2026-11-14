"""The placeholders are what an AI reads in place of who you are, so they must be right:
one label per identity (no merging two people, no splitting one), the same label in every
document and in scans, and a person's letter the same in every kind (<NAME_B> and <SSN_B>
are the same person)."""

import json
import re

import pymupdf

from taxonymous.detect import ProfileMatcher
from taxonymous.profile import Profile
from taxonymous.redact import redact_file
from taxonymous.vault import Vault


def test_a_persons_letter_is_theirs_in_every_kind_whoever_is_seen_first():
    v = Vault()
    v.people, v.letters = 3, {"ea1": 0, "eb2": 1, "ec3": 2}  # each person's letter, by their permanent id
    assert v.placeholder("SSN", "ssn:eb2", "987-65-4324") == "<SSN_B>"  # the spouse's SSN, seen first
    assert v.placeholder("NAME", "person:ec3", "Anneliese") == "<NAME_C>"
    assert v.placeholder("NAME", "person:ea1", "Avery") == "<NAME_A>"
    assert v.placeholder("SSN", "ssn:ea1", "987-65-4323") == "<SSN_A>"
    # anyone not in the profile comes after its people, never taking one of their letters
    assert v.placeholder("SSN", "ssn:987654320", "987-65-4320") == "<SSN_D>"
    assert v.placeholder("NAME", "preparer:0:name", "Jordan Okafor") == "<NAME_D>"
    assert v.placeholder("SSN", "ssn:ec3", "987-65-4325") == "<SSN_C>"
    assert v.placeholder("ACCT", "acct:4", "0047812") == "<ACCT_1>"  # numbered kinds just count


def test_an_older_vault_keeps_its_names_and_never_hands_one_out_twice():
    v = Vault()
    v.placeholder("SSN", "ssn:987654320", "987-65-4320")  # named before letters followed people: <SSN_A>
    v.people, v.letters = 2, {"0": 0, "1": 1}
    assert v.placeholder("SSN", "ssn:0", "987-65-4323") == "<SSN_C>"  # A is taken; B is the spouse's
    assert v.placeholder("SSN", "ssn:987654320", "987-65-4320") == "<SSN_A>"  # unchanged


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
    assert set(lines[:lines.index("Recipient: <NAME_A>")]) == {"<NAME_A>"}, lines
    after = lines[lines.index("Recipient: <NAME_A>") + 1:]
    assert after == ["<NAME_B>", "<NAME_A>", "<NAME_B>"], lines  # the spouse never takes Avery's label
    keys = [e["key"] for e in vault.entries.values()]
    assert set(keys) == {"person:0", "person:1"} and len(keys) == 2


def test_every_document_and_scan_shares_one_set_of_labels(corpus, tmp_path):
    """Text documents and scans through one vault: each label stands for one identity,
    each identity has one label, the people's letters follow the profile, and a scan uses
    the same labels as the text version of the same form."""
    src, _, profile = corpus
    matcher = ProfileMatcher(Profile.from_dict(profile))
    vault = Vault(tmp_path / "vault" / "placeholders.json")
    names = ["w2.pdf", "w2_scan.pdf", "1099_int.pdf", "1099_int_scan.pdf", "k1_statement.pdf",
             "k1_statement_scan.pdf", "receipt_photo.jpg", "brokerage_consolidated.pdf"]
    used = {n: set(redact_file(src / n, tmp_path / "out", matcher, vault).placeholders) for n in names}
    entries = json.loads((tmp_path / "vault" / "placeholders.json").read_text())["placeholders"]
    keys = [e["key"] for e in entries.values()]
    assert len(keys) == len(set(keys))  # no identity split across two labels, none merged into one
    for ph, e in entries.items():
        m = re.fullmatch(r"(?:person|ssn):(\d)", e["key"])
        if m and e["kind"] in ("NAME", "SSN"):
            assert ph == f"<{e['kind']}_{'ABC'[int(m.group(1))]}>", (ph, e["key"])
    for text, scan in [("w2.pdf", "w2_scan.pdf"), ("1099_int.pdf", "1099_int_scan.pdf"),
                       ("k1_statement.pdf", "k1_statement_scan.pdf")]:
        people = {ph for ph in used[text] if re.fullmatch(r"<(NAME|SSN)_[A-C]>", ph)}
        assert people and people <= used[scan], (text, people - used[scan])


def test_labels_follow_people_through_edits_to_the_profile(corpus, tmp_path):
    """Reorder the people, remove one and add another: everyone keeps their label (they
    used to follow the place in the list), and a removed person's letter is never reused."""
    import shutil

    from fastapi.testclient import TestClient

    from taxonymous.api import TOKEN_HEADER, create_app
    from taxonymous.pipeline import Workspace
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

    def run() -> list[str]:
        client.post("/api/run")
        app.state.worker.wait()
        text = ws.outputs("family")["txt"].read_text()
        return [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()][2:]

    people = [{k: p[k] for k in ("name", "ssn", "role")} for p in profile["people"]]
    assert client.put("/api/profile", json={"people": people}).status_code == 200
    assert run() == ["<NAME_A>", "<NAME_B>", "<NAME_C>", "Morgan Delacroix"]

    saved = client.get("/api/profile").json()["profile"]["people"]  # with their ids, as the form keeps them
    avery, quinn, _ = saved
    morgan = {"name": "Morgan Delacroix", "role": "dependent"}  # new: no id yet
    assert client.put("/api/profile", json={"people": [quinn, avery, morgan]}).status_code == 200
    shutil.rmtree(ws.work)  # cleaned afresh, from the vault alone
    ws.work.mkdir()
    # Avery and Quinn keep A and B though their places swapped. Anneliese is no longer in the
    # profile: her first names stay readable, the family's surname alone is still removed
    # (its own label). Morgan doesn't get her retired C.
    lines = run()
    assert lines[:2] == ["<NAME_A>", "<NAME_B>"] and lines[2].startswith("Anneliese P <NAME_")
    assert lines[3] not in ("<NAME_A>", "<NAME_B>", "<NAME_C>", lines[2][len("Anneliese P "):])
