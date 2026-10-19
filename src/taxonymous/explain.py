"""Why did verification fail? Each leak with its surroundings, with the values masked.

For sharing a problem without sharing the document: every letter becomes a/A and every
digit #, except common form words ("Employer", "ID", "State") and placeholders, so what
is left is the layout around the leak, not what it says.

  page 5 · EIN ##-####### · a value redacted in this document · seen in: text layer, OCR
      …State wages #,###.## AA##-####### Aaaaa…
      not in one piece in the redactor's reading of the original page
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from . import ocr as ocr_mod
from .detect import (
    FORM_WORDS,
    NOT_AMOUNT,
    NUMBER_SEP,
    ProfileMatcher,
    after_omb,
    detect,
    digits,
    is_caption,
    is_number_value,
    number_regex,
    pattern_hits,
)
from .pagetext import PageText, extract
from .profile import Profile
from .vault import Vault
from .verify import CONFUSABLE, OCR_DPI, PAGE_HEADER_RE, PLACEHOLDER_RE, _passes

CONTEXT = 30  # characters either side, extended to whole words
KEEP_WORDS = FORM_WORDS | {
    "id", "ein", "ssn", "tin", "itin", "no", "nbr", "num", "account", "acct", "address", "street", "name",
    "wages", "tax", "taxes", "withheld", "control", "code", "social", "security", "income", "medicare",
    "tips", "statement", "employer", "employers", "identification", "deferred", "compensation", "dependent",
    "care", "benefits", "allocated", "nonqualified", "plans", "retirement", "plan", "third", "party", "sick",
    "pay", "gross", "net", "ytd", "current", "period", "summary", "detail", "information", "reference",
    "routing", "card", "member", "policy", "loan", "phone", "email", "fax", "tel", "birth", "dob", "apt",
    "suite", "ste", "unit", "po", "st", "ave", "rd", "dr", "ln", "blvd", "w", "c", "b", "a", "d",
    "irs", "omb", "department", "treasury", "service", "internal", "revenue", "file", "with", "for", "to",
    "by", "on", "in", "at", "is", "this", "your", "you", "are", "not", "if", "from", "box", "boxes",
}


def mask(text: str) -> str:
    """Letters to a/A and digits to #, except form words and placeholders."""
    out, pos = [], 0
    for m in re.finditer(r"<[A-Z]+_[A-Z0-9]+>|[A-Za-z]+|\d", text):
        out.append(text[pos:m.start()])
        token = m.group()
        if token.startswith("<") or token.lower() in KEEP_WORDS:
            out.append(token)
        elif token.isdigit():
            out.append("#")
        else:
            out.append("".join("A" if c.isupper() else "a" for c in token))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out).replace("\n", " ⏎ ")


@dataclass
class Value:
    kind: str
    text: str
    origin: str  # "this document" | "another document (its file, as its placeholder)" | "your details"


@dataclass
class Hit:
    page: int | None  # 1-based
    kind: str
    why: str
    value: str  # masked
    context: str  # masked
    checks: list[str]  # text | txt | ocr: where it was seen
    split: bool = False  # the redactor's reading of the original page doesn't have it in one piece


def _regex(v: Value, fuzzy: bool) -> re.Pattern:
    if is_number_value(v.text):
        ds = digits(v.text)
        if fuzzy:
            parts = [f"[{re.escape(CONFUSABLE.get(d, d))}]" for d in ds]
            return re.compile(r"(?<![0-9])" + NOT_AMOUNT + NUMBER_SEP.join(parts) + r"(?![0-9])")
        return re.compile(number_regex(ds))
    words = re.findall(r"[A-Za-z0-9]+", v.text)
    between = r"[^A-Za-z0-9]+"
    return re.compile(r"(?<![A-Za-z0-9])" + between.join(map(re.escape, words)) + r"(?![A-Za-z0-9])", re.I)


