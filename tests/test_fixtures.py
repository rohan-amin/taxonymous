"""The fixture corpus must be valid ground truth: fake, present, and reproducible."""

import json
import re
import subprocess

import pymupdf
import pytest

from tools.check_pii import ssn_allowed
from tools.fixtures.generate import generate


def norm(s: str) -> str:
    return re.sub(r"[\s\-]", "", s).lower()


def doc_text(path) -> str:
    doc = pymupdf.open(path)
    parts = [p.get_text() for p in doc] + [w.field_value or "" for p in doc for w in p.widgets()]
    return norm(" ".join(map(str, parts)))


def all_values(manifest, kind="redact"):
    return [(d["file"], i) for d in manifest["documents"] for i in d[kind]]


def test_every_identifier_is_from_a_reserved_fake_range(corpus):
    _, manifest, profile = corpus
    for _, item in all_values(manifest):
        v = item["value"]
        if item["type"] == "SSN":
            assert ssn_allowed(*v.split("-")), v
        if item["type"] == "EIN":
            assert v.startswith("00-"), v
        if item["type"] == "EMAIL":
            assert v.endswith("@example.com"), v
        if item["type"] == "PHONE":
            assert "555-01" in v, v
    assert all(ssn_allowed(*p["ssn"].split("-")) for p in profile["people"])


def test_text_documents_contain_their_ground_truth(corpus):
    out, manifest, _ = corpus
    for d in manifest["documents"]:
        if d["kind"] != "text":
            continue
        text = doc_text(out / d["file"])
        missing = [i["value"] for i in d["redact"] + d["keep"] if norm(i["value"]) not in text]
        assert not missing, f"{d['file']}: {missing[:5]}"


def test_every_document_has_identifiers_and_kept_values(corpus):
    _, manifest, _ = corpus
    for d in manifest["documents"]:
        assert d["redact"], d["file"]
        assert d["keep"], d["file"]


def test_nothing_is_both_redacted_and_kept(corpus):
    _, manifest, _ = corpus
    for d in manifest["documents"]:
        assert not {i["value"] for i in d["redact"]} & {i["value"] for i in d["keep"]}, d["file"]


def test_third_parties_are_in_corpus_but_not_in_profile(corpus):
    _, manifest, profile = corpus
    third = {i["value"] for _, i in all_values(manifest) if i["third_party"]}
    assert third, "corpus needs third-party PII (household employee, IRA beneficiaries)"
    profile_text = json.dumps(profile)
    assert not [v for v in third if v in profile_text]


def test_scans_have_no_text_layer(corpus):
    out, manifest, _ = corpus
    for d in manifest["documents"]:
        if d["kind"] == "scan":
            assert not pymupdf.open(out / d["file"])[0].get_text().strip(), d["file"]


@pytest.mark.parametrize("name", ["w2_scan.pdf", "receipt_photo.jpg"])
def test_scans_are_readable_by_ocr(corpus, name, tmp_path):
    """Ground truth for scans is only meaningful if OCR can read most of it."""
    out, manifest, _ = corpus
    d = next(d for d in manifest["documents"] if d["file"] == name)
    img = out / name
    if name.endswith(".pdf"):
        img = tmp_path / "page.png"
        pymupdf.open(out / name)[0].get_pixmap(dpi=200).save(img)
    text = norm(subprocess.run(["tesseract", str(img), "-"], capture_output=True, text=True).stdout)
    found = [i for i in d["redact"] if norm(i["value"]) in text]
    assert len(found) >= 0.6 * len(d["redact"]), f"OCR found {len(found)}/{len(d['redact'])}"


def test_brokerage_stats_are_recorded(corpus):
    _, manifest, _ = corpus
    d = next(d for d in manifest["documents"] if d["file"] == "brokerage_consolidated.pdf")
    assert d["stats"]["pages"] == 6
    assert d["stats"]["transaction_rows"] > 100


def test_generation_is_deterministic(tmp_path):
    a = generate(tmp_path / "a", seed=11, brokerage_pages=2)
    b = generate(tmp_path / "b", seed=11, brokerage_pages=2)
    c = generate(tmp_path / "c", seed=12, brokerage_pages=2)
    assert a == b
    assert a != c
    for d in a["documents"]:
        if d["kind"] == "text":
            assert doc_text(tmp_path / "a" / d["file"]) == doc_text(tmp_path / "b" / d["file"]), d["file"]
        else:
            assert (tmp_path / "a" / d["file"]).read_bytes() == (tmp_path / "b" / d["file"]).read_bytes()
