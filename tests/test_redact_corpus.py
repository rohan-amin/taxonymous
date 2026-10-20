"""M1 acceptance: every text-layer fixture, redacted with the household profile."""

import re

import pymupdf


def norm(s: str) -> str:
    return re.sub(r"[\s\-]", "", s).lower()


def test_no_identifier_survives(redacted):
    results, _ = redacted
    leaks = {}
    for name, (d, res) in results.items():
        text = norm(res.text.read_text())
        pdf_text = norm(" ".join(p.get_text() for p in pymupdf.open(res.pdf)))
        found = [i["value"] for i in d["redact"] if norm(i["value"]) in text or norm(i["value"]) in pdf_text]
        if found:
            leaks[name] = found
    assert not leaks


def test_amounts_and_public_names_survive(redacted):
    results, _ = redacted
    lost = {}
    for name, (d, res) in results.items():
        text = norm(res.text.read_text())
        missing = [i["value"] for i in d["keep"] if norm(i["value"]) not in text]
        if missing:
            lost[name] = missing[:10]
    assert not lost


def test_other_peoples_ssns_are_redacted_and_their_other_details_kept(redacted):
    """A household employee's or a beneficiary's SSN goes though nobody listed it; their
    name and other numbers stay (in `keep`, checked above)."""
    results, _ = redacted
    third = {name for name, (d, _) in results.items() for i in d["redact"] if i["third_party"]}
    assert third, "the corpus has someone else's SSN somewhere"
    kept = {name for name, (d, _) in results.items() for i in d["keep"] if i["type"] in ("NAME", "EIN")}
    assert kept


def test_outputs_are_scrubbed(redacted):
    results, _ = redacted
    for name, (_, res) in results.items():
        doc = pymupdf.open(res.pdf)
        assert not any(v for k, v in doc.metadata.items() if k not in ("format", "encryption")), name
        assert doc.get_xml_metadata() == "", name
        assert doc.embfile_count() == 0, name
        for page in doc:
            assert page.first_annot is None, name
            assert page.first_widget is None, name
            assert not page.get_links(), name


def test_every_redaction_leaves_a_placeholder_in_the_text(redacted):
    results, _ = redacted
    for name, (_, res) in results.items():
        text = res.text.read_text()
        missing = [ph for ph in res.placeholders if ph not in text]
        assert not missing, f"{name}: {missing}"


def test_same_value_gets_same_placeholder_across_documents(redacted, corpus):
    results, vault = redacted
    _, _, profile = corpus
    ssn = vault.by_key["ssn:0"]  # the taxpayer
    for name in ["w2.pdf", "1099_int.pdf", "k1_1065.pdf", "5498.pdf"]:
        assert ssn in results[name][1].text.read_text(), name
    entry = vault.entries[ssn]
    assert profile["people"][0]["ssn"] in entry["values"]


def test_masked_value_maps_to_the_full_values_placeholder(redacted):
    results, vault = redacted
    text = results["brokerage_consolidated.pdf"][1].text.read_text()
    # "XXXX-1234" in page headers and the full account number share one placeholder
    masked = [ph for ph, e in vault.entries.items() if any(v.startswith("XXXX-") for v in e["values"])]
    assert len(masked) == 1
    values = vault.entries[masked[0]]["values"]
    assert any(v.isdigit() for v in values), values
    assert text.count(masked[0]) >= 6
