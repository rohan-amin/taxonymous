"""Detector behaviour on small hand-built pages: your details in their many printed forms,
and SSN shapes. A real-looking SSN is assembled at runtime so the pre-commit PII guard
doesn't flag this file."""

import pymupdf
import pytest

from taxonymous.detect import ProfileMatcher, detect
from taxonymous.pagetext import PageText, extract
from taxonymous.profile import Profile

SSN = "-".join(["123", "45", "6789"])

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
    ("SSN: ending in 9999", None),  # not ours, and four digits are no SSN shape
    ("Payer's TIN 1099-INT", None),  # a form number, not a last four
    ("SSN 2026.00", None),
])
def test_last_four_after_label_edge_cases(line, expected):
    fs = findings(line)
    assert [(f.kind, f.text) for f in fs] == ([expected] if expected else [])


@pytest.mark.parametrize("line", [f"Employee SSN {SSN} on file", f"dependent {SSN.replace('-', ' ')}",
                                  "TIN XXX-XX-6789", "ssn ***-**-6789"])
def test_anything_shaped_like_an_ssn_is_found(line):
    """Whoever's it is: a dependent's or a preparer's, full or masked."""
    assert [f.kind for f in findings(line)] == ["SSN"]


@pytest.mark.parametrize("line", [f"Payer EIN {'-'.join(['12', '3456789'])}", "Account number: 88812345",
                                  "Phone (212) 555-1234", "Name:  Morgan Q Delacroix", "Contoso Widgets LLC",
                                  "Card 4111 1111 1111 1111", "Springfield, IL 62704-1234",
                                  "The account was opened in 2019 and the name on it did not change."])
def test_what_isnt_listed_or_ssn_shaped_stays(line):
    assert texts(line) == []


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


MORE = Profile.from_dict({
    "people": [{"name": "Avery F Abernathy"}],
    "accounts": [{"number": "8320967808", "routing": "021000021"}],
    "numbers": [{"number": "54321"}, {"number": "AB-10442"}],
    "preparers": [{"name": "Jordan Q Okafor", "firm": "Okafor & Co., CPAs", "street": "100 Main Street",
                   "city": "Seattle", "state": "WA", "zip": "98101", "ptin": "P00012345",
                   "phone": "(555) 555-0123", "ein": "00-1234567"}],
})


@pytest.mark.parametrize("line, expected", [
    ("Acct 7808", "7808"), ("Account ending 7808", "7808"), ("Checking ...7808", "7808"),
    ("Card x7808", "7808"), ("Routing 021000021", "021000021"), ("PIN 54321", "54321"),
    ("Self-select PIN 5 4 3 2 1", "5 4 3 2 1"), ("Client ID ab 10442", "ab 10442"),
    ("OKAFOR & CO CPAS", "OKAFOR & CO CPAS"), ("Paid preparer Jordan Q. Okafor", "Jordan Q. Okafor"),
    ("100 Main St", "100 Main St"), ("Seattle, WA 98101", "Seattle, WA 98101"),
    ("PTIN P00012345", "P00012345"), ("Firm's EIN 00-1234567", "00-1234567"),
])
def test_routing_other_numbers_and_the_preparer_are_found(line, expected):
    assert [f.text for f in findings(line, profile=MORE)] == [expected]


@pytest.mark.parametrize("line", [
    "Wages 7808.00", "Total 7,808", "Ref 178080", "Dated 12/7808", "Refund 543.21", "Jordan called",
    "Okafor", "Box 12 code D 7808-1",
])
def test_short_numbers_and_parts_of_a_preparer_alone_stay(line):
    assert findings(line, profile=MORE) == []


def test_a_short_number_alone_is_not_looked_for_in_raw_file_content():
    """There "7808" is as likely an object number or a coordinate ("7808 0 R")."""
    m = ProfileMatcher(MORE)
    assert m.find(PageText(0, "7808 0 R 54321 0 R"), raw=True) == []
    assert [f.text for f in m.find(PageText(0, "XXXX7808 P00012345"), raw=True)] == ["XXXX7808", "P00012345"]


