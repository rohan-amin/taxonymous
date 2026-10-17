"""M4 acceptance: long statements, names in prose, values repeated across pages,
and a document type the detectors were never tuned on."""

import json
import re
import time

import pymupdf
import pytest

from taxonymous import names
from taxonymous import redact as redact_mod
from taxonymous.detect import ProfileMatcher, detect
from taxonymous.pagetext import PageText, extract
from taxonymous.profile import Profile
from taxonymous.redact import redact_file
from taxonymous.vault import Vault
from taxonymous.verify import Known, verify
from tools.fixtures.generate import generate

ROW_RE = re.compile(r"^\d+ sh .*?([\d,]+\.\d\d)\s+[\d,]+\.\d\d\s*$", re.M)
LONG_PAGES = 150
LONG_SECONDS = 60


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
def test_150_page_statement_in_under_a_minute(tmp_path):
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

def test_outsiders_named_in_prose_are_redacted(redacted):
    d, res, _ = doc(redacted, "k1_statement.pdf")
    text = res.text.read_text()
    outsiders = [i["value"] for i in d["redact"] if i["third_party"]]
    assert len(outsiders) >= 8
    assert not [v for v in outsiders if v in text]
    # the numbers in the same sentences stay
    assert all(i["value"] in text for i in d["keep"])


def test_a_surname_alone_gets_the_full_names_placeholder(redacted):
    d, res, vault = doc(redacted, "k1_statement.pdf")
    full = [i["value"] for i in d["redact"] if i["type"] == "NAME" and i["third_party"]]
    last = [i["value"] for i in d["redact"] if i["type"] == "NAME_LAST"]
    pairs = [(f, s) for f in full for s in last if f.endswith(" " + s)]
    assert pairs
    for f, s in pairs:
        assert placeholder_of(vault, f) == placeholder_of(vault, s) is not None


def test_a_value_labelled_on_one_page_is_redacted_bare_on_another(redacted):
    d, res, vault = doc(redacted, "k1_statement.pdf")
    [account] = [i["value"] for i in d["redact"] if i["type"] == "ACCOUNT"]
    ph = placeholder_of(vault, account)
    page2 = res.text.read_text().split("=== Page 2 ===")[1]
    assert ph and ph in page2 and account not in page2


def test_an_outsiders_ssn_in_a_sentence_is_redacted(redacted):
    d, res, _ = doc(redacted, "k1_statement.pdf")
    [ssn] = [i["value"] for i in d["redact"] if i["type"] == "SSN"]
    assert ssn not in res.text.read_text()


def test_a_private_partnership_heading_is_redacted(redacted):
    d, res, _ = doc(redacted, "k1_statement.pdf")
    [firm] = [i["value"] for i in d["redact"] if i["type"] == "ORG_PRIVATE"]
    assert firm not in res.text.read_text()


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

def page(*items: tuple[float, float, str]) -> PageText:
    """A page with text objects placed at (x, y)."""
    pdf = pymupdf.open()
    p = pdf.new_page()
    for x, y, text in items:
        p.insert_text((x, y), text, fontsize=9)
    return extract(p)


@pytest.fixture(scope="module")
def matcher():
    return ProfileMatcher(Profile.from_dict({"people": [{"name": "Avery F Abernathy"}]}))


def texts(pt, matcher):
    return [f.text for f in detect(pt, matcher)]


def test_only_prose_goes_to_the_name_model():
    pt = page((72, 72, "During the year the partnership was managed by its general partner."),
              (72, 86, "Dear Jordan and Casey,"),
              (72, 100, "141 sh Woodgrove Growth Fund Cl I   N8Y2ARB54   32,135.13"),
              (72, 114, "Northwind Total Market Index Fd"),
              (72, 128, "Sincerely,"), (72, 142, "Gideon Marchetti"), (72, 156, "Partnership Representative"))
    blocks = [pt.text[s:e] for s, e in names.prose_blocks(pt)]
    assert blocks == ["During the year the partnership was managed by its general partner.\n"
                      "Dear Jordan and Casey,",
                      "Sincerely,\nGideon Marchetti\nPartnership Representative"]


def test_value_to_the_right_of_its_label_on_the_same_row(matcher):
    pt = page((330, 72, "Employee ID:"), (420, 72, "489135"), (330, 84, "Pay date:"), (420, 84, "12/19/2025"))
    assert texts(pt, matcher) == ["489135"]


@pytest.mark.parametrize("line, expected", [
    ("Parcel 3955-11-3990", ["3955-11-3990"]),
    ("Advice no. 63083443", ["63083443"]),
    ("OMB No. 1545-0029", []),
    ("Assessed value 1,234,567", []),
    ("Total 1234567.00", []),
    ("Tax year 2025 statement 12/19/2025", []),
])
def test_long_bare_numbers_are_ids_but_amounts_and_form_numbers_are_not(matcher, line, expected):
    assert texts(page((72, 72, line)), matcher) == expected


def test_a_role_and_a_colon_label_a_name(matcher):
    pt = page((72, 72, "Provider: Cyrus Pemberton"), (72, 86, "Executor: Ines Valdivia"))
    assert texts(pt, matcher) == ["Cyrus Pemberton", "Ines Valdivia"]
