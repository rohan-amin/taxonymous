"""What a value the reviewer clicked probably is, so adding it to My profile is one step.

Rules only, no models: the value's shape (an email, a phone number, an EIN, a card number
that passes the Luhn check, a street address, "City, ST 12345", a name) and the words
printed just before it or above it ("Account number", "Recipient's name", "Payer's TIN").
It is only a suggestion: the reviewer sees the kind and the fields filled in, and can
change either before anything is added.
"""

import re
from collections.abc import Sequence

from .profile import DOB_RE, EMAIL_RE
from .words import PageWord

STREET = re.compile(
    r"^\d+[A-Za-z]?\s+(?:[NSEW]\.?\s+)?[\w.' -]*?\b(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|"
    r"boulevard|way|ct|court|pl|place|pkwy|parkway|cir|circle|ter|terrace|hwy|highway|loop|trl|trail|sq)\b",
    re.I)
STREET_LINE = re.compile(STREET.pattern + r"\.?(?:,?\s+(?:apt|unit|suite|ste|#)\.?\s*#?\w+)?$", re.I)
CITY_LINE = re.compile(r"^(?P<city>[A-Za-z][A-Za-z .'-]*?),?\s+(?P<state>[A-Z]{2})\s+"
                       r"(?P<zip>\d{5}(?:-\d{4})?)$")
PHONE = re.compile(r"^(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}$")  # punctuated like one
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
             "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP".split())
MONEY = re.compile(r"^\(?-?\$?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?$")
EIN = re.compile(r"^\d{2}-\d{7}$")
SSN = re.compile(r"^\d{3}-\d{2}-\d{4}$")
MASKED_SSN = re.compile(r"^[X*•#]{3}[-_ ]?[X*•#]{2}[-_ ]?\d{4}$", re.I)
PTIN = re.compile(r"^P\d{8}$", re.I)
NAME = re.compile(r"^[A-Za-z][A-Za-z.'-]*(?:,?\s+[A-Za-z][A-Za-z.'-]*){1,4}$")

# words in a label, by what they say the value is
LABELS: list[tuple[str, re.Pattern]] = [
    ("routing", re.compile(r"\brouting\b|\baba\b|\brtn\b", re.I)),
    ("card", re.compile(r"\bcard\b|\bvisa\b|mastercard|\bamex\b|american express|discover", re.I)),
    ("account", re.compile(r"\baccount\b|\bacct\b|\ba/c\b|\bpolicy\b|\bcontract\b|\bloan\b|"
                           r"\bplan (?:number|no|id)\b|participant", re.I)),
    ("ein", re.compile(r"\bein\b|employer identification|\bpayer['’]?s? (?:federal )?(?:tin|id)|federal id|"
                       r"\bfein\b", re.I)),
    ("ssn", re.compile(r"\bssn\b|social security|\bitin\b|recipient['’]?s? tin|\btaxpayer id", re.I)),
    ("phone", re.compile(r"\bphone\b|\btel\b|telephone|\bfax\b|\bmobile\b|\bcell\b", re.I)),
    ("dob", re.compile(r"\bbirth\b|\bdob\b|\bborn\b", re.I)),
    ("ptin", re.compile(r"\bptin\b", re.I)),
    ("preparer", re.compile(r"preparer|\bfirm\b|\bcpa\b", re.I)),
    ("address", re.compile(r"\baddress\b|\bstreet\b|\bcity\b|\bzip\b", re.I)),
    ("id", re.compile(r"\bid\b|\bpin\b|client|customer|member|reference|confirmation|\bno\.|number", re.I)),
    ("name", re.compile(r"\bname\b|recipient|employee|taxpayer|spouse|beneficiary|owner|shareholder|"
                        r"partner|payee|borrower|student|depositor|account holder|donor|insured", re.I)),
]


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def luhn(ds: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(ds)):
        d = int(c) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def label_for(words: Sequence[PageWord], chosen: Sequence[int]) -> str:
    """What the chosen words are labelled: of the words printed just before them on their
    line, and the three lines above that line up with them (forms print a box's label over
    it, sometimes wrapped), the nearest that says what a value is; "" if none does. The
    nearest only, so a paragraph's other sentences don't speak for it."""
    if not chosen:
        return ""
    first = words[min(chosen)]
    x0 = min(words[i].rect[0] for i in chosen)
    x1 = max(words[i].rect[2] for i in chosen)
    top = min(words[i].rect[1] for i in chosen)
    height = max(1.0, first.rect[3] - first.rect[1])
    before = [w for i, w in enumerate(words[:min(chosen)]) if w.line == first.line and i not in chosen][-6:]
    above = [w for w in words if top - 4 * height <= w.rect[3] <= top + 0.2 * height
             and w.rect[2] > x0 - 6 * height and w.rect[0] < x1 + height and w.line != first.line]
    lines: list[list[PageWord]] = []  # nearest first
    for w in sorted(above, key=lambda w: -w.rect[3]):
        if lines and abs(lines[-1][0].rect[3] - w.rect[3]) < 0.5 * height:
            lines[-1].append(w)
        elif len(lines) < 3:
            lines.append([w])
    segments = [" ".join(w.text for w in before)]
    segments += [" ".join(w.text for w in sorted(line, key=lambda w: w.rect[0])) for line in lines]
    if len(segments) > 2:  # a label wrapped onto the line above the value: read the two as one
        segments.insert(2, f"{segments[2]} {segments[1]}")
    return next((s for s in segments if any(p.search(s) for _, p in LABELS)), "")


