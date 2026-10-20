"""M4 acceptance: long statements, an SSN repeated across pages however it is printed,
and document types the redactor was never tuned on."""

import json
import re
import time

import pymupdf
import pytest

from taxonymous import redact as redact_mod
from taxonymous.detect import ProfileMatcher
from taxonymous.profile import Profile
from taxonymous.redact import redact_file
from taxonymous.vault import Vault
from taxonymous.verify import Known, verify
from tools.fixtures.generate import generate

ROW_RE = re.compile(r"^\d+ sh .*?([\d,]+\.\d\d)\s+[\d,]+\.\d\d\s*$", re.M)
LONG_PAGES = 150
LONG_SECONDS = 75  # 60 until every page rewrite was checked for moved text (about 1 s per pass)


def rows_and_proceeds(text: str) -> tuple[int, float]:
    proceeds = [float(m.group(1).replace(",", "")) for m in ROW_RE.finditer(text)]
    return len(proceeds), round(sum(proceeds), 2)


def placeholder_of(vault: Vault, value: str) -> str | None:
    return next((ph for ph, e in vault.entries.items() if value in e["values"]), None)


def doc(redacted, name):
    results, vault = redacted
    return results[name][0], results[name][1], vault


# ---------------------------------------------------------------- long statements

def test_transaction_rows_and_totals_match_the_original(redacted):
    d, res, _ = doc(redacted, "brokerage_consolidated.pdf")
    assert rows_and_proceeds(res.text.read_text()) == (d["stats"]["transaction_rows"],
                                                       d["stats"]["total_proceeds"])


def test_security_names_in_tables_are_not_taken_for_people(redacted):
    _, res, _ = doc(redacted, "brokerage_consolidated.pdf")
    text = res.text.read_text()
    assert "Woodgrove Growth Fund Cl I" in text and "Northwind Securities LLC" in text


@pytest.mark.slow
def test_150_page_statement_in_reasonable_time(tmp_path):
    src = tmp_path / "src"
    manifest = generate(src, seed=7, brokerage_pages=LONG_PAGES)
    d = next(x for x in manifest["documents"] if x["file"] == "brokerage_consolidated.pdf")
    matcher = ProfileMatcher(Profile.from_dict(json.loads((src / "household.json").read_text())))
    start = time.monotonic()
    res = redact_file(src / d["file"], tmp_path / "out", matcher, Vault(tmp_path / "v.json"))
    report = verify(res.pdf, matcher, [Known(f.kind, f.text) for f in res.findings], res.text)
    elapsed = time.monotonic() - start
    print(f"{LONG_PAGES} pages: redact + verify {elapsed:.1f}s")
    assert report.passed and report.pages == LONG_PAGES
    assert rows_and_proceeds(res.text.read_text()) == (d["stats"]["transaction_rows"],
                                                       d["stats"]["total_proceeds"])
    assert elapsed < LONG_SECONDS


def test_long_scans_are_read_a_few_pages_at_a_time(corpus, monkeypatch, tmp_path):
    """Pages are dropped from memory once read and rendered again to be rebuilt; the
    output is the same as reading them all at once."""
    src, manifest, profile = corpus
    matcher = ProfileMatcher(Profile.from_dict(profile))
    monkeypatch.setattr(redact_mod, "OCR_BATCH", 1)
    res = redact_file(src / "brokerage_scan.pdf", tmp_path, matcher, Vault(tmp_path / "v.json"))
    assert res.image_pages == [1, 2]
    assert verify(res.pdf, matcher, [Known(f.kind, f.text) for f in res.findings], res.text).passed


# ---------------------------------------------------------------- free text

def test_an_outsiders_ssn_in_a_sentence_is_redacted(redacted):
    d, res, _ = doc(redacted, "k1_statement.pdf")
    [ssn] = [i["value"] for i in d["redact"] if i["type"] == "SSN"]
    assert ssn not in res.text.read_text()


# ---------------------------------------------------------------- held out

def test_held_out_document_types_pass(redacted, corpus):
    _, manifest, _ = corpus
    held = [d["file"] for d in manifest["documents"] if d["source"].startswith("heldout:")]
    assert held
    results, _ = redacted
    for name in held:
        d, res = results[name]
        text = res.text.read_text()
        assert not [i["value"] for i in d["redact"] if i["value"] in text], name
        assert all(i["value"] in text for i in d["keep"]), name


# ---------------------------------------------------------------- units

@pytest.mark.parametrize("later", ["SSN987654329", "987 654 329", "(987)654329"])
def test_an_ssn_found_on_one_page_is_redacted_however_another_prints_it(tmp_path, later):
    """A dependent's SSN, printed normally on page 1 and run together with a label or spaced
    differently further on. Verification matches a number by its digits, so redaction has
    to as well."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 90), "Dependent SSN: 987-65-4329", fontsize=10)
    doc.new_page().insert_text((72, 90), f"Dependent detail  {later}  Credit 1,234.56", fontsize=10)
    doc.save(tmp_path / "statement.pdf")
    matcher = ProfileMatcher(Profile.from_dict({"people": [{"name": "Avery F Abernathy"}]}))
    vault = Vault(tmp_path / "vault.json")
    result = redact_file(tmp_path / "statement.pdf", tmp_path / "out", matcher, vault)
    known = [Known(f.kind, f.text) for f in result.findings]
    report = verify(result.pdf, matcher, known, result.text, ocr=False)
    assert report.passed, report.issues
    assert "1,234.56" in pymupdf.open(result.pdf)[1].get_text()
