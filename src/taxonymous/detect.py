"""Find identifiers on a page. Document-agnostic: the same rules run on every page of
every document. Two kinds of finding, highest priority first:
  profile  - the user's own known values (Your details), matched with formatting tolerance,
             including masked and last-four forms
  pattern  - anything shaped like an SSN, whoever's it is

Then, across the whole document, an SSN-shaped value found on one page is found again
wherever else it appears ("repeat"), however it is spaced there.
"""

import re
from dataclasses import dataclass

from .pagetext import PageText
from .profile import Address, Profile

IC = re.IGNORECASE
PRIORITY = {"profile": 2, "pattern": 1, "repeat": 0}
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
MONTH_YEAR_RE = re.compile(r"(?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}")  # "Rev. 12-2026", "exp 04/2027"
# words printed on forms; a "name" made only of these is a caption, not a person
FORM_WORDS = {"state", "city", "town", "country", "zip", "code", "postal", "province", "total", "amount",
              "date", "yes", "no", "see", "instructions", "part", "line", "box", "form", "page", "schedule",
              "copy", "void", "corrected", "other", "and", "or", "of", "the", "number", "local", "federal",
              # the parties a form's captions talk about ("Employer's state", "Payer's TIN")
              "s", "employer", "employee", "payer", "recipient", "partner", "partnership", "shareholder",
              "beneficiary", "filer", "borrower", "lender", "student", "transferor", "owner", "holder",
              "trustee", "corporation", "estate", "trust", "id", "tin", "ein", "ssn",
              # what the boxes hold ("Federal income tax withheld", "Medicare wages and tips")
              "income", "tax", "taxes", "withheld", "wages", "tips", "compensation", "social", "security",
              "medicare", "allocated", "dependent", "benefits", "deferred", "nonqualified", "retirement",
              "statement", "control", "identification", "information", "treasury", "department",
              "internal", "revenue", "service", "irs", "omb",
              # filing status, which the name model reads as a person ("Married Filing Jointly")
              "married", "filing", "jointly", "separately", "qualifying", "surviving", "spouse", "widow",
              "widower",
              # 1099 captions ("Total capital gain distributions", "Unrecaptured Sec. 1250 gain"),
              # leaving out the ones that are also surnames (Cash, Long, Short, Bond, Gross...)
              "reference", "ordinary", "qualified", "dividend", "dividends", "distribution", "distributions",
              "capital", "gain", "gains", "loss", "losses", "unrecaptured", "section", "sec", "collectibles",
              "nondividend", "exempt", "interest", "foreign", "investment", "expenses", "liquidation",
              "noncash", "specified", "activity", "proceeds", "basis", "adjusted", "disallowed", "accrued",
              "term", "covered", "noncovered", "reported", "net", "summary", "detail", "original", "issue",
              "obligations", "savings", "taxable", "nontaxable", "royalties", "rents", "payments",
              "substitute", "nonemployee", "contributions", "unrealized", "realized", "aggregate",
              "securities", "description", "acquired", "sold", "quantity", "shares", "withholding",
              "premium", "discount", "penalty", "account", "year", "end", "supplemental",
              "a", "b", "c", "d", "e", "f", "g", "h",  # box letters: "Section 199A", "2a", "12b"
              "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}  # "Part II" of a K-1
def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def is_amount(s: str) -> bool:
    return bool(AMOUNT_RE.match(s.strip()))


NUMBER_SEP = r"[\s.\-()/,]{0,2}"  # what may sit between the digits of the same number
# an amount ("61234.50", "1,234.56") is never a copy of a number, whatever its digits:
# "6123450" (an OCR read that lost the point) must not black out the wages
NOT_AMOUNT = r"(?![0-9][0-9,]*\.[0-9]{2}(?!\.?[0-9]))"


def number_regex(ds: str) -> str:
    """The digits `ds` however they are spaced or punctuated, as their own number (not
    inside a longer one, and not an amount). Verification uses the same rule, so whatever
    it would call a copy of a value, redaction finds too."""
    return r"(?<![0-9])" + NOT_AMOUNT + NUMBER_SEP.join(ds) + r"(?![0-9])"


def is_number_value(text: str) -> bool:
    """Mostly digits and long enough to be matched by its digits alone."""
    ds = digits(text)
    return len(ds) >= 7 and len(ds) >= len(re.sub(r"[^A-Za-z]", "", text))


def digit_pattern(value: str) -> str | None:
    ds = digits(value)
    if len(ds) < 4:
        return None
    return r"(?<![0-9])\(?" + r"[\s.\-()]{0,2}".join(ds) + r"(?![0-9])"  # "(" of "(555) 555-0188" too


def date_patterns(value: str) -> list[str]:
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if not m:
        return [re.escape(value)]
    mo, d, y = int(m[1]), int(m[2]), m[3]
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    return [
        rf"(?<!\d)0?{mo} ?[/.-] ?0?{d} ?[/.-] ?(?:{y}|{y[2:]})(?!\d)",
        rf"(?<!\d){y}-{mo:02d}-{d:02d}(?!\d)",
        # a form's boxes, one per digit or part ("03 07 1981"), and e-file data ("03071981")
        rf"(?<!\d){mo:02d}\s+{d:02d}\s+{y}(?!\d)",
        rf"(?<!\d)(?:{mo:02d}{d:02d}{y}|{y}{mo:02d}{d:02d})(?!\d)",
        rf"\b{months[mo - 1]}[a-z]*\.?\s+0?{d},?\s+{y}\b",
    ]


# ---------------------------------------------------------------- profile

IDENTIFIER_KINDS = ("SSN", "EIN", "TIN", "ACCT", "CARD", "ROUTING", "ID", "PHONE")


UNIT_WORDS = r"apartment|apt|unit|suite|ste|floor|fl|room|rm|building|bldg|lot|space|spc"
UNIT_RE = re.compile(rf"(?:{UNIT_WORDS})\.?", IC)
SHORT = 7  # digits: a shorter number on its own also turns up by chance in a file's drawing code
YEAR_RE = re.compile(r"(?:19|20)\d\d")


class ProfileMatcher:
    def __init__(self, profile: Profile):
        # (pattern, kind, key, whether to look for it in a file's raw content too)
        self.rules: list[tuple[re.Pattern, str, str, bool]] = []
        self.by_digits: dict[str, tuple[str, str]] = {}
        self.by_last4: dict[str, tuple[str, str]] = {}
        self.by_name: dict[str, tuple[str, str]] = {}
        self.surname_of: dict[str, str] = {}  # a person's key -> the key their surname alone has

        def add(pattern: str | None, kind: str, key: str, flags: int = IC, raw: bool = True) -> None:
            if pattern:
                self.rules.append((re.compile(pattern, flags), kind, key, raw))

        def add_number(value: str, kind: str, key: str) -> None:
            short = len(digits(value)) < SHORT
            # a short one ("12345", a PIN) isn't an amount's digits ("123.45")
            add(digit_pattern(value) and (NOT_AMOUNT if short else "") + digit_pattern(value), kind, key,
                raw=not short)
            if len(digits(value)) >= 4:
                self.by_digits[digits(value)] = (kind, key)
                self.by_last4.setdefault(digits(value)[-4:], (kind, key))
            if len(digits(value)) < 4 or kind == "PHONE":
                return
            last4 = digits(value)[-4:]
            # the masked forms statements print: "XXX-XX-4323", "****4323", "ending in 4323"
            add(rf"(?<![\w*•#])(?:[*Xx•#]{{2,}}[\s-]?){{1,4}}{last4}(?!\d)", kind, key)
            add(rf"\b(?:ending in|ends in|last (?:4|four)(?: digits)?(?: of)?)\W{{0,3}}(?:[*Xx•#]+[\s-]?)?"
                rf"(?P<v>{last4})(?!\d)", kind, key)
            if kind == "SSN":  # "SSN 4323", "TIN: 4323"
                add(rf"\b(?:ssn|social security(?: number| no\.?)?|i?tin)\b\W{{0,4}}(?P<v>{last4})(?!\d)",
                    kind, key)
            if kind in ("ACCT", "CARD") and not YEAR_RE.fullmatch(last4):
                # an account's last four on their own, as statements print them ("Acct 4323",
                # "...4323", "x4323"); not in an amount (4323.00), a date or a longer number
                end = r"(?![\w/-]|[.,]\d)"
                add(rf"(?<![\w.,$/-]){last4}{end}", kind, key, raw=False)
                add(rf"(?:(?<!\w)[x#*•]|\.{{2,}}|…)\s?(?P<v>{last4}){end}", kind, key, raw=False)

        def add_address(a: Address, key: str) -> None:
            words = a.street.split()
            # the unit ("Apt 1007", "Apartment APT 1007", "#4B") is printed on a line of its own
            # as often as after the street: each is found on its own
            # with a street before it ("100 Unit Road" has none)
            at = next((i for i, w in enumerate(words)
                       if i > 1 and (UNIT_RE.fullmatch(w) or w.startswith("#"))), None)
            if at is not None and not UNIT_RE.fullmatch(words[-1]):
                words, unit = words[:at], words[at:][-1].lstrip("#")
                if unit:
                    add(rf"(?<!\w)(?:(?:{UNIT_WORDS})\.?\s*#?|#)\s*{re.escape(unit)}(?!\w)", "ADDR", key)
            if words:
                parts = [re.escape(w) for w in words[:-1]]
                suffix = words[-1].rstrip(".").lower()
                alias = next((g for g in _SUFFIX_GROUPS if suffix in g), [re.escape(suffix)])
                parts.append("(?:" + "|".join(alias) + r")\.?")
                add(r"\b" + r"\s+".join(parts) + r"(?!\w)", "ADDR", key)
            if a.state and a.zip:
                city = r"\s+".join(map(re.escape, a.city.split()))
                town = rf"(?:{city},?\s+)?" if city else ""
                add(rf"\b{town}{re.escape(a.state)}\s+{re.escape(a.zip)}(?:-\d{{4}})?\b", "ADDR", key)

        def add_phone(v: str, key: str) -> None:
            ds = digits(v)
            if len(ds) == 11 and ds[0] == "1":  # +1 / 1- country code: documents mostly leave it out
                ds = ds[1:]
            add_number(ds, "PHONE", key)

        def name_rules(name: str) -> tuple[str, str, str, str]:
            """A full name's parts as patterns: first, middles (initials allowed), last."""
            words = name.split()
            first, middles, last = words[0], words[1:-1], words[-1]
            # up to two middle names or initials, any (a document can have the initial wrong, OCR
            # can read its dot as a comma), with or without a space after the dot: "Avery F.
            # Abernathy", "Avery F.Abernathy", "Abernathy, Avery F.", "ABERNATHY AVERY F"
            initial = "|".join([*(re.escape(m) for m in middles if len(m) > 1), "[a-z]"])
            mid = rf"(?:\s+(?:{initial})\b(?:\.|,(?=\s))?){{0,2}}"
            return re.escape(first), initial, mid, re.escape(last)

        for i, p in enumerate(profile.people):
            key = f"person:{i}"
            self.surname_of[key] = f"surname:{p.parts[2].lower()}"
            first, _, last = p.parts
            f, initial, mid, last_ = name_rules(p.name)
            add(rf"\b{f}{mid}\s*(?<=[\s.,]){last_}\b", "NAME", key)
            add(rf"\b{last_}(?:,\s*|\s+){f}{mid}", "NAME", key)
            # a joint name gives the surname once: "Avery F. & Jordan K. Abernathy"; the first
            # name and an initial with a dot, or before "&" / "and", are this person
            add(rf"\b(?:{f}|{re.escape(first.upper())})(?:\s+[A-Z]\.){{1,2}}", "NAME", key, 0)
            add(rf"\b{f}(?:\s+(?:{initial})\b\.?){{1,2}}(?=\s*(?:&|\band\b))", "NAME", key)
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
            add_address(a, f"addr:{i}")
        for i, v in enumerate(profile.emails):
            add(re.escape(v), "EMAIL", f"email:{i}")
        for i, v in enumerate(profile.phones):
            add_phone(v, f"phone:{i}")
        for kind, values in [("EIN", profile.eins), ("ACCT", profile.accounts), ("CARD", profile.cards),
                             ("ROUTING", profile.routing)]:
            for i, v in enumerate(values):
                add_number(v, kind, f"{kind.lower()}:{i}")
        for i, v in enumerate(profile.numbers):
            if re.search(r"[A-Za-z]", v):  # a client ID like "AB-10442": its letters and digits, any spacing
                chars = re.sub(r"[^A-Za-z0-9]", "", v)
                add(r"(?<![A-Za-z0-9])" + r"[\s.\-]?".join(map(re.escape, chars)) + r"(?![A-Za-z0-9])",
                    "ID", f"id:{i}", raw=len(chars) >= SHORT)
            else:
                add_number(v, "ID", f"id:{i}")
        for i, pr in enumerate(profile.preparers):
            key = f"preparer:{i}"
            if len(pr.name.split()) > 1:  # the whole name only: a first name alone is too common
                f, _, mid, last_ = name_rules(pr.name)
                add(rf"\b{f}{mid}\s*(?<=[\s.,]){last_}\b", "NAME", f"{key}:name")
                add(rf"\b{last_}(?:,\s*|\s+){f}{mid}", "NAME", f"{key}:name")
            for v in (pr.firm, pr.name if len(pr.name.split()) == 1 else ""):
                words = re.findall(r"[A-Za-z0-9&']+", v)
                if words:  # "Doe & Co., CPAs" as "DOE & CO CPAS" too
                    phrase = r"[^A-Za-z0-9&]*".join(map(re.escape, words))
                    add(rf"(?<![A-Za-z0-9]){phrase}(?![A-Za-z0-9])", "ORG", f"{key}:firm")
            add_address(Address(pr.street, pr.city, pr.state, pr.zip), f"{key}:addr")
            if pr.phone:
                add_phone(pr.phone, f"{key}:phone")
            if pr.ein:
                add_number(pr.ein, "EIN", f"{key}:ein")
            if pr.ptin:  # "P01234567", "P 0123 4567"
                add(r"(?<![A-Za-z0-9])(?:P[\s-]?)?" + r"[\s-]?".join(digits(pr.ptin)) + r"(?!\d)",
                    "ID", f"{key}:ptin")

    def related(self, keys) -> set[str]:
        """The keys `keys` stand for: a person's, with the key of their surname on its own."""
        return {k for key in keys for k in (key, self.surname_of.get(key)) if k}

    def find(self, pt: PageText, raw: bool = False) -> list[Finding]:
        """`raw`: the text is a file's raw content, where a short number on its own is
        likely an object number or a coordinate; only its forms with context count there."""
        out = []
        for rx, kind, key, in_raw in self.rules:
            if raw and not in_raw:
                continue
            for m in rx.finditer(pt.text):
                start, end = m.span("v") if "v" in rx.groupindex else m.span()  # "ending in <v>"
                out.append(Finding(pt.number, start, end, kind, key, "profile", pt.text[start:end]))
        return out

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

SSN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<![\w-])\d{3}[- ]\d{2}[- ]\d{4}(?![\w-])"), "SSN"),
    (re.compile(r"(?<![\w*•])[*Xx•]{3}[- ]?[*Xx•]{2}[- ]?\d{4}(?!\d)"), "SSN"),
]
OMB_RE = re.compile(r"\bOMB\s*No\W{0,3}$", IC)  # tolerant: OCR reads "OMB No." as "OMBNo," too


