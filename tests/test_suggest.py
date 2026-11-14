"""Click a value: what it grows to, what it is labelled, and what it is suggested as."""

import pytest

from taxonymous.suggest import AMOUNT, MASKED, ORG, STATE, THEIR_EIN, grow, label_for, suggest
from taxonymous.words import PageWord


def row(*texts: str, y: float = 100, x: float = 40, line: int = 1, gap: float = 3) -> list[PageWord]:
    """Words set left to right on one line, 10 points tall, 6 points a character."""
    out = []
    for t in texts:
        out.append(PageWord(t, (x, y, x + 6 * len(t), y + 10), line))
        x += 6 * len(t) + gap
    return out


@pytest.mark.parametrize("text, label, kind, item", [
    ("avery@example.com", "", "email", {"value": "avery@example.com"}),
    ("(555) 555-0100", "", "phone", {"value": "(555) 555-0100"}),
    ("5555550100", "Telephone", "phone", {"value": "5555550100"}),
    ("987-65-4323", "", "person", {"name": "", "ssn": "987-65-4323", "role": "other"}),
    ("00-8907406", "PAYER’S TIN", "ein", {"ein": "00-8907406", "description": "PAYER’S TIN"}),
    ("002823586", "Payer's RTN (optional)", "account",
     {"institution": "", "number": "", "routing": "002823586", "name": ""}),
    ("8320968305", "Account number (see instructions)", "account",
     {"institution": "", "number": "8320968305", "routing": "", "name": "", "type": ""}),
    ("2287441903", "Roth IRA account number", "account",
     {"institution": "", "number": "2287441903", "routing": "", "name": "", "type": "roth_ira"}),
    ("4111 1111 1111 1111", "", "card", {"number": "4111 1111 1111 1111", "description": ""}),
    ("736 Quarry Ct", "", "address", {"street": "736 Quarry Ct", "city": "", "state": "", "zip": ""}),
    ("Madison, WI 53780", "", "address", {"street": "", "city": "Madison", "state": "WI", "zip": "53780"}),
    ("ABERNATHY, AVERY F", "Recipient's name", "person", {"name": "Avery F Abernathy", "role": "other"}),
    ("P01234567", "", "preparer", {"name": "", "firm": "", "ptin": "P01234567"}),
    ("Okafor & Co CPAs", "Firm's name", "preparer", {"name": "", "firm": "Okafor & Co CPAs"}),
    ("A1B2C3D4", "Client ID", "number", {"number": "A1B2C3D4", "description": "Client ID"}),
    ("4/7/1984", "Date of birth", "person", {"name": "", "dob": "4/7/1984", "role": "other"}),
    ("CA", "15 State", "address", {"street": "", "city": "", "state": "CA", "zip": ""}),
])
def test_a_value_is_suggested_by_its_shape_and_label(text, label, kind, item):
    assert suggest(text, label)[:2] == (kind, item)


def test_an_ordinary_word_is_not_guessed():
    for word in ("income", "Income", "Total wages"):
        assert suggest(word)[:2] == ("", {}), word
    assert suggest("Abernathy", "Last name")[:2] == ("person", {"name": "Abernathy", "role": "other"})


def test_the_first_person_added_is_you():
    assert suggest("Avery Abernathy", "", has_people=False)[1]["role"] == "taxpayer"


def test_cautions_for_what_usually_stays_readable():
    assert suggest("$14,871.34")[2] == AMOUNT  # amounts stay: the AI needs them
    assert suggest("00-8907406", "PAYER’S TIN")[2] == THEIR_EIN
    assert suggest("00-4869454", "Your household employer EIN")[2] is None
    assert suggest("Contoso Bank, N.A.")[2] == ORG
    assert suggest("CA", "State")[2] == STATE  # removed next to the ZIP, not on its own
    kind, item, note = suggest("XXX-XX-4323", "Partner's identifying number")
    assert (kind, item["ssn"], note) == ("person", "", MASKED)  # the full SSN covers every form of it


def test_punctuation_a_sentence_put_around_a_value_is_dropped():
    assert suggest("987-65-4321).")[1]["ssn"] == "987-65-4321"


def test_a_click_grows_to_the_whole_value():
    words = row("Rewards", "member:", "Avery", "Abernathy", "$12.00")
    assert grow(words, [3]) == [2, 3]  # not the label before it
    words = row("Call", "(555)", "555-0128", "today")
    assert grow(words, [2]) == [1, 2]
    words = row("PAYER", "736", "Quarry", "Ct", "Suite", "4")
    assert grow(words, [2]) == [1, 2, 3, 4, 5]
    words = row("Madison,", "WI", "53780")
    assert grow(words, [0]) == [0, 1, 2]
    # a form's first-name box and last-name box, on one row with a gap between
    words = row("Avery", "F", line=1) + row("Abernathy", x=180, line=2)
    assert grow(words, [2]) == [0, 1, 2]
    assert grow(words, [0, 1]) == [0, 1]  # chosen by hand: left as chosen


def test_the_nearest_label_speaks_for_a_value():
    label = row("Account", "number", y=80, line=1)
    value = row("8320968305", y=94, x=50, line=2)
    para = row("Your", "capital", "account", "is", "described", "below.", y=60, line=0)
    words = para + label + value
    assert label_for(words, [len(words) - 1]) == "Account number"
    # in a sentence: the words just before it, not the sentence above
    words = row("The", "account", "was", "closed.", y=40, line=0)
    words += row("His", "SSN", "987-65-4321", y=54, line=1)
    assert label_for(words, [6]) == "His SSN"