def grow(words: Sequence[PageWord], chosen: Sequence[int]) -> list[int]:
    """One word clicked, the whole value, from the words beside it on its row: a phone
    number's area code ("555-0128" -> "(555) 555-0128"), a street or "City, ST ZIP" line,
    or the rest of a name ("Avery" -> "Avery F Abernathy", across a form's boxes too)."""
    chosen = sorted(set(chosen))
    if len(chosen) != 1:
        return chosen
    i = chosen[0]
    w = words[i]
    h = max(1.0, w.rect[3] - w.rect[1])
    mid = (w.rect[1] + w.rect[3]) / 2
    row = sorted((j for j, v in enumerate(words) if abs((v.rect[1] + v.rect[3]) / 2 - mid) < 0.35 * h),
                 key=lambda j: words[j].rect[0])
    at = row.index(i)

    def gap(a: int, b: int) -> float:  # between row[a] and row[b], a left of b
        return words[row[b]].rect[0] - words[row[a]].rect[2]

    def span(a: int, b: int) -> str:
        return " ".join(words[row[k]].text for k in range(a, b + 1))

    # a run of words set close together, as one line of an address is
    lo, hi = at, at
    while lo > 0 and gap(lo - 1, lo) < 1.2 * h:
        lo -= 1
    while hi < len(row) - 1 and gap(hi, hi + 1) < 1.2 * h:
        hi += 1
    for a in range(lo, at + 1):  # from the first start that makes a whole value, its longest
        ends = [b for b in range(at, min(hi, a + 8) + 1)
                if (a < b and (PHONE.fullmatch(t := _tidy(span(a, b))) or CITY_LINE.fullmatch(t)))
                or STREET_LINE.fullmatch(_tidy(span(a, b)))]
        if ends:
            return [row[k] for k in range(a, ends[-1] + 1)]
    if not _name_part(w.text) or _says(w.text):
        return chosen

    def joins(k: int, toward: int) -> bool:  # row[k] continues the name from row[toward]
        v = words[row[k]].text
        near = gap(min(k, toward), max(k, toward)) < 12 * h
        return near and _name_part(v) and not v.endswith(":") and not _says(v)

    lo = hi = at
    while lo > 0 and at - lo < 3 and joins(lo - 1, lo):
        lo -= 1
    while hi < len(row) - 1 and hi - lo < 3 and joins(hi + 1, hi) and not words[row[hi]].text.endswith(":"):
        hi += 1
    return [row[k] for k in range(lo, hi + 1)]


def _says(text: str) -> bool:
    return any(p.search(text) for _, p in LABELS)


def _name_part(token: str) -> bool:
    """A word that can be part of a name: capitalised letters ("Avery", "ABERNATHY,", "M.")."""
    return bool(re.fullmatch(r"[A-Z][A-Za-z'’-]*\.?,?", token))


def _person_name(text: str) -> str:
    """'ABERNATHY, AVERY M' -> 'Avery M Abernathy': the way the form is filled in."""
    if "," in text:
        last, _, rest = text.partition(",")
        text = f"{rest.strip()} {last.strip()}"
    text = " ".join(text.split())
    return text.title() if text.isupper() else text


AMOUNT = ("This looks like an amount. Amounts stay readable so your AI can work with them: "
          "add it only if it identifies you.")
COMPANY = re.compile(r"\b(?:bank|inc|llc|llp|lp|corp|corporation|co|company|fund|funds|partners|n\.a|"
                     r"credit union|securities|financial|investments?|brokerage|insurance|services|group|"
                     r"association|foundation|university|college|hospital)\b\.?", re.I)
ORG = ("This looks like a company's name. Companies stay readable, so your AI knows who paid you "
       "or holds your account. Add it only if it identifies you.")
STATE = ("A state on its own stays readable: it's removed next to your ZIP, as part of your address. "
         "Add your whole address instead, and it's removed wherever it's printed.")