def is_caption(text: str) -> bool:
    """Only words printed on forms: a caption, never anyone's name."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return bool(words) and words <= FORM_WORDS


IRS_OMB_RE = re.compile(r"1545-\d{4}")  # every IRS form's control number; printed far from "OMB No." too


def is_date(text: str) -> bool:
    return bool(DATE_RE.fullmatch(text.strip()) or MONTH_YEAR_RE.fullmatch(text.strip()))


def identifies(kind: str, value: str) -> bool:
    """Whether a value some detector redacted can identify anyone. Not a caption ("Part II",
    "Capital gain distributions"), an IRS form's OMB number, or a date other than a birth
    date: a detector mistake, which must not fail every document that prints the same. Nor
    a bare short number ("4323", an SSN's last four after "SSN"): on its own it turns up by
    chance in amounts, font tables and page drawing code; the profile's rules still find it
    where it has its context ("SSN 4323", "XXX-XX-4323")."""
    if is_caption(value) or IRS_OMB_RE.fullmatch(value.strip()):
        return False
    if kind != "DOB" and re.fullmatch(r"[\d\s./-]+", value.strip()) and len(digits(value)) < 7:
        return False
    return kind == "DOB" or not is_date(value)


PROSE_KINDS = {"NAME", "ORG"}
# content stream operators that sit between the strings of a sentence: [(filing)-250(your)]TJ
PDF_OPERATORS = {"tj", "td", "tf", "tm", "tz", "tc", "tw", "tl", "ts", "tr", "bt", "et", "re", "cm", "q", "w",
                 "f", "s", "b", "n", "do", "bdc", "emc", "g", "rg", "k", "l", "m", "c", "h"}


def in_prose(text: str, start: int, end: int) -> bool:
    """A match all in lowercase with a lowercase word right before or after it: a word in a
    sentence ("the due date for filing your return"), not a name, which is capitalized.
    Read in a PDF's raw content too, where operators sit between a sentence's pieces."""
    if re.search(r"[A-Z]", text[start:end]):
        return False

    def word(chunk: str, last: bool) -> str | None:
        words = [w for w in re.findall(r"[A-Za-z]+", chunk) if w.lower() not in PDF_OPERATORS or w.isupper()]
        return (words[-1] if last else words[0]) if words else None
    before, after = word(text[max(0, start - 40):start], True), word(text[end:end + 40], False)
    return any(w is not None and w.islower() and len(w) > 1 for w in (before, after))


def incidental(kind: str, text: str, m: re.Match) -> bool:
    """A copy of a known number's digits that is something else here: a form's OMB number, or
    a date (whatever form the number was stored in: "12312025" matches "12/31/2025")."""
    return after_omb(text, m.start()) or (kind != "DOB" and is_date(m.group()))


def after_omb(text: str, start: int) -> bool:
    """The number at `start` is the form's printed OMB control number."""
    return bool(OMB_RE.search(text[:start][-12:]))


def pattern_hits(text: str):
    """Yield (match, kind) for every SSN-shaped string in text."""
    for rx, kind in SSN_PATTERNS:
        for m in rx.finditer(text):
            if "\n" not in m.group():
                yield m, kind


def repeat_values(findings: list[Finding]) -> dict[str, Finding]:
    """Values worth looking for elsewhere in the document, each with the finding it
    came from: an SSN shape found on one page, printed differently on another."""
    values: dict[str, Finding] = {}
    for f in findings:
        text = f.text.strip()
        if f.source == "profile" or len(text) < REPEAT_MIN or is_amount(text) or "\n" in text:
            continue  # profile values are matched everywhere already
        values.setdefault(text, f)
    return values


def repeated(pt: PageText, found: list[Finding], values: dict[str, Finding]) -> list[Finding]:
    """Occurrences of `values` on this page that nothing in `found` covers yet. A number is
    found by its digits, however it is spaced or punctuated here."""
    taken = [(f.start, f.end) for f in found]
    out = []
    for text, f in values.items():
        if is_number_value(text):
            pattern = number_regex(digits(text))
        else:
            pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, text.split())) + r"(?!\w)"
        for m in re.finditer(pattern, pt.text):
            if not any(m.start() < e and s < m.end() for s, e in taken):
                out.append(Finding(pt.number, m.start(), m.end(), f.kind, f.key, "repeat", text))
                taken.append((m.start(), m.end()))
    return out


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


