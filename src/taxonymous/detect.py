"""Find identifiers on a page. Document-agnostic: the same detectors run on
every page of every document.

Four detectors, highest priority first:
  profile  - the user's own known values, matched with formatting tolerance
  pattern  - shapes that are identifiers wherever they appear (SSN, EIN, email, ...)
  label    - text next to a PII label ("Account number", "RECIPIENT'S name", ...)
  ner      - people (and private organizations) named in prose, by a local
             language model (names.py)

Then, across the whole document, anything one detector found is found again
wherever else it appears ("repeat"): a name in a letter's heading and the same
name in a footnote on page 30.

Pattern and label findings never cover a dollar amount.
"""

import re
from dataclasses import dataclass

from . import names
from .pagetext import Line, PageText
from .profile import Profile

IC = re.IGNORECASE
PRIORITY = {"profile": 4, "pattern": 3, "label": 2, "ner": 1, "repeat": 0}
RIGHT_GAP = 200  # points: how far right of a label its value may sit on the same row
REPEAT_MIN = 4  # shorter values are not searched for elsewhere in the document


@dataclass
class Finding:
    page: int
    start: int
    end: int
    kind: str  # placeholder prefix: SSN EIN TIN ACCT CARD ROUTING ID NAME ORG ADDR PHONE EMAIL DOB
    key: str  # identity used to give the same value the same placeholder
    source: str  # profile | pattern | label | ner | repeat
    text: str = ""


# ---------------------------------------------------------------- helpers

AMOUNT_RE = re.compile(r"^\(?-?\$?\s*-?\d[\d,]*\.\d{2}\)?%?$")
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b", IC)
TIN_VALUE_RE = re.compile(r"(?<![\w*•])(?:[\dXx*•]{3}[- ]?[\dXx*•]{2}[- ]?\d{4}|\d{2}-?\d{7})(?!\d)")
# just the last four, printed right after the label: "SSN 5455", "SSN: ending in 5455"
LAST4_VALUE_RE = re.compile(r"^(?:\(?(?:ending in|last (?:4|four)(?: digits)?)\)?[\s:]*)?"
                            r"(\d{4})(?![\w.,/-])", IC)
# an account/ID value has at least 5 digits, so form numbers ("1099-R", "5498") and years don't qualify
ID_VALUE_RE = re.compile(r"(?<![\w*•#-])(?=(?:[A-Za-z*•#-]*\d){5})[A-Za-z0-9*•#-]{5,}(?![\w-])")
# words printed on forms; a "name" made only of these is a caption, not a person
FORM_WORDS = {"state", "city", "town", "country", "zip", "code", "postal", "province", "total", "amount",
              "date", "yes", "no", "see", "instructions", "part", "line", "box", "form", "page", "schedule",
              "copy", "void", "corrected", "other", "and", "or", "of", "the", "number", "local", "federal",
              # the parties a form's captions talk about ("Employer's state", "Payer's TIN")
              "s", "employer", "employee", "payer", "recipient", "partner", "partnership", "shareholder",
              "beneficiary", "filer", "borrower", "lender", "student", "transferor", "owner", "holder",
              "trustee", "corporation", "estate", "trust", "id", "tin", "ein", "ssn"}
STREET_SUFFIX = (r"st|street|ave|avenue|ln|lane|ct|court|rd|road|way|blvd|boulevard|dr|drive|pl|place|"
                 r"ter|terrace|cir|circle|pkwy|parkway|hwy|highway|trl|trail|sq|square|loop|row")
STREET_RE = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z][A-Za-z.'-]*\s+){1,4}(?:" + STREET_SUFFIX + r")\b\.?"
    r"(?:,?\s+(?:apt|unit|suite|ste|#)\.?\s*[\w-]+)?", IC)
