"""M7: odd PDFs (tools/fixtures/odd.py) through the whole pipeline.

Every file must come out with no identifier anywhere in it, checked here
independently of the verifier: in the raw bytes of every object and stream,
in all text including text outside the page, and in the outline and page
labels. And the verifier alone must catch each hiding place, so a redactor
bug there fails rather than passes.
"""

import json
from pathlib import Path

import pymupdf
import pytest

from taxonymous.detect import ProfileMatcher
from taxonymous.pipeline import Workspace, process
from taxonymous.profile import Profile
from taxonymous.sanitize import strip_replacement_strings
from taxonymous.vault import Vault
from taxonymous.verify import Known, verify
from tools.fixtures import fake, odd

AMOUNT = "1,234.56"  # printed on every odd page; amounts stay


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    h = fake.household(7)
    ws = Workspace(tmp_path_factory.mktemp("oddws"))
    ws.init()
    ws.profile_path.write_text(json.dumps(h.profile()))
    cases = odd.build_all(h, ws.inbox)
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    outcomes = {name: process(ws.inbox / name, ws, matcher, vault, release=True) for name in cases}
    return h, ws, cases, outcomes


def leaks(pdf: Path, values: list[str]) -> set[str]:
    """Every value found anywhere in the file, by any means short of rendering."""
    doc = pymupdf.open(pdf)
    blobs = [pdf.read_bytes()]
    for xref in range(1, doc.xref_length()):
        blobs.append(doc.xref_object(xref, compressed=False).encode("latin-1", "replace"))
        if doc.xref_is_stream(xref):
            blobs.append(doc.xref_stream(xref) or b"")
    text = "".join(p.get_text(clip=pymupdf.INFINITE_RECT()) for p in doc)
    text += str(doc.get_toc()) + str(doc.get_page_labels()) + str(doc.metadata)
    found = set()
    for v in values:
        hexed = v.encode().hex().encode()
        forms = [v.encode(), v.encode("utf-16-be"), hexed, hexed.upper()]
        if v in text or any(f in b for f in forms for b in blobs):
            found.add(v)
    return found


@pytest.mark.parametrize("name", [n for n in odd.BUILDERS if n not in odd.REFUSED])
def test_odd_pdf_is_redacted_with_nothing_left_anywhere(processed, name):
    _, ws, cases, outcomes = processed
    outcome = outcomes[name]
    assert outcome.report.passed, outcome.report.issues
    assert not leaks(outcome.result.pdf, cases[name])
    assert outcome.released  # it passed, so it went to ready-to-upload/
    assert AMOUNT in outcome.result.text.read_text()


def test_password_protected_pdf_is_held_back(processed):
    _, ws, _, outcomes = processed
    report = outcomes["user_password.pdf"].report
    assert not report.passed and "password" in report.issues[0].detail
    assert not any(p.exists() for p in ws.released("user_password"))


@pytest.mark.parametrize("name", ["type3_broken_encoding.pdf", "vector_text.pdf", "letter_spaced.pdf"])
def test_a_text_layer_that_doesnt_match_the_page_is_rebuilt_from_ocr(processed, name):
    outcome = processed[3][name]
    assert outcome.result.image_pages == [1]
    assert "rebuilt as an image" in outcome.report.notes[0]


def test_pages_keep_their_visible_area_and_rotation(processed):
    _, _, _, outcomes = processed
    cropped = pymupdf.open(outcomes["outside_cropbox.pdf"].result.pdf)[0]
    assert cropped.rect == pymupdf.Rect(0, 0, 595, 442) and "Statement of account" in cropped.get_text()
    for angle in (90, 180, 270):
        page = pymupdf.open(outcomes[f"rotate_{angle}.pdf"].result.pdf)[0]
        assert page.rotation == angle and "<SSN_A>" in page.get_text()


@pytest.mark.parametrize("name, check", [
    ("off_page.pdf", "structure"),
    ("outside_cropbox.pdf", "structure"),
    ("hidden_layer.pdf", "structure"),
    ("actual_text.pdf", "raw"),
    ("bookmarks.pdf", "raw"),
    ("orphan_object.pdf", "raw"),
    ("incremental_update.pdf", "raw"),
])
def test_the_verifier_alone_catches_each_hiding_place(processed, tmp_path, name, check):
    """The original file, as if the redactor had done nothing at all."""
    h, *_ = processed
    values = odd.BUILDERS[name](h, tmp_path / name)
    report = verify(tmp_path / name, ProfileMatcher(Profile.from_dict(h.profile())),
                    [Known("ID", v) for v in values], ocr=False)
    assert not report.passed
    assert check in {i.check for i in report.issues}
    assert {i.kind for i in report.issues if i.check == "raw"} >= {"SSN"}  # the value itself, in the file


@pytest.mark.parametrize("data, expected", [
    (b"/Span << /ActualText (987-65-4320) >> BDC", b"/Span << >> BDC"),
    (b"/Span <</Alt (a (nested) one) /Lang (en)>> BDC", b"/Span << /Lang (en)>> BDC"),
    (b"/Span << /ActualText <FEFF0041> >> BDC", b"/Span << >> BDC"),
    (b"/Span << /ActualText (esc \\) aped) >> BDC", b"/Span << >> BDC"),
    (b"/P << /MCID 3 >> BDC /E1 gs", b"/P << /MCID 3 >> BDC /E1 gs"),  # names that only start the same
])
def test_replacement_text_is_stripped_from_content(data, expected):
    assert b" ".join(strip_replacement_strings(data).split()) == b" ".join(expected.split())


@pytest.mark.slow
def test_the_whole_corpus_passes_in_one_workspace(corpus, tmp_path):
    """Values found in one document are checked for in every other: a false positive in
    one (a form's revision date taken for an account number) fails the rest."""
    src, manifest, profile = corpus
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    matcher = ProfileMatcher(Profile.from_dict(profile))
    vault = Vault(ws.vault_dir / "placeholders.json")
    failed = {}
    for d in manifest["documents"]:
        (ws.inbox / d["file"]).write_bytes((src / d["file"]).read_bytes())
        report = process(ws.inbox / d["file"], ws, matcher, vault).report
        if not report.passed:
            failed[d["file"]] = [(i.check, i.kind, i.page) for i in report.issues]
    assert not failed
