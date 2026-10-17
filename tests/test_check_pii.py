"""The pre-commit guard. Real-looking values are assembled at runtime so this
file itself passes the guard."""

import pymupdf

from tools.check_pii import problems

REAL_LOOKING_SSN = "-".join(["123", "45", "6789"])
REAL_LOOKING_EIN = "-".join(["12", "3456789"])


def test_reserved_fake_ssn_passes():
    assert problems("notes.md", b"taxpayer 987-65-4321, spouse 987-65-4329") == []


def test_real_looking_ssn_is_blocked():
    assert problems("notes.md", f"ssn {REAL_LOOKING_SSN}".encode())
    assert problems("notes.md", REAL_LOOKING_SSN.replace("-", " ").encode())


def test_ein_without_00_prefix_is_blocked():
    assert problems("a.py", f'EIN = "{REAL_LOOKING_EIN}"'.encode())
    assert problems("a.py", b'EIN = "00-1234567"') == []


def test_ssn_inside_pdf_is_blocked():
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), f"SSN {REAL_LOOKING_SSN}")
    assert problems("tests/fixtures/x.pdf", doc.tobytes())


def test_documents_outside_fixtures_are_blocked():
    doc = pymupdf.open()
    doc.new_page()
    assert problems("W2-2025.pdf", doc.tobytes())
    assert problems("tests/fixtures/ok.pdf", doc.tobytes()) == []
    assert problems("scan.jpg", b"\xff\xd8")


def test_profile_files_are_blocked():
    assert problems("vault/profile.json", b"{}")
    assert problems("mine.profile.json", b"{}")