CITY_LINE_RE = re.compile(r"\b[A-Z][A-Za-z.'-]*(?: [A-Za-z.'-]+){0,3},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
PUBLIC_ORG_RE = re.compile(
    r"\b(?:bank|n\.a\.|securities|brokerage|investments?|financial|credit union|trust company|"
    r"insurance|charitable|foundation|department|state of|university|college|hospital|library|"
    r"internal revenue|treasury)\b", IC)
PRIVATE_ORG_RE = re.compile(r"\b(?:family|living|revocable|irrevocable)\s+(?:giving\s+)?(?:trust|fund)\b", IC)
ORG_RE = re.compile(r"\b(?:llc|l\.l\.c\.|lp|l\.p\.|llp|inc|incorporated|corp|corporation|company|co\.|group|"
                    r"partners|partnership|trust|estate|associates|holdings|studio|ltd|fund)\b", IC)


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def is_amount(s: str) -> bool:
    return bool(AMOUNT_RE.match(s.strip()))


# a private organization's name standing alone: "Larkspur Ridge Partners LP" heading a statement
LEGAL_NAME_RE = re.compile(r"(?:[A-Z][\w&.,'-]*\s+|&\s+){1,6}(?:LLC|L\.L\.C\.|LP|L\.P\.|LLP|PLLC|"
                           r"Inc\.?|Ltd\.?|Corp\.?|Partners|Associates|Holdings)")


def is_public_org(s: str) -> bool:
    return bool(PUBLIC_ORG_RE.search(s)) and not PRIVATE_ORG_RE.search(s)


def luhn(ds: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(ds)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def digit_pattern(value: str) -> str | None:
    ds = digits(value)
    if len(ds) < 4:
        return None
    return r"(?<![0-9])" + r"[\s.\-()]{0,2}".join(ds) + r"(?![0-9])"


def date_patterns(value: str) -> list[str]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if not m:
        return [re.escape(value)]
    mo, d, y = int(m[1]), int(m[2]), m[3]
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    return [
        rf"(?<!\d)0?{mo}[/.-]0?{d}[/.-](?:{y}|{y[2:]})(?!\d)",
        rf"(?<!\d){y}-{mo:02d}-{d:02d}(?!\d)",
        rf"\b{months[mo - 1]}[a-z]*\.?\s+0?{d},?\s+{y}\b",
    ]


# ---------------------------------------------------------------- profile

IDENTIFIER_KINDS = ("SSN", "EIN", "TIN", "ACCT", "CARD", "ROUTING", "ID", "PHONE")


class ProfileMatcher:
    def __init__(self, profile: Profile):
        self.rules: list[tuple[re.Pattern, str, str]] = []
        self.by_digits: dict[str, tuple[str, str]] = {}
        self.by_last4: dict[str, tuple[str, str]] = {}
        self.by_name: dict[str, tuple[str, str]] = {}

        def add(pattern: str | None, kind: str, key: str, flags: int = IC) -> None:
            if pattern:
                self.rules.append((re.compile(pattern, flags), kind, key))

        def add_number(value: str, kind: str, key: str) -> None:
            add(digit_pattern(value), kind, key)
            if len(digits(value)) >= 4:
                self.by_digits[digits(value)] = (kind, key)
                self.by_last4.setdefault(digits(value)[-4:], (kind, key))

        for i, p in enumerate(profile.people):
            key = f"person:{i}"
            first, middles, last = p.parts
            f, last_ = re.escape(first), re.escape(last)
            mid = "".join(rf"(?:\s+{re.escape(m[0])}(?:{re.escape(m[1:])})?\.?)?" for m in middles)
            add(rf"\b{f}{mid}\s+{last_}\b", "NAME", key)
            add(rf"\b{last_},\s*{f}{mid}\b", "NAME", key)
            if len(first) > 2:
                add(rf"\b(?:{f}|{re.escape(first.upper())})\b", "NAME", key, 0)
            if len(last) > 2:
                add(rf"\b(?:{last_}|{re.escape(last.upper())})\b", "NAME", f"surname:{last.lower()}", 0)
            self.by_name[" ".join(p.name.lower().split())] = ("NAME", key)
            if p.ssn:
                add_number(p.ssn, "SSN", f"ssn:{i}")
            if p.dob:
                for pat in date_patterns(p.dob):
                    add(pat, "DOB", f"dob:{i}")
        for i, a in enumerate(profile.addresses):
            words = a.street.split()
            parts = [re.escape(w) for w in words[:-1]]
            suffix = words[-1].rstrip(".").lower() if words else ""
            alias = next((g for g in _SUFFIX_GROUPS if suffix in g), [suffix])
            parts.append("(?:" + "|".join(alias) + r")\.?")
            add(r"\b" + r"\s+".join(parts) + r"(?!\w)", "ADDR", f"addr:{i}")
            if a.state and a.zip:
                add(rf"\b{re.escape(a.state)}\s+{re.escape(a.zip)}(?:-\d{{4}})?\b", "ADDR", f"addr:{i}")
        for i, v in enumerate(profile.emails):
            add(re.escape(v), "EMAIL", f"email:{i}")
        for kind, values in [("PHONE", profile.phones), ("EIN", profile.eins),
                             ("ACCT", profile.accounts), ("CARD", profile.cards)]:
            for i, v in enumerate(values):
                add_number(v, kind, f"{kind.lower()}:{i}")

    def find(self, pt: PageText) -> list[Finding]:
        return [Finding(pt.number, m.start(), m.end(), kind, key, "profile", m.group())
                for rx, kind, key in self.rules for m in rx.finditer(pt.text)]

    def canonical(self, kind: str, text: str) -> tuple[str, str]:
        """Map a pattern/label hit onto a profile identity when it is the same value."""
        ds = digits(text)
        if ds in self.by_digits:
            return self.by_digits[ds]
        bare = re.fullmatch(r"\d{4}", text.strip()) and kind in IDENTIFIER_KINDS
        if len(ds) == 4 and ds in self.by_last4 and (re.search(r"[*Xx•#]", text) or bare):
            return self.by_last4[ds]
        name = " ".join(text.lower().split())
        if name in self.by_name:
            return self.by_name[name]
        norm = ds if kind in IDENTIFIER_KINDS else name
        if re.search(r"[*Xx•#]", text):
            norm = "masked:" + norm
        return kind, f"{kind.lower()}:{norm}"


_SUFFIX_GROUPS = [["st", "street"], ["ave", "avenue"], ["ln", "lane"], ["ct", "court"], ["rd", "road"],
                  ["dr", "drive"], ["blvd", "boulevard"], ["pl", "place"], ["ter", "terrace"],
                  ["cir", "circle"], ["pkwy", "parkway"], ["hwy", "highway"], ["trl", "trail"],
                  ["sq", "square"], ["way"]]


# ---------------------------------------------------------------- patterns

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<![\w-])\d{3}[- ]\d{2}[- ]\d{4}(?![\w-])"), "SSN"),
    (re.compile(r"(?<![\w*•])[*Xx•]{3}[- ]?[*Xx•]{2}[- ]?\d{4}(?!\d)"), "SSN"),
    (re.compile(r"(?<![\w*•#])(?:[*Xx•#]{2,}[\s-]?){1,4}\d{2,4}(?!\d)"), "ACCT"),
    (re.compile(r"(?<![\w-])\d{2}-\d{7}(?![\w-])"), "EIN"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "EMAIL"),
    (re.compile(r"(?<![\d/])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?![\d/])"), "PHONE"),
    (re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), "CARD"),
    (STREET_RE, "ADDR"),
    (CITY_LINE_RE, "ADDR"),
]


