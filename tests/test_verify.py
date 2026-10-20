"""M2 acceptance: the verification gate passes clean output and catches every kind of leak."""

import json
import shutil

import pymupdf
import pytest

from taxonymous import verify as verify_mod
from taxonymous.cli import main as cli
from taxonymous.detect import ProfileMatcher, detect
from taxonymous.pagetext import extract
from taxonymous.pipeline import Workspace, process, reverify, run
from taxonymous.profile import Profile
from taxonymous.redact import Overrides
from taxonymous.sanitize import allow_list
from taxonymous.vault import Vault
from taxonymous.verify import Known, verify


@pytest.fixture(scope="module")
def matcher(corpus):
    return ProfileMatcher(Profile.from_dict(corpus[2]))


def known(result) -> list[Known]:
    return [Known(f.kind, f.text) for f in result.findings]


def box_only(src, dest, matcher):
    """A broken redactor: draws black boxes over the findings but removes nothing."""
    doc = pymupdf.open(src)
    doc.bake()
    for page in doc:
        pt = extract(page)
        for f in detect(pt, matcher):
            for rect in pt.rects(f.start, f.end):
                page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0))
    doc.set_metadata({})
    doc.save(dest)


def image_only_pdf(path, text: str):
    """A page whose only content is a picture of `text` (no text layer)."""
    scratch = pymupdf.open()
    scratch.new_page(width=400, height=60).insert_text((10, 35), text, fontsize=20)
    png = scratch[0].get_pixmap(dpi=200).tobytes("png")
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=60)
    page.insert_image(page.rect, stream=png)
    allow_list(doc)  # MuPDF writes its name into the catalog; the redactor removes it too
    doc.save(path)


def locked_pdf(path):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "statement", fontsize=11)
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")


def test_every_correctly_redacted_fixture_passes_with_ocr(redacted, matcher):
    results, _ = redacted
    failures = {}
    for name, (_, res) in results.items():
        report = verify(res.pdf, matcher, known(res), res.text)
        if not report.passed:
            failures[name] = [(i.check, i.kind, i.page) for i in report.issues][:5]
    assert not failures


def test_box_only_redaction_is_caught_on_every_fixture(redacted, matcher, tmp_path):
    results, _ = redacted
    missed = []
    for name, (_, res) in results.items():
        dest = tmp_path / name
        box_only(res.source, dest, matcher)
        report = verify(dest, matcher, known(res), ocr=False)
        if report.passed or not any(i.check == "text" for i in report.issues):
            missed.append(name)
    assert not missed


def test_value_hidden_under_a_drawn_rectangle_is_caught(matcher, corpus, tmp_path):
    ssn = corpus[2]["people"][0]["ssn"]
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), f"SSN {ssn}", fontsize=11)
    page.draw_rect(pymupdf.Rect(60, 55, 250, 80), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(tmp_path / "covered.pdf")
    report = verify(tmp_path / "covered.pdf", matcher, [], ocr=False)
    assert not report.passed and any(i.check == "text" and i.kind == "SSN" for i in report.issues)


def test_identifier_in_image_pixels_is_only_caught_by_ocr(matcher, corpus, tmp_path):
    ssn = corpus[2]["people"][0]["ssn"]
    image_only_pdf(tmp_path / "scan.pdf", f"SSN {ssn}")
    assert verify(tmp_path / "scan.pdf", matcher, [], ocr=False).passed
    report = verify(tmp_path / "scan.pdf", matcher, [])
    assert not report.passed and {i.check for i in report.issues} == {"ocr"}


def _clean_copy(redacted, tmp_path, name="receipt.pdf"):
    _, res = redacted[0][name]
    dest = tmp_path / name
    shutil.copyfile(res.pdf, dest)
    return dest, res


@pytest.mark.parametrize("damage, kind", [
    (lambda doc: doc.set_metadata({"author": "someone"}), "metadata"),
    (lambda doc: doc[0].add_text_annot((20, 20), "note"), "annotations"),
    (lambda doc: doc.embfile_add("notes.txt", b"hello"), "attachments"),
    (lambda doc: doc[0].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(0, 0, 9, 9),
                                     "uri": "https://example.com"}), "links"),
])
def test_structural_leaks_are_caught(redacted, matcher, tmp_path, damage, kind):
    src, res = _clean_copy(redacted, tmp_path)
    doc = pymupdf.open(src)
    damage(doc)
    doc.save(tmp_path / "damaged.pdf")
    report = verify(tmp_path / "damaged.pdf", matcher, known(res), ocr=False)
    assert not report.passed and kind in {i.kind for i in report.issues}


