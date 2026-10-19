"""The vault remembers every value ever redacted. Verification looks only for the ones some
document's latest redaction still uses: a detector mistake that has since been fixed
("This information is" taken for a name) must not fail every page it's on, while a value
another document redacts is still a leak here."""

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


def test_a_value_another_document_redacts_is_still_a_leak(ws):
    pdf(ws.inbox / "letter.pdf", "Preparer's name: Quinn Delacroix-Marsh")
    assert run(ws, "letter.pdf").report.passed
    assert {"Quinn Delacroix-Marsh"} <= {v for e in Vault(ws.vault_dir / "placeholders.json").entries.values()
                                         for v in e["values"]}
    assert run(ws, "w2.pdf").report.passed
    # the W-2's output somehow has that name in it: only the letter's redaction knows it
    pdf(ws.outputs("w2")["pdf"], "Employee: <NAME_A>", "Wages 1,234.56", "quinn delacroix-marsh")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    report = reverify(ws, "w2", matcher, Vault(ws.vault_dir / "placeholders.json"), ocr=False)
    assert {(i.check, i.kind) for i in report.issues} >= {("text", "NAME")}


def test_forgetting_an_entry_never_hands_out_a_name_still_in_use():
    vault = Vault()
    a, b, c = (vault.placeholder("REDACTED", f"manual:{i}", f"box {i}") for i in range(3))
    vault.forget("manual:0")
    d = vault.placeholder("REDACTED", "manual:3", "box 3")
    assert d not in {b, c} and vault.entries[c]["values"] == ["box 2"]


def test_wages_whose_digits_match_an_id_elsewhere_stay_readable(tmp_path):
    """An ID found on one page ("6123450") repeats onto every copy of it, but not onto an
    amount with the same digits on another page, and verification agrees."""
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery F Abernathy"}]}))
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 90), "Employer's state ID number: 6123450", fontsize=10)
    page = doc.new_page()
    page.insert_text((72, 90), "16 State wages, tips etc.", fontsize=10)
    page.insert_text((72, 106), "DE 61234.50   MA 612-3450", fontsize=10)
    doc.save(ws.inbox / "w2.pdf")
    outcome = run(ws, "w2.pdf")
    assert outcome.report.passed, outcome.report.issues
    text = pymupdf.open(ws.outputs("w2")["pdf"])[1].get_text()
    assert "61234.50" in text and "612-3450" not in text


def test_captions_and_the_omb_number_another_document_redacted_are_not_leaks(ws):
    """A detector mistake in another document (a W-2 caption taken for a name, the OMB number
    for an ID) must not fail every W-2 that prints them: neither identifies anyone."""
    pdf(ws.inbox / "letter.pdf", "Preparer's name: Quinn Delacroix-Marsh")
    assert run(ws, "letter.pdf").report.passed
    vault = Vault(ws.vault_dir / "placeholders.json")
    mistakes = [vault.placeholder("NAME", "name:fitw", "Federal income tax withheld"),
                vault.placeholder("ID", "id:15450008", "1545-0008")]
    vault.save()
    regions = ws.outputs("letter")["regions"]
    used = json.loads(regions.read_text())
    regions.write_text(json.dumps(used + [{**used[0], "placeholder": ph} for ph in mistakes]))
    pdf(ws.inbox / "w2.pdf", "Employee: Avery F Abernathy", "2 Federal income tax withheld 1,234.56",
        "Wage and Tax Statement 2025  OMB No. 1545-0008")
    outcome = run(ws, "w2.pdf")
    assert outcome.report.passed, outcome.report.issues
    assert "OMB No. 1545-0008" in pymupdf.open(ws.outputs("w2")["pdf"])[0].get_text()


def test_explain_names_the_document_a_leaked_value_comes_from(ws):
    from taxonymous.explain import explain, mask
    test_a_value_another_document_redacts_is_still_a_leak(ws)
    hits = [h for h in explain(ws.root, "w2", ocr=False) if h.kind == "NAME"]
    assert hits and all("letter.pdf" in h.why for h in hits)
    assert hits[0].value == mask("quinn delacroix-marsh")  # the leak itself, not text beside it


def test_a_filing_status_is_not_a_name(ws):
    """"Married Filing Jointly" read as a person once gave "Filing" as a name to look for,
    which every W-2's instructions print ("...after the due date for filing your return")."""
    from taxonymous.detect import is_caption
    assert is_caption("Married Filing Jointly") and is_caption("Filing")
    pdf(ws.inbox / "letter.pdf", "Preparer's name: Quinn Delacroix-Marsh")
    assert run(ws, "letter.pdf").report.passed
    vault = Vault(ws.vault_dir / "placeholders.json")
    ph = vault.placeholder("NAME", "name:filing", "Filing")
    vault.save()
    regions = ws.outputs("letter")["regions"]
    used = json.loads(regions.read_text())
    regions.write_text(json.dumps(used + [{**used[0], "placeholder": ph}]))
    pdf(ws.inbox / "w2.pdf", "Employee: Avery F Abernathy", "after the due date for filing your return")
    assert run(ws, "w2.pdf").report.passed
