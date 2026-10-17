"""Redact a text-layer PDF.

1. Flatten form fields and annotations so their values become page text
   (and get scanned like everything else).
2. Detect identifiers on every page.
3. Apply true redactions: the covered text, image pixels and drawings are
   removed from the file, and a placeholder like <SSN_A> is written in.
4. Scrub metadata, XMP, attachments, JavaScript, links, hidden text.
5. Write the placeholder text by re-extracting the redacted PDF.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .detect import Finding, ProfileMatcher, detect
from .pagetext import extract
from .vault import Vault


@dataclass
class Result:
    source: Path
    pdf: Path
    text: Path
    findings: list[Finding] = field(default_factory=list)
    placeholders: dict[str, str] = field(default_factory=dict)  # placeholder -> kind

    def counts(self) -> Counter:
        return Counter(f.kind for f in self.findings)


def _fontsize(rect: pymupdf.Rect, text: str) -> float:
    by_width = rect.width / pymupdf.get_text_length(text, fontname="helv", fontsize=1)
    return max(2.0, min(rect.height * 0.9, by_width, 10))


def redact_pdf(src: Path, out_dir: Path, matcher: ProfileMatcher, vault: Vault) -> Result:
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = Result(src, out_dir / f"{src.stem}.redacted.pdf", out_dir / f"{src.stem}.redacted.txt")

    doc = pymupdf.open(src)
    if doc.needs_pass:
        raise ValueError(f"{src.name} is password-protected; remove the password first")
    doc.bake(annots=True, widgets=True)

    for page in doc:
        pt = extract(page)
        labels: list[tuple[pymupdf.Rect, str]] = []
        for f in detect(pt, matcher):
            ph = vault.placeholder(f.kind, f.key, f.text)
            result.findings.append(f)
            result.placeholders[ph] = f.kind
            for i, line_rect in enumerate(pt.rects(f.start, f.end)):
                # trim to the middle of the line: char boxes are taller than glyphs and
                # overlap the lines above/below, which would otherwise be removed too
                h = line_rect.height
                rect = pymupdf.Rect(line_rect.x0, line_rect.y0 + 0.2 * h,
                                    line_rect.x1, line_rect.y1 - 0.2 * h)
                page.add_redact_annot(rect, fill=(0, 0, 0))
                if i == 0:
                    labels.append((rect, ph))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        # placeholders go in after the real text is gone; insert_text never drops text
        # that doesn't fit, so every redaction is visible in the text layer
        for rect, ph in labels:
            fs = _fontsize(rect, ph)
            page.insert_text((rect.x0 + 0.5, rect.y1 - (rect.height - fs) / 2 - 0.15 * fs), ph,
                             fontname="helv", fontsize=fs, color=(1, 1, 1))

    doc.scrub(attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
              javascript=True, metadata=True, redactions=False, remove_links=True, reset_fields=True,
              reset_responses=True, thumbnails=True, xml_metadata=True)
    for page in doc:
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for widget in list(page.widgets()):
            page.delete_widget(widget)
    doc.set_metadata({})
    doc.save(result.pdf, garbage=4, deflate=True, clean=True)
    doc.close()

    out = pymupdf.open(result.pdf)
    pages = [f"=== Page {p.number + 1} ===\n{p.get_text(sort=True).rstrip()}\n" for p in out]
    result.text.write_text(f"[{src.name} — redacted by taxonymous]\n\n" + "\n".join(pages))
    vault.save()
    return result
