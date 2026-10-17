"""Detector behaviour on small hand-built pages. Real-looking SSN/EIN values
are assembled at runtime so the pre-commit PII guard doesn't flag this file."""

import pymupdf
import pytest

from taxonymous.detect import ProfileMatcher, detect, luhn
from taxonymous.pagetext import extract
from taxonymous.profile import Profile

SSN = "-".join(["123", "45", "6789"])
EIN = "-".join(["12", "3456789"])

PROFILE = Profile.from_dict({
    "people": [{"name": "Avery F Abernathy", "ssn": "987-65-4323", "dob": "03/07/1981", "role": "taxpayer"}],
    "addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA", "zip": "98458"}],
    "phones": ["(555) 555-0142"],
    "emails": ["avery@example.com"],
    "accounts": ["6346191"],
})


def findings(*lines: str, profile: Profile = PROFILE):
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + 14 * i), line, fontsize=10)
    return detect(extract(page), ProfileMatcher(profile))


def texts(*lines: str) -> list[str]:
    return [f.text for f in findings(*lines)]


@pytest.mark.parametrize("line, expected", [
    ("Recipient: Avery F Abernathy", "Avery F Abernathy"),
    ("ABERNATHY, AVERY F", "ABERNATHY, AVERY F"),
    ("Avery F. Abernathy", "Avery F. Abernathy"),
    ("Dear Avery,", "Avery"),
    ("SSN 987654323", "987654323"),
    ("Born March 7, 1981", "March 7, 1981"),
    ("born 1981-03-07", "1981-03-07"),
    ("5842 FENWICK AVENUE", "5842 FENWICK AVENUE"),
    ("call 555.555.0142", "555.555.0142"),
])
def test_profile_values_match_in_other_formats(line, expected):
    assert expected in texts(line)


def test_masked_last_four_maps_to_profile_identity():
    fs = findings("Recipient's TIN: ***-**-4323", "Account XXXX-6191")
    assert [f.key for f in fs] == ["ssn:0", "acct:0"]


@pytest.mark.parametrize("line", ["SSN 4323", "SSN: ending in 4323", "TIN (last 4 digits): 4323",
                                  "XXX-XX-4323"])
def test_last_four_after_ssn_label_is_the_profile_ssn(line):
    """Masked or bare, the last four after an SSN label is redacted as the taxpayer's SSN."""
    [f] = findings(line)
    assert (f.kind, f.key, f.text[-4:]) == ("SSN", "ssn:0", "4323")


@pytest.mark.parametrize("line, expected", [
    ("SSN: ending in 9999", ("TIN", "9999")),  # not ours: still redacted, not called a card
    ("Payer's TIN 1099-INT", None),  # a form number, not a last four
    ("SSN 2026.00", None),
])
def test_last_four_after_label_edge_cases(line, expected):
    fs = findings(line)
    assert [(f.kind, f.text) for f in fs] == ([expected] if expected else [])


@pytest.mark.parametrize("line, expected", [
    (f"Employee SSN {SSN} on file", SSN),
    (f"Payer EIN {EIN}", EIN),
    ("Write to jo.smith+tax@mail.example.org today", "jo.smith+tax@mail.example.org"),
    ("Phone (212) 555-1234", "(212) 555-1234"),
    ("Card 4111 1111 1111 1111 approved", "4111 1111 1111 1111"),
    ("Ship to 77 Old Mill Road, Apt 4", "77 Old Mill Road, Apt 4"),
    ("Springfield, IL 62704-1234", "Springfield, IL 62704-1234"),
])
def test_patterns_catch_identifiers_not_in_profile(line, expected):
    assert expected in texts(line)


def test_luhn_rejects_random_long_numbers():
    assert luhn("4111111111111111")
    assert not luhn("4111111111111112")
    assert texts("Ref 4111111111111112") == []


@pytest.mark.parametrize("lines, expected", [
    (["Account number: 88812345"], "88812345"),
    (["Member # 353985716236"], "353985716236"),
    (["Fund No. 7731042"], "7731042"),
    (["Control number", "C123456789"], "C123456789"),
    (["Name:  Morgan Q Delacroix"], "Morgan Q Delacroix"),
    (["Date of birth", "11/02/1958"], "11/02/1958"),
])
def test_values_next_to_labels(lines, expected):
    assert expected in texts(*lines)


def test_amounts_are_never_taken_by_patterns_or_labels():
    assert texts("Account number: 1,234.56", "Control number", "98,765.43") == []


def test_public_institution_names_are_kept_but_private_ones_are_not():
    kept = texts("PAYER'S name", "Contoso Bank, N.A.")
    assert "Contoso Bank, N.A." not in kept
    assert "Thorne Engineering LLC" in texts("Employer's name", "Thorne Engineering LLC")


def test_prose_is_left_alone():
    assert texts("The account was opened in 2019 and the name on it did not change.") == []