# a bare number this long is an ID (parcel, tax account, member number) unless it is an
# amount (decimals or thousands commas), a date, or a form's printed OMB control number
LONG_NUMBER_RE = re.compile(r"(?<![\w.,/$-])\d[\d-]{5,}\d(?![\w.,/%-]|\.\d)")
LONG_NUMBER_DIGITS = 7
OMB_RE = re.compile(r"\bOMB\s*No\W{0,3}$", IC)  # tolerant: OCR reads "OMB No." as "OMBNo," too


def pattern_hits(text: str):
    """Yield (match, kind) for every identifier-shaped string in text."""
    for rx, kind in PATTERNS:
        for m in rx.finditer(text):
            value = m.group()
            if kind == "CARD" and not (13 <= len(digits(value)) <= 19 and luhn(digits(value))):
                continue
            if "\n" in value:
                continue
            yield m, kind


def find_patterns(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    out = []
    for m, kind in pattern_hits(pt.text):
        k, key = pm.canonical(kind, m.group())
        out.append(Finding(pt.number, m.start(), m.end(), k, key, "pattern", m.group()))
    # not in PATTERNS: verification would flag every OCR misread of an amount that lost its point
    for m in LONG_NUMBER_RE.finditer(pt.text):
        if len(digits(m.group())) >= LONG_NUMBER_DIGITS and not OMB_RE.search(pt.text[:m.start()][-12:]):
            out.append(Finding(pt.number, m.start(), m.end(), *pm.canonical("ID", m.group()), "pattern",
                               m.group()))
    return out


# ---------------------------------------------------------------- labels

# (label regex, value type, placeholder kind)
LABELS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"routing (?:number|no\.?)|\brtn\b", IC), "id", "ROUTING"),
    (re.compile(r"account (?:number|no\b\.?|#)|\bacct\b\.?(?: ?(?:#|no\b\.?))?|fund (?:no\b\.?|number|id)|"
                r"member (?:#|no\b\.?|number|id)|policy (?:number|no\b\.?)|loan (?:number|no\b\.?)|"
                r"customer (?:number|id)|participant id", IC), "id", "ACCT"),
    (re.compile(r"state (?:id|identification)\b(?: (?:number|no\b\.?))?|payer's state no\b\.?|"
                r"control (?:number|no\b\.?)|"
                r"\b(?:employee|client|patient|student|badge|personnel|staff) (?:id\b|number|no\b\.?|#)",
                IC), "id", "ID"),
    (re.compile(r"social security number|\bssn\b|\bi?tin\b|identification number|identifying number|"
                r"\bein\b|taxpayer id", IC), "tin", "TIN"),
    (re.compile(r"date of birth|\bdob\b|birth ?date", IC), "date", "DOB"),
    (re.compile(r"\b(?:visa|mastercard|amex|american express|discover)\b|card (?:number|no\b\.?|#)|"
                r"\bending in\b", IC), "card", "CARD"),
    (re.compile(r"\bname\b|\baddress\b|^\s*recipient\s*$|account holder|^\s*beneficiar(?:y|ies)\s*$|"
                # a person's role, then a colon: "Provider: Cyrus Pemberton", "Prepared for: ..."
                r"\b(?:provider|payee|patient|student|child(?:ren)?|dependents?|parents?|owners?|taxpayers?|"
                r"spouse|guardian|executor|executrix|administrator|trustee|preparer|prepared (?:for|by)|"
                r"attention|client|insured|borrowers?|co-borrower|signer|contact)(?=\s*:)", IC),
     "name", "NAME"),
]
CARD_TOKEN_RE = re.compile(r"[\d*•#]")
LABEL_CONTINUATION_RE = re.compile(
    r"\b(?:postal|zip|code|telephone|foreign|province|country|instructions|city or town|apt\. no)\b", IC)


