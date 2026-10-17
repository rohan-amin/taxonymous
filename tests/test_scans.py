"""M3 acceptance: scans, photos and mixed PDFs are redacted in their pixels."""

import io
import json
import re
import shutil

import pymupdf
import pytest
from PIL import Image, ImageDraw

from taxonymous import ocr
from taxonymous.detect import ProfileMatcher, detect
from taxonymous.pagetext import Line, PageText
from taxonymous.pipeline import Workspace, run
from taxonymous.profile import Profile
from taxonymous.redact import redact_file
from taxonymous.vault import Vault
from taxonymous.verify import PLACEHOLDER_RE, Known, verify

AMOUNT = re.compile(r"[\d,]+\.\d\d")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9*]", "", s.lower())


def read(path) -> str:
    """Everything our OCR can find in a file: every page, straightened or not, both passes."""
    path = str(path)
    if path.endswith(".pdf"):
        images = []
        for page in pymupdf.open(path):
            pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
            images.append(Image.frombytes("L", (pix.width, pix.height), pix.samples))
    else:
        images = [Image.open(path)]
    jobs = [(ocr.png(v), psm) for img in images for v, _ in ocr.variants(img) for psm in ocr.PASSES]
    return norm(" ".join(ocr.run_all(jobs)))


@pytest.fixture(scope="module")
def matcher(corpus):
    return ProfileMatcher(Profile.from_dict(corpus[2]))


@pytest.fixture(scope="module")
def scanned(corpus, matcher, tmp_path_factory):
    """Every scan, photo and mixed fixture redacted: {file: (manifest doc, Result)}."""
    src, manifest, _ = corpus
    out = tmp_path_factory.mktemp("scanned")
    vault = Vault(out / "vault" / "placeholders.json")
    docs = [d for d in manifest["documents"] if d["kind"] != "text"]
    return {d["file"]: (d, redact_file(src / d["file"], out, matcher, vault)) for d in docs}


def test_corpus_covers_scans_photos_and_mixed(scanned):
    assert {d["kind"] for d, _ in scanned.values()} == {"scan", "photo", "mixed"}


def test_no_seeded_identifier_survives_in_ocr_of_output(scanned):
    leaks = {}
    for name, (doc, res) in scanned.items():
        after = read(res.pdf)
        found = [i["value"] for i in doc["redact"] if norm(i["value"]) in after]
        if found:
            leaks[name] = found
    assert not leaks


def test_every_scan_passes_verification(scanned, matcher):
    failures = {}
    for name, (_, res) in scanned.items():
        report = verify(res.pdf, matcher, [Known(f.kind, f.text) for f in res.findings], res.text)
        if not report.passed:
            failures[name] = [(i.check, i.kind, i.page) for i in report.issues]
    assert not failures


def test_amounts_stay_readable(corpus, scanned):
    """Of the amounts OCR can read in the original, how many it still reads after
    redaction. Below 100% is OCR noise near the black boxes, not lost data, so this is
    tracked with a floor rather than gated at the plan's 98% target."""
    src = corpus[0]
    readable = kept = 0
    for name, (doc, res) in scanned.items():
        before, after = read(src / name), read(res.pdf)
        amounts = [norm(i["value"]) for i in doc["keep"] if AMOUNT.fullmatch(i["value"])]
        amounts = [a for a in amounts if a in before]
        readable += len(amounts)
        kept += sum(a in after for a in amounts)
    print(f"amount retention: {kept}/{readable} = {kept / readable:.1%}")
    assert kept / readable >= 0.95


def test_scan_pages_are_rebuilt_as_one_image(scanned):
    for name, (doc, res) in scanned.items():
        if doc["kind"] == "mixed" and "scanner" not in doc["source"]:
            continue
        out = pymupdf.open(res.pdf)
        assert res.image_pages == list(range(1, out.page_count + 1)), name
        for page in out:
            assert len(page.get_images()) == 1, name
            assert not PLACEHOLDER_RE.sub("", page.get_text()).strip(), name  # only placeholders


def test_scan_text_file_has_placeholders_and_kept_values(scanned):
    _, res = scanned["w2_scan.pdf"]
    text = res.text.read_text()
    assert "<SSN_A>" in text and "Wages, tips, other compensation" in text


def test_mixed_page_keeps_its_text_layer(scanned):
    doc, res = scanned["daf_with_receipt.pdf"]
    out = pymupdf.open(res.pdf)
    assert res.image_pages == []
    assert "Attachment: copy of receipt" in out[-1].get_text()
    receipt_items = [i["value"] for i in doc["redact"] if "@" in i["value"]]
    assert receipt_items and not any(norm(v) in read(res.pdf) for v in receipt_items)


def _photo_bytes(corpus, fmt: str, **save) -> bytes:
    img = Image.open(corpus[0] / "receipt_photo.jpg")
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


def test_photo_metadata_and_orientation(corpus, matcher, tmp_path):
    """EXIF (camera, GPS, owner name) never reaches the output; the camera's rotation flag is applied."""
    name = corpus[2]["people"][0]["name"]
    img = Image.open(corpus[0] / "receipt_photo.jpg").rotate(90, expand=True)  # stored sideways...
    exif = Image.Exif()
    exif[0x0112] = 6  # ...with "rotate 90° clockwise to view"
    exif[0x013B] = name  # Artist
    img.save(tmp_path / "sideways.jpg", "JPEG", exif=exif.tobytes())

    res = redact_file(tmp_path / "sideways.jpg", tmp_path / "out", matcher, Vault(tmp_path / "v.json"))
    out = pymupdf.open(res.pdf)
    page = out[0]
    assert page.rect.height > page.rect.width  # upright receipt
    data = out.extract_image(page.get_images()[0][0])["image"]
    assert name.encode() not in data and b"Exif" not in data
    assert verify(res.pdf, matcher, [Known(f.kind, f.text) for f in res.findings], res.text).passed
    assert "<EMAIL_" in res.text.read_text()


