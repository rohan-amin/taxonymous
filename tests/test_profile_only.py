"""The default: only your details, and anything shaped like an SSN, are redacted."""

import json

import pymupdf

from taxonymous.detect import ProfileMatcher, detect
from taxonymous.pagetext import extract
from taxonymous.pipeline import Workspace, run
from taxonymous.profile import Profile
from taxonymous.verify import verify

NAME = "Avery F Abernathy"
SSN = "987-65-4323"
OTHER_SSN = "-".join(["123", "45", "6789"])  # a dependent's or preparer's, not listed
EIN = "00-3456789"  # the employer's
DETAILS = {
    "people": [{"name": NAME, "ssn": SSN, "dob": "03/07/1981"}],
    "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA", "zip": "98458"}],
    "accounts": [{"number": "8320968305"}],
}
W2 = [f"Employee's name {NAME}", f"Employee's SSN {SSN}", "5842 Fenwick Avenue", "Tacoma, WA 98458",
      f"Employer identification number (EIN) {EIN}", "Employer's name Contoso Widgets LLC",
      "Control number 0047812", "Preparer Morgan Delacroix", f"Dependent SSN {OTHER_SSN}",
      "Wages, tips, other compensation 54367.87"]


def _page(lines: list[str]) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 14 * i), line, fontsize=10)
    return doc


def found(*lines: str, details: dict = DETAILS) -> list[str]:
    page = _page(list(lines))[0]
    return [f.text for f in detect(extract(page), ProfileMatcher(Profile.from_dict(details)))]


def test_only_your_details_and_ssn_shapes_are_found():
    texts = found(*W2)
    assert NAME in texts and SSN in texts and OTHER_SSN in texts
    assert "5842 Fenwick Avenue" in texts and "Tacoma, WA 98458" in texts
    for kept in (EIN, "Contoso", "0047812", "Morgan", "54367.87"):
        assert not any(kept in t for t in texts), kept


def test_masked_and_last_four_forms_of_your_numbers_are_found():
    texts = found("SSN XXX-XX-4323", "TIN: 4323", "Account ****8305", "Checking ending in 8305",
                  "Order 4323 shipped")
    assert texts == ["XXX-XX-4323", "4323", "****8305", "8305"]


def test_the_detectors_still_run_when_asked():
    texts = found(*W2, details={**DETAILS, "find_others": True})
    assert EIN in texts and "0047812" in texts


def _workspace(tmp_path, details: dict) -> Workspace:
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(details))
    return ws


def test_a_w2_passes_with_the_employer_left_readable(tmp_path):
    ws = _workspace(tmp_path, DETAILS)
    _page(W2).save(ws.inbox / "w2.pdf")
    [outcome] = run(ws, ocr=False)
    assert outcome.report.passed, outcome.report.issues
    text = pymupdf.open(ws.outputs("w2")["pdf"])[0].get_text()
    assert EIN in text and "Contoso Widgets LLC" in text and "Morgan Delacroix" in text
    for gone in ("Abernathy", SSN, OTHER_SSN, "Fenwick", "98458"):
        assert gone not in text, gone


def test_what_another_document_redacted_fails_nothing_here(tmp_path):
    """A name the detectors once took from a tax return ("Go to") isn't looked for in a
    W-2 once they are off."""
    ws = _workspace(tmp_path, {**DETAILS, "find_others": True})
    _page(["Preparer Morgan Delacroix", f"Employee {NAME}"]).save(ws.inbox / "a_return.pdf")
    assert run(ws, ocr=False)[0].report.passed
    ws.profile_path.write_text(json.dumps(DETAILS))
    _page([f"Employee {NAME}", "Prepared with Morgan Delacroix"]).save(ws.inbox / "b_w2.pdf")
    outcomes = {o.source.name: o for o in run(ws, ocr=False)}
    assert outcomes["b_w2.pdf"].report.passed, outcomes["b_w2.pdf"].report.issues


def test_your_details_left_in_a_file_still_fail_it(tmp_path):
    _page(W2).save(tmp_path / "leaky.pdf")
    report = verify(tmp_path / "leaky.pdf", ProfileMatcher(Profile.from_dict(DETAILS)), [], ocr=False)
    kinds = {i.kind for i in report.issues if i.check == "text"}
    assert not report.passed and {"NAME", "SSN", "ADDR"} <= kinds
    assert "EIN" not in kinds and "ID" not in kinds


def test_a_last_four_redacted_after_its_label_is_not_searched_for_alone(tmp_path):
    """"SSN 4323" is redacted; the same four digits in a font's width table, the page's
    drawing code or an amount are not a leak."""
    ws = _workspace(tmp_path, DETAILS)
    doc = _page([f"Employee {NAME}", "Partner SSN 4323", "Units 4323 of 10000", "Total 14,323.00"])
    doc.save(ws.inbox / "k1.pdf")
    [outcome] = run(ws, ocr=False)
    assert outcome.report.passed, outcome.report.issues
    text = pymupdf.open(ws.outputs("k1")["pdf"])[0].get_text()
    assert "SSN 4323" not in text and "Units 4323" in text


def test_a_birth_date_is_found_however_a_form_prints_it():
    texts = found("Born 03/07/1981", "DOB 03071981", "Birth date 03 07 1981", "19810307", "03 / 07 / 1981",
                  "Mar 7, 1981", "Total 3071981.00", "Ref 1030719812")
    assert texts == ["03/07/1981", "03071981", "03 07 1981", "19810307", "03 / 07 / 1981", "Mar 7, 1981"]