def _plain(text: str) -> str:
    """Straighten curly apostrophes (same length, so offsets still line up)."""
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _is_label_line(text: str) -> bool:
    return any(rx.search(_plain(text)) for rx, _, _ in LABELS)


def _value_in(text: str, vtype: str, inline: bool = False) -> tuple[int, int] | None:
    """Character range of a plausible value of this type inside text, or None.
    `inline` is text right after the label on the same line, where a bare last four counts."""
    if vtype == "tin":
        m = TIN_VALUE_RE.search(text)
        if not m and inline and (last4 := LAST4_VALUE_RE.match(text)):
            return last4.span(1)
    elif vtype == "date":
        m = DATE_RE.search(text)
    elif vtype == "card":
        return _card_value(text)
    elif vtype == "id":
        m = next((m for m in ID_VALUE_RE.finditer(text)
                  if not is_amount(m.group()) and not DATE_RE.fullmatch(m.group())), None)
    else:
        s = text.strip()
        if not s or len(s) > 80 or not re.search(r"[A-Za-z]{2}", s) or is_amount(s):
            return None
        if s[0].isdigit() and not (STREET_RE.search(s) or CITY_LINE_RE.search(s)):
            return None  # a box caption like "15 State", not a name or address
        if set(re.findall(r"[a-z]+", s.lower())) <= FORM_WORDS:
            return None
        start = text.index(s)
        return start, start + len(s)
    return (m.start(), m.end()) if m else None