MASKED = ("Only the last 4 digits are printed. Add the full SSN to this person and it is removed "
          "in every form it's printed, this one included.")
THEIR_EIN = "An employer's or bank's EIN usually stays readable. Add it only if it's yours."


def suggest(text: str, label: str = "", has_people: bool = True) -> tuple[str, dict, str | None]:
    """(kind, fields, note) for a value: a kind of profile.ITEMS, a row for its list, and
    a word of caution when adding it is probably not what the reviewer wants."""
    text = _tidy(text)
    kind, item = _suggest(text, label, has_people)
    note = None
    if MONEY.fullmatch(text):
        note = AMOUNT
    elif kind == "number" and COMPANY.search(text) and not digits(text):
        note = ORG
    elif MASKED_SSN.fullmatch(text):
        note = MASKED
    elif kind == "address" and text in STATES:
        note = STATE
    elif kind == "ein" and not re.search(r"\byour\b|\bhousehold\b|\btrust\b", label, re.I):
        note = THEIR_EIN
    return kind, item, note


def _tidy(text: str) -> str:
    """Without the punctuation a sentence put around it: "4321)." -> "4321"."""
    text = " ".join(text.split()).strip(" .,;:")
    if text.endswith(")") and "(" not in text:
        text = text[:-1]
    if text.startswith("(") and ")" not in text:
        text = text[1:]
    return text.strip(" .,;:")


def _suggest(text: str, label: str, has_people: bool) -> tuple[str, dict]:
    says = {kind for kind, pattern in LABELS if pattern.search(label)}
    ds = digits(text)
    letters = sum(c.isalpha() for c in text)
    you = {"role": "taxpayer" if not has_people else "other"}

    if EMAIL_RE.fullmatch(text):
        return "email", {"value": text}
    if PTIN.fullmatch(text.replace(" ", "")) or ("ptin" in says and len(ds) == 8):
        return "preparer", {"name": "", "firm": "", "ptin": text}
    if MASKED_SSN.fullmatch(text):
        return "person", {"name": "", "ssn": "", **you}
    if SSN.fullmatch(text) or ("ssn" in says and len(ds) == 9 and not letters):
        return "person", {"name": "", "ssn": text, **you}
    if EIN.fullmatch(text) or ("ein" in says and len(ds) == 9 and not letters):
        return "ein", {"ein": text, "description": _describe(label)}
    if "account" in says and ds and not MONEY.fullmatch(text):
        return "account", {"institution": "", "number": text, "routing": "", "name": ""}
    if "routing" in says and len(ds) == 9:
        return "account", {"institution": "", "number": "", "routing": ds, "name": ""}
    if 13 <= len(ds) <= 19 and not letters and ("card" in says or luhn(ds)) and "account" not in says:
        return "card", {"number": text, "description": _describe(label)}
    if PHONE.fullmatch(text) or ("phone" in says and len(ds) in (10, 11) and not letters):
        return "phone", {"value": text}
    if "dob" in says and DOB_RE.fullmatch(text):
        return "person", {"name": "", "dob": text, **you}
    if text in STATES:  # "CA" in a W-2's state box: part of an address, never a name
        return "address", {"street": "", "city": "", "state": text, "zip": ""}
    if m := CITY_LINE.fullmatch(text):
        return "address", {"street": "", **m.groupdict()}
    if STREET.match(text):
        return "address", {"street": text, "city": "", "state": "", "zip": ""}
    if ds and len(re.sub(r"[^A-Za-z0-9]", "", text)) >= 4:
        return "number", {"number": text, "description": _describe(label)}
    if COMPANY.search(text) and "preparer" not in says:
        return "number", {"number": text, "description": "Company name"}
    if "preparer" in says and letters:
        person = bool(NAME.fullmatch(text)) and "firm" not in label.lower()
        return "preparer", {"name": text if person else "", "firm": "" if person else text}
    if "address" in says and letters:
        return "address", {"street": text, "city": "", "state": "", "zip": ""}
    if NAME.fullmatch(text) or re.fullmatch(r"[A-Z][A-Za-z'’-]+", text):  # one word: they add the rest
        return "person", {"name": _person_name(text), **you}
    return "number", {"number": text, "description": _describe(label)}


def _describe(label: str) -> str:
    """A label for the reviewer's own list: the form's words, without box numbers, and only
    when they say what the value is (the words before a value can be another value)."""
    if not any(pattern.search(label) for _, pattern in LABELS):
        return ""
    words = [w for w in label.split() if not re.fullmatch(r"\d+[a-z]?|[\W_]+", w)]
    return " ".join(words)[:60]
