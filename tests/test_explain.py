"""`taxonymous explain`: why a document failed, with nothing in it that could identify anyone."""

import json
import re
import shutil

import pymupdf
import pytest

from taxonymous.cli import main as cli
from taxonymous.detect import ProfileMatcher
from taxonymous.explain import explain, mask, render
from taxonymous.pipeline import Workspace, process, reverify
from taxonymous.profile import Profile
from taxonymous.vault import Vault

NAME, SSN, EIN = "Avery F Abernathy", "987-65-4323", "00-4869455"
STREET = "5842 Fenwick Ave"


@pytest.fixture
def failed(tmp_path):
    """A document whose output was swapped for the original, so everything in it leaks."""
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps({"people": [{"name": NAME, "ssn": SSN}], "addresses": [
        {"street": STREET, "city": "Tacoma", "state": "WA", "zip": "98458"}], "find_others": True}))
    doc = pymupdf.open()
    page = doc.new_page()
    lines = [f"Employee: {NAME}  SSN {SSN}", f"Employer identification number (EIN): {EIN}", STREET,
             f"State wages 1,234.56  ID{EIN}"]
    for y, line in zip((90, 110, 130, 150), lines, strict=True):
        page.insert_text((72, y), line, fontsize=10)
    doc.save(ws.inbox / "w2_statement.pdf")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    assert process(ws.inbox / "w2_statement.pdf", ws, matcher, Vault(ws.vault_dir / "placeholders.json"),
                   release=False).report.passed
    shutil.copyfile(ws.inbox / "w2_statement.pdf", ws.outputs("w2_statement")["pdf"])
    report = reverify(ws, "w2_statement", matcher, Vault(ws.vault_dir / "placeholders.json"), ocr=False)
    assert not report.passed
    return ws


def test_mask_keeps_form_words_and_placeholders_only():
    assert mask("Employer ID: 00-4869455 Avery <NAME_A> Wages 1,234.56") == \
        "Employer ID: ##-####### Aaaaa <NAME_A> Wages #,###.##"


def test_explain_shows_where_without_showing_what(failed, capsys):
    hits = explain(failed.root, "w2_statement", ocr=False)
    kinds = {(h.kind, h.why) for h in hits}
    assert ("EIN", "a value redacted in this document") in kinds
    assert ("NAME", "it is in your details") in kinds
    assert any("ID##-#######" in h.context for h in hits)  # the layout that matters survives

    assert cli(["explain", "--workspace", str(failed.root), "w2_statement.pdf", "--no-ocr"]) == 0
    out = capsys.readouterr().out
    for secret in [*NAME.split(), "Fenwick", "Tacoma", *re.findall(r"\d{2,}", SSN + EIN + STREET)]:
        assert secret not in out, secret


def test_explain_says_so_when_there_is_nothing_to_explain():
    assert render([], []) == "Verification passed: nothing to explain."


def test_explain_one_box_says_where_it_came_from(failed, capsys):
    from taxonymous.explain import explain_placeholder
    vault = Vault(failed.vault_dir / "placeholders.json")
    ein = next(ph for ph, e in vault.entries.items() if e["kind"] == "EIN")
    out = explain_placeholder(failed.root, "w2_statement", ein.strip("<>"))
    assert f"{ein} (EIN) stands for: ##-#######" in out
    assert "page 1 · a nearby label" in out or "page 1 · its shape" in out
    assert "a copy of it" in out  # "ID00-..." in the state wages line: not a detector's, the repeat's
    assert cli(["explain", "--workspace", str(failed.root), "w2_statement.pdf", ein.strip("<>")]) == 0
    printed = capsys.readouterr().out
    for secret in [*(w for w in NAME.split() if len(w) > 1), *re.findall(r"\d{2,}", SSN + EIN)]:
        assert secret not in printed, secret