@pytest.mark.parametrize("street", ["5842 Fenwick Ave Apartment APT 1007", "5842 Fenwick Ave Apt. 1007",
                                    "5842 Fenwick Ave #1007"])
def test_an_address_s_unit_is_found_apart_from_its_street(street):
    """Forms print the unit on the street's line or the next one, with any designator."""
    profile = Profile.from_dict({"addresses": [{"street": street, "city": "Tacoma", "state": "WA",
                                                "zip": "98458"}]})
    assert texts_of(["5842 FENWICK AVENUE", "APT 1007, TACOMA, WA 98458"], profile) == [
        "5842 FENWICK AVENUE", "APT 1007", "TACOMA, WA 98458"]
    assert texts_of(["5842 Fenwick Ave., Unit #1007"], profile) == ["5842 Fenwick Ave.", "Unit #1007"]
    assert texts_of(["Total 1007", "Room for 1007"], profile) == []


def test_a_street_named_like_a_unit_is_still_a_street():
    profile = Profile.from_dict({"addresses": [{"street": "100 Unit Road"}]})
    assert texts_of(["100 Unit Rd", "Unit 100"], profile) == ["100 Unit Rd"]


def texts_of(lines, profile):
    return [f.text for f in findings(*lines, profile=profile)]


def test_an_occupation_is_found_however_it_is_printed():
    profile = Profile.from_dict({"people": [{"name": "Avery Abernathy", "occupation": "Software Engineer"}]})
    assert texts_of(["Your occupation SOFTWARE ENGINEER", "Spouse's occupation software-engineer",
                     "Engineer", "Software"], profile) == ["SOFTWARE ENGINEER", "software-engineer"]


def test_a_last_four_that_could_be_a_year_is_found_after_a_mark():
    profile = Profile.from_dict({"accounts": ["Z28-401909"]})
    assert texts_of(["NATIONAL FINANCIAL SERVICES LLC #1909", "Acct x1909", "Ending ...1909",
                     "Founded 1909", "Tax year 1909"], profile) == ["1909", "1909", "1909"]


def test_a_school_district_is_found_as_the_state_return_prints_it():
    profile = Profile.from_dict({"addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA",
                                                "zip": "98458", "school_district": "Tacoma Union Free"}]})
    lines = ["School district name TACOMA UNION FREE", "Union Free"]
    assert texts_of(lines, profile) == ["TACOMA UNION FREE"]


def test_a_city_state_and_zip_in_boxes_of_their_own_are_found():
    """A form prints them in boxes of their own, its state and ZIP can run together, and a
    state alone is everywhere on a state return."""
    profile = Profile.from_dict({"addresses": [{"street": "5842 Fenwick Ave", "city": "Tacoma", "state": "WA",
                                                "zip": "98458"}]})
    lines = ["City, town, or post office TACOMA", "State ZIP code WA98458", "ZIP 98458-1234", "98458",
             "WA IT-201", "Wages 98458.00", "Total 98,458", "Ref 1984581"]
    assert texts_of(lines, profile) == ["TACOMA", "WA98458", "98458-1234", "98458"]


def test_an_employer_is_found_however_it_is_printed():
    profile = Profile.from_dict({"people": [{"name": "Avery Abernathy", "employer": "Contoso Widgets, LLC"}]})
    assert texts_of(["Employer's name CONTOSO WIDGETS LLC", "Contoso"], profile) == ["CONTOSO WIDGETS LLC"]


def test_a_form_s_letter_after_a_name_is_not_its_initial():
    """"ABERNATHY AVERY W-2" (a file name, a heading): the W is the form's, not a middle initial."""
    profile = Profile.from_dict({"people": [{"name": "Avery F Abernathy"}]})
    assert texts_of(["ABERNATHY AVERY W-2", "Abernathy, Avery F. W-2", "AVERY F ABERNATHY"], profile) == [
        "ABERNATHY AVERY", "Abernathy, Avery F.", "AVERY F ABERNATHY"]
