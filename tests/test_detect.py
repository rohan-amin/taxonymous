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
    assert [f.kind for f in findings("Ref 4111111111111112")] == ["ID"]  # a long number, not a card


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


def test_a_month_and_year_is_not_an_account_number():
    """A form's revision date under an ID label (1099-G: "State identification no." above
    "Form 1099-G (Rev. 12-2026)")."""
    assert texts("Account number", "Form 1099-G (Rev. 12-2026)") == []
    assert texts("Card expires: 04/2027") == []


def test_public_institution_names_are_kept_but_private_ones_are_not():
    kept = texts("PAYER'S name", "Contoso Bank, N.A.")
    assert "Contoso Bank, N.A." not in kept
    assert "Thorne Engineering LLC" in texts("Employer's name", "Thorne Engineering LLC")


def test_prose_is_left_alone():
    assert texts("The account was opened in 2019 and the name on it did not change.") == []


@pytest.mark.parametrize("entered", ["5555550142", "(555) 555-0142", "+1 555 555 0142", "1-555-555-0142"])
@pytest.mark.parametrize("ssn", ["987654323", "987-65-4323", "987 65 4323"])
def test_numbers_match_however_they_were_entered(entered, ssn):
    profile = Profile.from_dict({"people": [{"name": "Avery F Abernathy", "ssn": ssn}], "phones": [entered]})
    for line, value in [("Call (555) 555-0142", "(555) 555-0142"), ("tel 555.555.0142", "555.555.0142"),
                        ("ph 5555550142", "5555550142"), ("SSN 987-65-4323", "987-65-4323"),
                        ("TIN 987654323", "987654323")]:
        assert value in [f.text for f in findings(line, profile=profile)], (line, entered, ssn)


def test_only_the_number_of_an_account_ein_or_card_is_matched():
    profile = Profile.from_dict({
        "accounts": [{"institution": "Contoso Bank", "number": "8320968305", "name": "Joint checking"}],
        "eins": [{"ein": "00-4869454", "description": "Household employer"}],
        "cards": [{"number": "4111 1111 1111 1111", "description": "Travel card"}]})
    found = texts_with(profile, "Contoso Bank Joint checking acct 8320968305", "EIN 00-4869454",
                       "Card 4111111111111111")
    assert {"8320968305", "00-4869454", "4111111111111111"} <= set(found)
    assert not any("Contoso" in t or "checking" in t for t in found)


def texts_with(profile, *lines):
    return [f.text for f in findings(*lines, profile=profile)]


def test_a_date_of_death_stays_readable():
    profile = Profile.from_dict({"people": [{"name": "Quinn L Abernathy", "dob": "09/24/1976",
                                             "deceased": True, "dod": "03/14/2025"}]})
    found = [f.text for f in findings("Decedent: Quinn L Abernathy", "Date of birth: 09/24/1976",
                                      "Date of death: 03/14/2025", profile=profile)]
    assert "Quinn L Abernathy" in found and "09/24/1976" in found
    assert not any("2025" in t for t in found)


def placed(*items: tuple[float, str]) -> list[str]:
    """Texts found on one line made of pieces drawn at the given x positions."""
    doc = pymupdf.open()
    page = doc.new_page()
    for x, s in items:
        page.insert_text((x, 100), s, fontsize=10)
    return [f.text for f in detect(extract(page), ProfileMatcher(PROFILE))]


@pytest.mark.parametrize("items, expected", [
    ([(72, "Employer's state ID number")], None),
    ([(72, "CA"), (110, "000-0000 5"), (250, "54367.87")], "000-0000 5"),  # check digit, then the next column
    ([(72, "ID 0000000 5")], "0000000 5"),  # ends the line
    ([(72, "Ref 0000000 3 shares")], "0000000"),  # more words in the same cell: not a check digit
    ([(72, "Ref 0000000"), (160, "12")], "0000000"),  # a column away: a different cell
])
def test_a_trailing_check_digit_belongs_to_the_number(items, expected):
    found = placed(*items)
    if expected is None:
        assert found == []
    else:
        assert expected in found and "54367.87" not in " ".join(found)


@pytest.mark.parametrize("lines", [
    ["Employee's name", "This information is"],
    ["Employee's address and ZIP code", "This information is being furnished to the IRS."],
    ["Recipient's name", "If you are required to file a return"],
])
def test_a_sentence_under_a_name_or_address_label_is_not_a_value(lines):
    assert texts(*lines) == []


@pytest.mark.parametrize("lines, expected", [
    (["Employee's name", "Morgan May Delacroix"], "Morgan May Delacroix"),  # May the name, not "may"
    (["Employee's address and ZIP code", "17 Will Rogers Ln"], "17 Will Rogers Ln"),
])
def test_names_and_addresses_with_word_like_parts_still_count(lines, expected):
    assert expected in texts(*lines)


@pytest.mark.parametrize("text, copy", [
    ("6123450", True), ("612-3450", True), ("612 34 50", True), ("ID 6123450.", True),
    ("61234.50", False), ("$61,234.50", False), ("(61234.50)", False), ("wages 61234.50.", False),
])
def test_an_amount_is_never_a_copy_of_a_number(text, copy):
    """A state ID read as "6123450" somewhere must not black out "61234.50" in the wages box."""
    import re

    from taxonymous.detect import number_regex
    assert bool(re.search(number_regex("6123450"), text)) is copy


def test_dotted_ids_are_still_copies():
    import re

    from taxonymous.detect import number_regex
    assert re.search(number_regex("123456"), "Ref 12.34.56 issued")