def _card_value(text: str) -> tuple[int, int] | None:
    """The card number after a brand name, however OCR mangled its mask:
    'VISA **##88*94444' -> '**##88*94444'. Stops at an amount or a word."""
    span = None
    for m in re.finditer(r"\S+", text):
        tok = m.group()
        if is_amount(tok) or (span is None and not CARD_TOKEN_RE.search(tok)):
            break
        if not CARD_TOKEN_RE.search(tok) and len(tok) > 2:
            break
        span = (span[0] if span else m.start(), m.end())
    return span


def _name_kind(text: str) -> str:
    if STREET_RE.search(text) or CITY_LINE_RE.search(text) or re.match(r"\s*\d", text):
        return "ADDR"
    return "ORG" if ORG_RE.search(text) else "NAME"


def _lines_below(pt: PageText, label: Line) -> list[Line]:
    """Lines in the same column, directly under the label."""
    r = label.rect
    below = [ln for ln in pt.lines if ln.rect.y0 >= r.y1 - 2 and ln.rect.x0 >= r.x0 - 25
             and ln.rect.x0 < r.x1 and ln.rect.x1 > r.x0]
    return sorted(below, key=lambda ln: (ln.rect.y0, ln.rect.x0))


def _single_value_below(pt: PageText, label: Line, vtype: str) -> tuple[int, int] | None:
    """A one-line value (number, date) anywhere in the box under the label: it may be
    indented or right-aligned, so search a band below, nearest first."""
    r = label.rect
    band = [ln for ln in pt.lines if r.y1 - 2 <= ln.rect.y0 <= r.y1 + 30
            and r.x0 - 25 <= ln.rect.x0 < r.x0 + 300]
    band.sort(key=lambda ln: (ln.rect.y0 - r.y1 > 14, abs(ln.rect.x0 - r.x0)))
    for ln in band:
        text = pt.text[ln.start:ln.end]
        if _is_label_line(text):
            continue
        hit = _value_in(text, vtype)
        if hit:
            return ln.start + hit[0], ln.start + hit[1]
    return None


def _value_right(pt: PageText, label: Line, vtype: str) -> tuple[int, int] | None:
    """A value in its own text object to the right of the label, on the same row
    ("Employee ID:" ... "489135"): the nearest text on the row."""
    r = label.rect
    row = [ln for ln in pt.lines if ln is not label and r.x1 - 2 <= ln.rect.x0 < r.x1 + RIGHT_GAP
           and min(ln.rect.y1, r.y1) - max(ln.rect.y0, r.y0) > 0.5 * min(r.height, ln.rect.height)]
    if not row:
        return None
    ln = min(row, key=lambda ln: ln.rect.x0)
    text = pt.text[ln.start:ln.end]
    hit = _value_in(text, vtype, inline=True)
    if hit and not text[: hit[0]].strip() and not _is_label_line(text):
        return ln.start + hit[0], ln.start + hit[1]
    return None