def _around(text: str, start: int, end: int, context: int = CONTEXT) -> str:
    a, b = max(0, start - context), min(len(text), end + context)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    return ("…" if a else "") + mask(text[a:b]).strip() + ("…" if b < len(text) else "")


def _hits(text: str, values: list[Value], matcher: ProfileMatcher,
          fuzzy: bool) -> list[tuple[str, str, int, int]]:
    """(kind, why, start, end) for everything verification would object to in `text`; a
    match inside a longer one of the same kind (a first name inside the full name) is left out."""
    text = PLACEHOLDER_RE.sub(lambda m: " " * len(m.group()), text)
    out = [(kind, "it has the shape of one", m.start(), m.end()) for m, kind in pattern_hits(text)]
    out += [(f.kind, "it is in your details", f.start, f.end) for f in matcher.find(PageText(0, text))]
    for v in values:
        out += [(v.kind, f"a value redacted in {v.origin}", m.start(), m.end())
                for m in _regex(v, fuzzy).finditer(text)
                if not (is_number_value(v.text) and after_omb(text, m.start()))]
    out.sort(key=lambda h: h[2] - h[3])  # longest first
    kept: list[tuple[str, str, int, int]] = []
    for h in out:
        if not any(k[0] == h[0] and k[2] <= h[2] and h[3] <= k[3] for k in kept):
            kept.append(h)
    return sorted(kept, key=lambda h: h[2])


def _values(ws_vault: Vault, regions: list[dict], used: dict[str, list[str]]) -> list[Value]:
    here = {r["placeholder"] for r in regions}

    def origin(ph):
        if ph in here:
            return "this document"
        return f"another document ({', '.join(used[ph])}, as {ph[1:-1]})"
    return [Value(e["kind"], v, origin(ph))
            for ph, e in ws_vault.entries.items() if ph in used for v in e["values"]
            if len(v.strip()) >= 4 and not is_caption(v)]  # what verification looks for


def explain(workspace, stem: str, ocr: bool = True) -> list[Hit]:
    from .pipeline import Workspace, allowed_values, users
    from .redact import Overrides, output_stem

    ws = Workspace(Path(workspace))
    out = ws.outputs(stem)
    report = json.loads(out["report"].read_text())
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    allowed = allowed_values(vault, Overrides.load(ws.overrides_path(stem)))
    regions = json.loads(out["regions"].read_text()) if out["regions"].exists() else []
    values = [v for v in _values(vault, regions, users(ws)) if v.text not in allowed]
    source = next(p for p in ws.pending() if output_stem(p) == stem)
    original = pymupdf.open(source) if source.suffix.lower() == ".pdf" else None
    doc = pymupdf.open(out["pdf"])
    hits: dict[tuple, Hit] = {}

    def add(check, page, text, fuzzy):
        nth: dict[tuple, int] = {}
        for kind, why, s, e in _hits(text, values, matcher, fuzzy):
            value = PLACEHOLDER_RE.sub(lambda m: " " * len(m.group()), text)[s:e]  # as _hits read it
            shape = (page, kind, mask(value))
            nth[shape] = nth.get(shape, 0) + 1
            key = (*shape, nth[shape])  # the first, second... spot that looks like this
            if key in hits:  # the same leak seen by another check
                if check not in hits[key].checks:
                    hits[key].checks.append(check)
                continue
            hit = hits[key] = Hit(page, kind, why, mask(value), _around(text, s, e), [check])
            if page and original is not None and page <= original.page_count:
                reading = extract(original[page - 1]).text
                hit.split = not _regex(Value(kind, value, ""), fuzzy=False).search(reading)

    pages_with = {(i["check"], i["page"]) for i in report["issues"]}
    for page in doc:
        if ("text", page.number + 1) in pages_with:
            add("text", page.number + 1, page.get_text(), False)
    if any(c == "txt" for c, _ in pages_with):
        parts = PAGE_HEADER_RE.split(out["txt"].read_text())
        for n, body in zip(parts[1::2], parts[2::2], strict=True):
            add("txt", int(n), body, True)
    if ocr:
        for page in doc:
            if ("ocr", page.number + 1) in pages_with:
                png = page.get_pixmap(dpi=OCR_DPI, colorspace=pymupdf.csGRAY).tobytes("png")
                for text in ocr_mod.run_all([(png, psm) for psm in _passes(page)]):
                    add("ocr", page.number + 1, text, True)
    return list(hits.values())