def test_javascript_is_caught(redacted, matcher, tmp_path):
    src, res = _clean_copy(redacted, tmp_path)
    doc = pymupdf.open(src)
    js = doc.get_new_xref()
    doc.update_object(js, "<< /S /JavaScript /JS (app.alert(1)) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    doc.save(tmp_path / "js.pdf")
    report = verify(tmp_path / "js.pdf", matcher, known(res), ocr=False)
    assert "javascript-or-actions" in {i.kind for i in report.issues}


def test_leak_in_placeholder_text_file_is_caught(redacted, matcher, corpus, tmp_path):
    src, res = _clean_copy(redacted, tmp_path)
    txt = tmp_path / "receipt.txt"
    txt.write_text(res.text.read_text() + "\nmember " + corpus[2]["emails"][0])
    report = verify(src, matcher, known(res), txt, ocr=False)
    assert {i.check for i in report.issues} == {"txt"}


def test_missing_tesseract_fails_closed(redacted, matcher, tmp_path, monkeypatch):
    src, res = _clean_copy(redacted, tmp_path)
    monkeypatch.setattr(verify_mod.ocr_mod.shutil, "which", lambda _: None)
    report = verify(src, matcher, known(res))
    assert not report.passed and report.issues[0].check == "error"


def test_ocr_tolerates_misread_digits(matcher):
    from taxonymous.verify import Scanner
    scanner = Scanner(matcher, [Known("ACCT", "6346191008")])
    assert scanner.scan("Acct 634619IOO8", fuzzy=True) == ["ACCT"]
    assert scanner.scan("Acct 634619IOO8", fuzzy=False) == []


def test_workspace_releases_only_verified_files(corpus, tmp_path):
    src, _, profile = corpus
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    for name in ["w2.pdf", "receipt.pdf"]:
        shutil.copyfile(src / name, ws.inbox / name)
    image_only_pdf(ws.inbox / "photo_of_card.pdf", f"SSN {profile['people'][0]['ssn']}")
    # a file that can't be redacted is held back without stopping the others
    locked_pdf(ws.inbox / "locked.pdf")
    (ws.ready / "locked.redacted.pdf").write_bytes(b"stale release from an earlier run")

    outcomes = {o.source.name: o for o in run(ws)}

    assert all(outcomes[n].report.passed for n in ["w2.pdf", "receipt.pdf", "photo_of_card.pdf"])
    assert not outcomes["locked.pdf"].report.passed
    assert outcomes["locked.pdf"].report.issues[0].check == "error"
    assert sorted(p.name for p in ws.ready.iterdir()) == [
        "photo_of_card.redacted.pdf", "receipt.redacted.pdf", "w2.redacted.pdf"]
    reports = " ".join(p.read_text() for p in ws.work.glob("*.report.json"))
    assert profile["people"][0]["ssn"] not in reports  # reports never contain real values
    assert (ws.inbox / "w2.pdf").read_bytes() == (src / "w2.pdf").read_bytes()  # originals untouched


def test_cli_run_exit_code_reflects_failures(corpus, tmp_path, capsys):
    _, _, profile = corpus
    ws = Workspace(tmp_path / "ws")
    assert cli(["init", "--workspace", str(ws.root)]) == 0
    ws.profile_path.write_text(json.dumps(profile))
    locked_pdf(ws.inbox / "bad.pdf")
    assert cli(["run", "--workspace", str(ws.root)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_showing_half_of_a_wrapped_name_excuses_all_of_it(tmp_path):
    """A name wrapped onto two lines is two boxes. Showing one leaves the whole name
    readable, so verification must excuse both halves, and still nothing else."""
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery Abernathy"}]}))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((400, 100), "Paid to: Avery", fontsize=11)
    page.insert_text((72, 114), "Abernathy for services", fontsize=11)
    page.insert_text((72, 300), "Recipient Avery Abernathy", fontsize=11)
    doc.save(ws.inbox / "letter.pdf")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    out = process(ws.inbox / "letter.pdf", ws, matcher, vault, release=False)
    assert out.report.passed, out.report.issues
    wrapped = sorted((r for r in out.result.regions if r.rect[1] < 200), key=lambda r: r.rect[1])
    assert len(wrapped) == 2
    o = Overrides()
    o.show.append((0, wrapped[0].rect, wrapped[0].placeholder))
    o.save(ws.overrides_path("letter"))
    out = process(ws.inbox / "letter.pdf", ws, matcher, vault, release=False)
    text = pymupdf.open(ws.outputs("letter")["pdf"])[0].get_text()
    assert "Avery" in text and "Abernathy for" in text and "Recipient Avery" not in text
    assert out.report.passed, out.report.issues
    assert reverify(ws, "letter", matcher, vault).passed
