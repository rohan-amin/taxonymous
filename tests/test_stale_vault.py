"""The vault remembers every value ever redacted. Verification of a document looks for its
own redactions' values, not every value the vault remembers: one no redaction uses any more
must not fail a page it's on. Also what counts as a leak at all (captions, dates, prose)."""

import json

import pymupdf
import pytest

from taxonymous.detect import ProfileMatcher
from taxonymous.pipeline import Workspace, process, reverify
from taxonymous.profile import Profile
from taxonymous.vault import Vault

FOOTER = "This information is being furnished to the Internal Revenue Service."


def pdf(path, *lines):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 90 + 16 * i), line, fontsize=10)
    doc.save(path)


@pytest.fixture
def ws(tmp_path):
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery F Abernathy", "ssn": "987-65-4323"}]}))
    pdf(ws.inbox / "w2.pdf", "Employee: Avery F Abernathy", "Wages 1,234.56", FOOTER)
    return ws


def run(ws, name):
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    return process(ws.inbox / name, ws, matcher, Vault(ws.vault_dir / "placeholders.json"), ocr=False,
                   release=False)


def remember(ws, kind, key, value):
    vault = Vault(ws.vault_dir / "placeholders.json")
    vault.placeholder(kind, key, value)
    vault.save()


def test_a_value_no_document_redacts_any_more_is_not_a_leak(ws):
    remember(ws, "NAME", "name:this information is", "This information is")  # an earlier run's mistake
    outcome = run(ws, "w2.pdf")
    assert outcome.report.passed, outcome.report.issues
    assert "This information is" in pymupdf.open(ws.outputs("w2")["pdf"])[0].get_text()
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    assert reverify(ws, "w2", matcher, Vault(ws.vault_dir / "placeholders.json"), ocr=False).passed


def test_forgetting_an_entry_never_hands_out_a_name_still_in_use():
    vault = Vault()
    a, b, c = (vault.placeholder("REDACTED", f"manual:{i}", f"box {i}") for i in range(3))
    vault.forget("manual:0")
    d = vault.placeholder("REDACTED", "manual:3", "box 3")
    assert d not in {b, c} and vault.entries[c]["values"] == ["box 2"]


def test_dates_part_headings_and_omb_numbers_do_not_identify_anyone():
    from taxonymous.detect import identifies, is_caption
    assert not identifies("ID", "12-31-2025") and not identifies("NAME", "Part II")
    assert not identifies("ID", "1545-0715") and identifies("DOB", "04/12/1981")
    assert identifies("ID", "6123450") and identifies("NAME", "Avery F Abernathy")
    assert is_caption("Married Filing Jointly") and is_caption("Filing")


def test_a_name_in_a_sentence_is_words_not_the_name():
    """Names are capitalized: "filing" in "the due date for filing your return" isn't a
    person called Filing. Read in a PDF's raw content too."""
    from taxonymous.detect import in_prose
    assert in_prose("the due date for filing your return", 17, 23)
    assert in_prose("BT (the due date for) Tj [(filing)-250(your)] TJ", 27, 33)
    assert not in_prose("Filing your return", 0, 6) and not in_prose("quinn delacroix-marsh", 0, 21)


def test_1099_captions_and_a_copy_heading_are_not_names():
    """Two real mistakes: "Capital gain distributions" read as a person, and "Reference" in a
    W-2's "Employee Reference Copy" heading taken for the employee's name."""
    from taxonymous.detect import is_caption
    for caption in ("Capital", "gain", "Distributions", "Total capital gain distributions",
                    "Unrecaptured Sec. 1250 gain", "Reference", "Qualified dividends",
                    "Nondividend distributions", "Section 199A dividends"):
        assert is_caption(caption), caption
    for name in ("Avery F Abernathy", "Johnny Cash", "Amy Long", "Quinn Bond"):
        assert not is_caption(name), name


def test_explain_shows_which_object_a_raw_leak_is_in(ws):
    """A value left in the file's objects but not on any page: explain names the object."""
    from taxonymous.explain import explain, render
    assert run(ws, "w2.pdf").report.passed
    out = ws.outputs("w2")["pdf"]
    doc = pymupdf.open(out)
    left = doc.get_new_xref()
    doc.update_object(left, "<</Type/XObject/Subtype/Form/BBox[0 0 10 10]>>")
    doc.update_stream(left, b"BT /F1 9 Tf (Avery F Abernathy) Tj ET")
    doc.save(out.with_suffix(".tmp"))
    out.with_suffix(".tmp").replace(out)
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    report = reverify(ws, "w2", matcher, Vault(ws.vault_dir / "placeholders.json"), ocr=False)
    assert ("raw", "NAME") in {(i.check, i.kind) for i in report.issues}
    issues = json.loads(ws.outputs("w2")["report"].read_text())["issues"]
    text = render(explain(ws.root, "w2", ocr=False), issues)
    assert f"object {left} /XObject /Form, its stream: not used by any page" in text, text
    assert "Abernathy" not in text


def test_a_dashed_date_is_not_a_long_number_id(ws):
    pdf(ws.inbox / "k1.pdf", "Employee: Avery F Abernathy", "Tax year ending 12-31-2025")
    assert run(ws, "k1.pdf").report.passed
    assert "12-31-2025" in pymupdf.open(ws.outputs("k1")["pdf"])[0].get_text()


def test_your_name_in_the_file_name_fails_nothing_and_isnt_exported(ws):
    """"30 - Fidelity Avery Abernathy Trust 1099.pdf": the text file used to start with the
    file name, so every such document failed; and the export carried the name to Claude."""
    from taxonymous.pipeline import export
    name = "30 - Fidelity Avery F Abernathy Trust 1099.pdf"
    pdf(ws.inbox / name, "Account holder: Avery F Abernathy", "Dividends 1,234.56")
    outcome = run(ws, name)
    assert outcome.report.passed, outcome.report.issues
    stem = name[:-4]
    assert "Abernathy" not in ws.outputs(stem)["txt"].read_text()
    (ws.ready / f"{stem}.redacted.pdf").write_bytes(b"an export under the old name")
    released = export(ws, stem)
    assert [p.name for p in released] == ["30 - Fidelity NAME Trust 1099.redacted.pdf"]
    assert [p.name for p in ws.ready.iterdir()] == ["30 - Fidelity NAME Trust 1099.redacted.pdf"]