def render(hits: list[Hit], issues: list[dict]) -> str:
    if not issues:
        return "Verification passed: nothing to explain."
    lines = []
    structure = sorted({i["kind"] for i in issues if i["check"] in ("structure", "raw", "error")})
    if structure:
        lines.append("Other problems: " + ", ".join(structure))
    where = {"text": "text layer", "txt": "text file", "ocr": "OCR"}
    for h in hits:
        page = f"page {h.page} · " if h.page else ""
        lines.append(f"{page}{h.kind} {h.value} · {h.why} · seen in: {', '.join(where[c] for c in h.checks)}")
        lines.append(f"    {h.context}")
        if h.split:
            lines.append("    not in one piece in the redactor's reading of the original page")
    return "\n".join(lines)


SOURCES = {"profile": "your details", "pattern": "its shape", "label": "a nearby label",
           "ner": "the name model", "repeat": "appearing elsewhere in the document", "manual": "you"}


def explain_placeholder(workspace, stem: str, placeholder: str) -> str:
    """Why one box is there: the value it stands for (masked), which detector found it and
    on which page, with what was around it, and every page it was redacted on. For a box
    that hides something it shouldn't, such as an amount taken for an ID."""
    from .pipeline import Workspace
    from .redact import output_stem

    ws = Workspace(Path(workspace))
    ph = placeholder if placeholder.startswith("<") else f"<{placeholder}>"
    vault = Vault(ws.vault_dir / "placeholders.json")
    entry = vault.entries.get(ph)
    if entry is None:
        return f"{ph} is not in the vault."
    out = ws.outputs(stem)
    regions = json.loads(out["regions"].read_text()) if out["regions"].exists() else []
    lines = [f"{ph} ({entry['kind']}) stands for: " + ", ".join(mask(v) for v in entry["values"])]
    boxes: dict[tuple[int, str], int] = {}
    for r in regions:
        if r["placeholder"] == ph:
            boxes[(r["page"] + 1, r["source"])] = boxes.get((r["page"] + 1, r["source"]), 0) + 1
    lines.append("Boxes in this document: " + ("; ".join(
        f"page {p} by {SOURCES.get(s, s)}" + (f" ×{n}" if n > 1 else "")
        for (p, s), n in sorted(boxes.items())) or "none"))

    source = next(p for p in ws.pending() if output_stem(p) == stem)
    if source.suffix.lower() != ".pdf":
        lines.append("(a photo: its OCR reads aren't shown)")
        return "\n".join(lines)
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    found = False
    lines.append("Found in the original by:")
    for page in pymupdf.open(source):
        pt = extract(page)
        if not pt.text.strip():
            continue  # a scanned page: read by OCR, not shown here
        hits = [f for f in detect(pt, matcher) if vault.by_key.get(f.key) == ph]
        for f in hits:
            found = True
            lines.append(f"  page {page.number + 1} · {SOURCES.get(f.source, f.source)} · {mask(f.text)}")
            lines.append(f"      {_around(pt.text, f.start, f.end, 60)}")
        for v in entry["values"]:  # what the repeat step redacts: copies no detector found
            for m in _regex(Value(entry["kind"], v, ""), fuzzy=False).finditer(pt.text):
                if not any(f.start < m.end() and m.start() < f.end for f in hits):
                    lines.append(f"  page {page.number + 1} · a copy of it: {mask(m.group())}")
                    lines.append(f"      {_around(pt.text, m.start(), m.end(), 60)}")
    if not found:
        lines.append("  no detector on a text page (it may come from a scanned page, or another document)")
    return "\n".join(lines)