def find_labels(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    out: list[Finding] = []

    def emit(start: int, end: int, kind: str) -> None:
        text = pt.text[start:end]
        if kind == "NAME":
            kind = _name_kind(text)
        k, key = pm.canonical(kind, text)
        out.append(Finding(pt.number, start, end, k, key, "label", text))

    for line in pt.lines:
        ltext = _plain(pt.text[line.start:line.end])
        if len(ltext.split()) > 16:
            continue  # prose, not a form label
        for rx, vtype, kind in LABELS:
            m = rx.search(ltext)
            if not m:
                continue
            # inline value: "Account Number: 1234", "Fund No. 6346191", "Name: ..."
            rest = ltext[m.end():]
            sep = re.match(r"[\s:#.]*", rest)
            body = rest[sep.end():]
            if body.strip() and (vtype != "name" or ":" in sep.group()):
                hit = _value_in(body, vtype, inline=True)
                if hit and (not body[: hit[0]].strip() or (vtype == "tin" and LAST4_VALUE_RE.match(body))):
                    offset = line.start + m.end() + sep.end()
                    emit(offset + hit[0], offset + hit[1], kind)
                    continue
            if not body.strip() and vtype in ("id", "tin", "card", "date"):
                hit = _value_right(pt, line, vtype)
                if hit:
                    emit(*hit, kind)
                    continue
            # value in the box below the label
            if vtype in ("id", "tin", "card"):
                hit = _single_value_below(pt, line, vtype)
                if hit:
                    emit(*hit, kind)
                continue
            limit = 6
            prev_bottom, taken = line.rect.y1, 0
            for ln in _lines_below(pt, line):
                gap = ln.rect.y0 - prev_bottom
                if gap > (30 if taken == 0 else 16):
                    break
                text = pt.text[ln.start:ln.end]
                if vtype == "name" and LABEL_CONTINUATION_RE.search(text):
                    if taken:
                        break  # the next box's label
                    prev_bottom = ln.rect.y1  # second line of this label
                    continue
                if _is_label_line(text):
                    break
                if vtype == "name" and is_public_org(text):
                    break  # a bank's or charity's own block: keep it
                hit = _value_in(text, vtype)
                if not hit:
                    break
                emit(ln.start + hit[0], ln.start + hit[1], kind)
                prev_bottom, taken = ln.rect.y1, taken + 1
                if taken >= limit or CITY_LINE_RE.search(text):
                    break  # an address block ends with its city, state and ZIP
    return out


# ---------------------------------------------------------------- names in prose

def _private_org(text: str) -> bool:
    return bool(LEGAL_NAME_RE.fullmatch(text)) and not is_public_org(text)


def find_names(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    out = []
    for start, end, label in names.entities(pt):
        text = pt.text[start:end]
        lead = len(text) - len(text.lstrip(" ,.;:"))
        start, end = start + lead, start + len(text.rstrip(" ,.;:"))
        text = pt.text[start:end]
        if label == "ORG":
            text = re.sub(r"^the\s+", "", text, flags=IC)
            start = end - len(text)
            if _private_org(text):
                out.append(Finding(pt.number, start, end, *pm.canonical("ORG", text), "ner", text))
            continue
        words = re.findall(r"[a-z]+", text.lower())
        if (len(text) < 3 or re.search(r"\d", text) or not re.search(r"[A-Z]", text)
                or set(words) <= FORM_WORDS or ORG_RE.search(text) or STREET_RE.search(text)):
            continue
        out.append(Finding(pt.number, start, end, *pm.canonical("NAME", text), "ner", text))
    return out


def find_orgs(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    """A line that is nothing but a private organization's legal name (a statement's
    heading). Not a table cell: a holdings table full of "... Inc" stays readable."""
    out = []
    for line in pt.lines:
        raw = pt.text[line.start:line.end]
        text = raw.strip()
        if not _private_org(text):
            continue
        r = line.rect
        row = [o for o in pt.lines if o is not line and min(o.rect.y1, r.y1) - max(o.rect.y0, r.y0)
               > 0.5 * r.height]
        if len(row) >= 2:
            continue
        start = line.start + raw.index(text)
        out.append(Finding(pt.number, start, start + len(text), *pm.canonical("ORG", text), "label", text))
    return out


# ---------------------------------------------------------------- repetition

def repeat_values(findings: list[Finding]) -> dict[str, Finding]:
    """Values worth looking for elsewhere in the document, each with the finding it
    came from. A third party's full name also gives its parts, so "Mr. Varga" on a
    later page gets the same placeholder as "Desmond Varga"."""
    values: dict[str, Finding] = {}
    for f in findings:
        text = f.text.strip()
        if f.source == "profile" or len(text) < REPEAT_MIN or is_amount(text) or "\n" in text:
            continue  # profile values are matched everywhere already
        values.setdefault(text, f)
    for f in findings:
        parts = f.text.split()
        if not _person(f):
            continue
        for part in parts:
            if len(part) >= 3 and part[0].isupper() and part.isalpha() and part.lower() not in FORM_WORDS:
                single = values.get(part)
                if single is None or len(single.text.split()) == 1:
                    values[part] = f
    return values


def _person(f: Finding) -> bool:
    """A full name, from the model or from a label the model agrees is a person."""
    if f.kind != "NAME" or not 2 <= len(f.text.split()) <= 4:
        return False
    return f.source == "ner" or (f.source == "label" and names.is_person(f.text))


def repeated(pt: PageText, found: list[Finding], values: dict[str, Finding]) -> list[Finding]:
    """Occurrences of `values` on this page that nothing in `found` covers yet."""
    taken = [(f.start, f.end) for f in found]
    out = []
    for text, f in values.items():
        for m in re.finditer(r"(?<!\w)" + re.escape(text) + r"(?!\w)", pt.text):
            if not any(m.start() < e and s < m.end() for s, e in taken):
                out.append(Finding(pt.number, m.start(), m.end(), f.kind, f.key, "repeat", text))
                taken.append((m.start(), m.end()))
    return out


def relink(findings: list[Finding], values: dict[str, Finding]) -> None:
    """A surname the model tagged on its own takes the full name's placeholder."""
    for f in findings:
        source = values.get(f.text.strip())
        if f.source == "ner" and source is not None and source is not f and source.source in ("ner", "label"):
            f.kind, f.key = source.kind, source.key


# ---------------------------------------------------------------- merge

def merge(findings: list[Finding], pt: PageText) -> list[Finding]:
    kept = [f for f in findings if f.source == "profile" or not is_amount(f.text)]
    kept = [f for f in kept if pt.text[f.start:f.end].strip()]
    kept.sort(key=lambda f: (f.start, -PRIORITY[f.source], -(f.end - f.start)))
    merged: list[Finding] = []
    for f in kept:
        if merged and f.start < merged[-1].end:
            last = merged[-1]
            if PRIORITY[f.source] > PRIORITY[last.source]:
                last.kind, last.key, last.source = f.kind, f.key, f.source
            last.end = max(last.end, f.end)
            last.text = pt.text[last.start:last.end]
            continue
        merged.append(Finding(f.page, f.start, f.end, f.kind, f.key, f.source, f.text))
    return merged


def detect(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    """Findings on one page; `repeated` adds what the rest of the document found."""
    found = pm.find(pt) + find_patterns(pt, pm) + find_labels(pt, pm) + find_orgs(pt, pm)
    return merge(found + find_names(pt, pm), pt)
