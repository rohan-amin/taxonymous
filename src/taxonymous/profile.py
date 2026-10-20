"""The user's own identifiers, entered once and matched everywhere.

Schema (plain JSON, lives in the vault folder, never committed):
{
  "people":    [{"name": "First M Last", "ssn": "...", "dob": "MM/DD/YYYY", "role": "taxpayer",
                 "deceased": false, "dod": "MM/DD/YYYY", "occupation": "..."}],
  "addresses": [{"street": "...", "city": "...", "state": "XX", "zip": "....."}],
  "phones": [...], "emails": [...],
  "accounts":  [{"institution": "...", "number": "...", "routing": "...", "name": "..."}],
  "eins":      [{"ein": "..", "description": "..."}],
  "cards":     [{"number": "...", "description": "..."}],
  "numbers":   [{"number": "...", "description": "..."}],
  "preparers": [{"name": "...", "firm": "...", "street": "...", "city": "...", "state": "XX",
                 "zip": ".....", "phone": "...", "ptin": "P........", "ein": ".."}]
}
Only the numbers are redacted; institution, account name and description are the user's
own labels. Other numbers are any IDs of yours (an e-file signature PIN, a client ID). A
preparer's every field is redacted. Only these details (and anything shaped like an SSN)
are redacted.
Older profiles listed plain strings for accounts, EINs and cards; both load.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Person:
    name: str
    ssn: str = ""
    dob: str = ""
    role: str = ""
    deceased: bool = False
    dod: str = ""  # date of death: for the record, and left readable in documents (it matters for taxes)
    occupation: str = ""  # as the return prints it, under the signature

    @property
    def parts(self) -> tuple[str, list[str], str]:
        words = self.name.split()
        return words[0], words[1:-1], words[-1]


@dataclass
class Address:
    street: str
    city: str = ""
    state: str = ""
    zip: str = ""


@dataclass
class Preparer:
    """The person or firm who prepared the return: everything about them is redacted."""
    name: str = ""
    firm: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    phone: str = ""
    ptin: str = ""
    ein: str = ""


@dataclass
class Profile:
    people: list[Person] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    eins: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)
    routing: list[str] = field(default_factory=list)  # the accounts' routing numbers
    numbers: list[str] = field(default_factory=list)  # other IDs: PINs, client IDs
    preparers: list[Preparer] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Profile":
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        return cls(
            people=[Person(**p) for p in d.get("people", [])],
            addresses=[Address(**a) for a in d.get("addresses", [])],
            phones=list(d.get("phones", [])),
            emails=list(d.get("emails", [])),
            eins=[_number(e, "eins") for e in d.get("eins", [])],
            accounts=[_number(a, "accounts") for a in d.get("accounts", [])],
            cards=[_number(c, "cards") for c in d.get("cards", [])],
            routing=[r for a in d.get("accounts", []) if (r := str(_record(a, "accounts")["routing"]))],
            numbers=[_number(n, "numbers") for n in d.get("numbers", [])],
            preparers=[Preparer(**{k: str(v or "") for k, v in p.items() if k in PREPARER_FIELDS})
                       for p in d.get("preparers", [])],
        )


# per kind of record: the field that is the identifier, and all of its fields
RECORDS = {
    "accounts": ("number", ("institution", "number", "routing", "name")),
    "eins": ("ein", ("ein", "description")),
    "cards": ("number", ("number", "description")),
    "numbers": ("number", ("number", "description")),
}
PREPARER_FIELDS = ("name", "firm", "street", "city", "state", "zip", "phone", "ptin", "ein")


def _record(item, key: str) -> dict:
    """A record as a dict, from either form (a plain string is the older format)."""
    main, fields = RECORDS[key]
    if isinstance(item, dict):
        return {f: item.get(f) or "" for f in fields}
    return {f: (item if f == main else "") for f in fields}


def _number(item, key: str) -> str:
    return str(_record(item, key)[RECORDS[key][0]])


ROLES = ("taxpayer", "spouse", "dependent", "other")
DOB_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
ZIP_RE = re.compile(r"\d{5}(?:-\d{4})?")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_ok(v: str) -> bool:
    ds = _digits(v)
    return len(ds) == 10 or len(ds) == 11 and ds[0] == "1"


def _account_ok(v: str) -> bool:
    return len(re.sub(r"[^A-Za-z0-9]", "", v)) >= 4


def clean(d: dict) -> tuple[dict, dict[str, str]]:
    """A profile as typed into a form: trimmed, empty rows dropped, SSNs, EINs and dates in
    one format. Returns (profile, errors by field path like "people.0.ssn"). Error
    messages never repeat the value."""
    errors: dict[str, str] = {}  # paths use the row numbers as sent, empty rows included
    out: dict = {k: [] for k in ("people", "addresses", "phones", "emails", "eins", "accounts", "cards",
                                 "numbers", "preparers")}

    def text(v) -> str:
        return " ".join(str(v or "").split())

    def when(value: str, field: str, message: str) -> date | None:
        m = DOB_RE.fullmatch(value)
        try:
            if not m:
                raise ValueError
            month, day, year = map(int, m.groups())
            return date(year, month, day)
        except ValueError:
            errors[field] = message
            return None

    for i, raw in enumerate(d.get("people") or []):
        p = {k: text(raw.get(k)) for k in ("name", "ssn", "dob", "role", "dod", "occupation")}
        p["deceased"] = bool(raw.get("deceased"))
        if not any(p[k] for k in ("name", "ssn", "dob", "dod", "occupation")):
            continue
        at = f"people.{i}"
        if len(p["name"].split()) < 2:
            errors[f"{at}.name"] = "Enter a first and last name."
        if p["ssn"]:
            ds = _digits(p["ssn"])
            if len(ds) != 9:
                errors[f"{at}.ssn"] = "An SSN or ITIN has 9 digits."
            else:
                p["ssn"] = f"{ds[:3]}-{ds[3:5]}-{ds[5:]}"
        born = when(p["dob"], f"{at}.dob", "Use MM/DD/YYYY.") if p["dob"] else None
        if born:
            p["dob"] = f"{born:%m/%d/%Y}"
        if not p["deceased"]:
            p["dod"] = ""
        elif not p["dod"]:
            errors[f"{at}.dod"] = "Enter the date of death (MM/DD/YYYY)."
        elif died := when(p["dod"], f"{at}.dod", "Use MM/DD/YYYY."):
            if died > date.today():
                errors[f"{at}.dod"] = "The date of death can't be in the future."
            elif born and died < born:
                errors[f"{at}.dod"] = "The date of death is before the date of birth."
            p["dod"] = f"{died:%m/%d/%Y}"
        p["role"] = p["role"] if p["role"] in ROLES else "other"
        out["people"].append(p)

    for i, a in enumerate(d.get("addresses") or []):
        a = {k: text(a.get(k)) for k in ("street", "city", "state", "zip")}
        if not any(a.values()):
            continue
        at = f"addresses.{i}"
        if not a["street"]:
            errors[f"{at}.street"] = "Enter the street address."
        if a["state"]:
            a["state"] = a["state"].upper()
            if not re.fullmatch(r"[A-Z]{2}", a["state"]):
                errors[f"{at}.state"] = "Use the two-letter state code."
        if a["zip"] and not ZIP_RE.fullmatch(a["zip"]):
            errors[f"{at}.zip"] = "Use 5 digits (or ZIP+4)."
        out["addresses"].append(a)

    for i, raw in enumerate(d.get("preparers") or []):
        r = {k: text(raw.get(k)) for k in PREPARER_FIELDS}
        if not any(r.values()):
            continue
        at = f"preparers.{i}"
        if not (r["name"] or r["firm"]):
            errors[f"{at}.name"] = "Enter the preparer's name or firm."
        if r["state"]:
            r["state"] = r["state"].upper()
            if not re.fullmatch(r"[A-Z]{2}", r["state"]):
                errors[f"{at}.state"] = "Use the two-letter state code."
        if r["zip"] and not ZIP_RE.fullmatch(r["zip"]):
            errors[f"{at}.zip"] = "Use 5 digits (or ZIP+4)."
        if r["phone"]:
            if _phone_ok(r["phone"]):
                ds = _digits(r["phone"])[-10:]
                r["phone"] = f"({ds[:3]}) {ds[3:6]}-{ds[6:]}"
            else:
                errors[f"{at}.phone"] = "Enter a 10-digit US phone number."
        if r["ptin"]:
            ds = _digits(r["ptin"])
            if len(ds) != 8 or not re.fullmatch(r"[Pp]?[\s-]*[\d\s-]+", r["ptin"]):
                errors[f"{at}.ptin"] = "A PTIN is P and 8 digits."
            else:
                r["ptin"] = "P" + ds
        if r["ein"]:
            if len(_digits(r["ein"])) != 9:
                errors[f"{at}.ein"] = "An EIN has 9 digits."
            else:
                r["ein"] = f"{_digits(r['ein'])[:2]}-{_digits(r['ein'])[2:]}"
        out["preparers"].append(r)

    lists = {
        "phones": (_phone_ok, "Enter a 10-digit US phone number."),
        "emails": (lambda v: EMAIL_RE.fullmatch(v), "Enter an email address."),
    }
    for key, (ok, message) in lists.items():
        for i, v in enumerate(d.get(key) or []):
            v = text(v)
            if not v:
                continue
            if not ok(v):
                errors[f"{key}.{i}"] = message
            elif key == "phones":
                ds = _digits(v)[-10:]  # without the +1 country code
                v = f"({ds[:3]}) {ds[3:6]}-{ds[6:]}"
            out[key].append(v)

    numbers = {  # the identifier of each record: (valid?, message, stored as)
        "accounts": (_account_ok, "Enter at least 4 letters or digits.", lambda v: v),
        "eins": (lambda v: len(_digits(v)) == 9, "An EIN has 9 digits.",
                 lambda v: f"{_digits(v)[:2]}-{_digits(v)[2:]}"),
        "cards": (lambda v: 13 <= len(_digits(v)) <= 19, "A card number has 13 to 19 digits.",
                  lambda v: " ".join(re.findall(r"\d{1,4}", _digits(v)))),
        "numbers": (_account_ok, "Enter at least 4 letters or digits.", lambda v: v),
    }
    for key, (ok, message, stored) in numbers.items():
        main = RECORDS[key][0]
        for i, item in enumerate(d.get(key) or []):
            r = {f: text(v) for f, v in _record(item, key).items()}
            if not any(r.values()):
                continue
            if not r[main]:
                errors[f"{key}.{i}.{main}"] = "Enter the number."
            elif not ok(r[main]):
                errors[f"{key}.{i}.{main}"] = message
            else:
                r[main] = stored(r[main])
            if r.get("routing"):  # an account's: optional
                if len(_digits(r["routing"])) != 9 or re.search(r"[A-Za-z]", r["routing"]):
                    errors[f"{key}.{i}.routing"] = "A routing number has 9 digits."
                else:
                    r["routing"] = _digits(r["routing"])
            out[key].append(r)
    return out, errors


# what the review screen can add in one step, from a document: the profile list it goes in
ITEMS = {"person": "people", "address": "addresses", "account": "accounts", "phone": "phones",
         "email": "emails", "ein": "eins", "card": "cards", "number": "numbers", "preparer": "preparers"}


def add_item(d: dict, kind: str, item: dict) -> tuple[dict, dict[str, str]]:
    """A profile with one more detail, seen in a document: `item` has the same fields as a
    row of that list in the form ({"value": ...} for a phone or email). Returns (profile,
    errors by field name); error messages never repeat a value."""
    key = ITEMS.get(kind)
    if key is None:
        return d, {"": "Unknown kind of detail."}
    if not any(str(v or "").strip() for v in item.values()):
        return d, {"": "Fill in the details first."}
    if key in ("phones", "emails"):
        item = item.get("value", "")
    elif key == "people":
        item = {"role": "other", **item}
    d = {**d, key: [*d.get(key, []), item]}
    out, errors = clean(d)
    at = f"{key}.{len(d[key]) - 1}"
    return out, {f.removeprefix(at).lstrip(".") or "value": m for f, m in errors.items()
                 if f == at or f.startswith(at + ".")}


def save(d: dict, path: Path) -> None:
    """Write a cleaned profile, readable by this user only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.replace(path)