CHECK_DIGITS_RE = re.compile(r"[ /](\d{1,3})(?=\s|$)")  # "185-2732 5", NJ's "<EIN>/000"
NUMBER_KINDS = {"SSN", "EIN", "TIN", "ACCT", "CARD", "ID"}


def _box_gap(pt: PageText, i: int, j: int) -> float | None:
    """Horizontal gap between the boxes of text[i] and text[j], in units of their height;
    None when they aren't side by side on one line."""
    a, b = pt.boxes[i], pt.boxes[j]
    if a is None or b is None or min(a.y1, b.y1) - max(a.y0, b.y0) < 0.5 * min(a.height, b.height):
        return None
    return (b.x0 - a.x1) / max(a.height, b.height, 1e-6)


def continue_numbers(findings: list[Finding], pt: PageText) -> None:
    """An identifier printed with a short trailing group after a word space or a slash
    ("185-2732 5", a state employer ID's check digit; "00-1234567/000") covers that group
    too, when the group ends the cell: the line ends, or a column gap follows."""
    for f in findings:
        if f.kind not in NUMBER_KINDS or f.end < 1:
            continue
        m = CHECK_DIGITS_RE.match(pt.text, f.end)
        if not m:
            continue
        gap = _box_gap(pt, f.end - 1, m.start(1))
        if gap is None or gap > 0.6:  # wider than a word space: the next column
            continue
        after = m.end()
        while after < len(pt.text) and pt.text[after] == " ":
            after += 1
        if after < len(pt.text) and pt.text[after] != "\n":
            next_gap = _box_gap(pt, m.end() - 1, after)
            if next_gap is not None and next_gap < 1.0:  # more of the same cell follows
                continue
        f.end = m.end()
        f.text = pt.text[f.start:f.end]


def detect(pt: PageText, pm: ProfileMatcher) -> list[Finding]:
    """Findings on one page: your details, and anything shaped like an SSN; `repeated`
    adds what the rest of the document found."""
    ssns = [Finding(pt.number, m.start(), m.end(), *pm.canonical(kind, m.group()), "pattern", m.group())
            for m, kind in pattern_hits(pt.text)]
    return merge(pm.find(pt) + ssns, pt)
