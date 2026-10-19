"""What redaction must leave alone around a value.

Old PDFs (some payroll W-2s) draw their captions in form XObjects that have no
/Resources of their own and borrow the page's fonts. Redaction rewrites the page's
resources down to what its own content uses, which used to drop the borrowed font: MuPDF
(and so the review UI) drew the captions in a stand-in font, macOS Preview left them out."""

import json

import pymupdf

from taxonymous.detect import ProfileMatcher
from taxonymous.pipeline import Workspace, process
from taxonymous.profile import Profile
from taxonymous.vault import Vault


def borrowing_pdf(path, nested=False):
    doc = pymupdf.open()
    page = doc.new_page()
    plain, bold, form = doc.get_new_xref(), doc.get_new_xref(), doc.get_new_xref()
    doc.update_object(plain, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    doc.update_object(bold, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold>>")
    doc.update_object(form, "<</Type/XObject/Subtype/Form/BBox[0 0 612 792]>>")
    caption = b"BT /F2 12 Tf 72 600 Td (Wages, tips, other comp.) Tj ET"
    if nested:  # a form inside a form, both borrowing
        inner = doc.get_new_xref()
        doc.update_object(inner, "<</Type/XObject/Subtype/Form/BBox[0 0 612 792]>>")
        doc.update_stream(inner, caption)
        doc.xref_set_key(form, "Resources", f"<</XObject<</Fm1 {inner} 0 R>>>>")
        caption = b"/Fm1 Do"
    doc.update_stream(form, caption)
    resources = f"<</Font<</F1 {bold} 0 R/F2 {plain} 0 R>>/XObject<</Fm0 {form} 0 R>>>>"
    if nested:  # the inner form borrows the fonts from the outer one
        doc.xref_set_key(form, "Resources", f"<</Font<</F2 {plain} 0 R>>/XObject<</Fm1 {inner} 0 R>>>>")
    doc.xref_set_key(page.xref, "Resources", resources)
    contents = doc.get_new_xref()
    doc.update_object(contents, "<<>>")
    doc.update_stream(contents, b"BT /F1 12 Tf 72 700 Td (Employee: Avery F Abernathy) Tj ET /Fm0 Do")
    doc.xref_set_key(page.xref, "Contents", f"{contents} 0 R")
    doc.save(path)


def fonts_of(page, text):
    return {s["font"] for b in page.get_text("dict")["blocks"] for line in b.get("lines", [])
            for s in line["spans"] if text in s["text"]}


def test_a_form_keeps_the_fonts_it_borrows_from_the_page(tmp_path):
    for nested in (False, True):
        ws = Workspace(tmp_path / str(nested))
        ws.init()
        ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery F Abernathy"}],
                                               "find_others": True}))
        borrowing_pdf(ws.inbox / "w2.pdf", nested)
        matcher = ProfileMatcher(Profile.load(ws.profile_path))
        outcome = process(ws.inbox / "w2.pdf", ws, matcher, Vault(ws.vault_dir / "p.json"), ocr=False,
                          release=False)
        assert outcome.report.passed, outcome.report.issues
        page = pymupdf.open(ws.outputs("w2")["pdf"])[0]
        assert "Abernathy" not in page.get_text()
        assert fonts_of(page, "Wages") == {"Helvetica"}, nested  # not "" (a font the file lost)


def test_a_caption_printed_just_above_a_value_stays(tmp_path):
    """MuPDF removes every character whose box touches a redaction. Text is removed only along
    the middle of the value's line, so the caption above it, whose box reaches into the
    value's, keeps all its letters."""
    ws = Workspace(tmp_path)
    ws.init()
    ws.profile_path.write_text(json.dumps({"people": [{"name": "Avery F Abernathy"}], "find_others": True}))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "b Employer's FED ID number", fontsize=10)
    page.insert_text((90, 108), "00-3456789", fontsize=10)
    doc.save(ws.inbox / "w2.pdf")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    outcome = process(ws.inbox / "w2.pdf", ws, matcher, Vault(ws.vault_dir / "p.json"), ocr=False,
                      release=False)
    assert outcome.report.passed, outcome.report.issues
    text = pymupdf.open(ws.outputs("w2")["pdf"])[0].get_text()
    assert "Employer's FED ID number" in text and "3456789" not in text