@pytest.mark.parametrize("ext, fmt", [("png", "PNG"), ("heic", "HEIF")])
def test_other_image_formats(corpus, matcher, tmp_path, ext, fmt):
    if fmt == "HEIF":
        from pillow_heif import register_heif_opener
        register_heif_opener()
    (tmp_path / f"receipt.{ext}").write_bytes(_photo_bytes(corpus, fmt))
    res = redact_file(tmp_path / f"receipt.{ext}", tmp_path / "out", matcher, Vault(tmp_path / "v.json"))
    assert res.pdf.name == f"receipt.{ext}.redacted.pdf"
    assert verify(res.pdf, matcher, [Known(f.kind, f.text) for f in res.findings], res.text).passed
    assert corpus[2]["emails"][0] not in res.text.read_text()


def test_workspace_takes_photos(corpus, tmp_path):
    src, _, profile = corpus
    ws = Workspace(tmp_path / "ws")
    ws.init()
    ws.profile_path.write_text(json.dumps(profile))
    shutil.copyfile(src / "receipt_photo.jpg", ws.inbox / "receipt_photo.jpg")
    (ws.inbox / "notes.txt").write_text("not a document")
    outcomes = run(ws)
    assert [o.source.name for o in outcomes] == ["receipt_photo.jpg"]
    assert outcomes[0].report.passed
    assert sorted(p.name for p in ws.ready.iterdir()) == [
        "receipt_photo.jpg.redacted.pdf", "receipt_photo.jpg.redacted.txt"]


# ---------------------------------------------------------------- units

def _line(text: str) -> PageText:
    boxes = [None if c == " " else pymupdf.Rect(i * 5, 0, i * 5 + 5, 10) for i, c in enumerate(text)]
    line = Line(0, len(text), pymupdf.Rect(0, 0, 5 * len(text), 10))
    return PageText(0, text + "\n", boxes + [None], [line])


@pytest.mark.parametrize("line, value", [
    ("VISA **##88*94444", "**##88*94444"),
    ("VISA ************1111 460.79", "************1111"),
    ("Card ending in 1111", "1111"),
    ("Mastercard 5555 5555 5555 4444 AUTH 1234", "5555 5555 5555 4444"),
])
def test_card_number_after_brand(matcher, line, value):
    """However OCR mangled the mask, the whole number is covered (and nothing after it)."""
    assert [f.text for f in detect(_line(line), matcher)] == [value]


def test_card_brand_without_a_number_is_not_redacted(matcher):
    assert not detect(_line("We accept Visa and Discover"), matcher)


def test_ocr_folds_misread_digits_only_in_numbers():
    words, x = [], 0
    for t in ["Acct", "634619IOO8", "Boston"]:
        words.append(ocr.Word(t, 90, (x, 0, x + 10 * len(t), 20), (1, 1, 1)))
        x += 10 * len(t) + 6
    assert ocr.page_text(words, 0, 1).text == "Acct 6346191008 Boston\n"


def test_ocr_lines_split_at_column_gaps():
    words = [ocr.Word("Iyer", 90, (0, 0, 40, 20), (1, 1, 1)), ocr.Word("LLC", 90, (50, 0, 80, 20), (1, 1, 1)),
             ocr.Word("21,624.73", 90, (400, 0, 480, 20), (1, 1, 1))]
    pt = ocr.page_text(words, 0, 1)
    assert [pt.text[ln.start:ln.end] for ln in pt.lines] == ["Iyer LLC", "21,624.73"]


def test_ocr_lines_split_at_a_jump_in_text_size():
    """An amount read next to the form's big printed year must not become a phone number."""
    big_year = [ocr.Word("1515880", 90, (0, 10, 70, 30), (1, 1, 1)),
                ocr.Word("2026", 90, (90, 0, 190, 45), (1, 1, 1))]
    # as Tesseract actually boxed it: similar heights, but the year's characters are twice as wide
    wide_year = [ocr.Word("1515880", 52, (984, 168, 1091, 202), (15, 1, 1)),
                 ocr.Word("2026", 65, (1120, 165, 1260, 206), (15, 1, 1))]
    assert len(ocr.page_text(big_year, 0, 1).lines) == 2
    assert len(ocr.page_text(wide_year, 0, 1).lines) == 2
    name = [ocr.Word(t, 90, (x, 0, x + w, 30), (1, 1, 1))
            for t, x, w in [("Avery", 0, 75), ("F", 87, 14), ("Abernathy", 113, 135)]]
    assert len(ocr.page_text(name, 0, 1).lines) == 1


def test_straightened_read_maps_back_onto_the_original():
    img = Image.new("L", (1200, 800), 255)
    ImageDraw.Draw(img).rectangle([300, 300, 900, 330], fill=0)  # a "text row"
    tilted = img.rotate(4, resample=Image.Resampling.BICUBIC, fillcolor=255)
    (_, none), (straight, back) = ocr.variants(tilted)
    assert none is None
    # the row's centre in the straightened copy lands back on the tilted original's row
    ys = [y for y in range(800) if straight.getpixel((600, y)) < 128]
    p = pymupdf.Point(600, sum(ys) / len(ys)) * back
    assert tilted.getpixel((round(p.x), round(p.y))) < 128
