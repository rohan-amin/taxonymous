"""The user's own identifiers, entered once and matched everywhere.

Schema (plain JSON, lives in the vault folder, never committed):
{
  "people":    [{"name": "First M Last", "ssn": "...", "dob": "MM/DD/YYYY", "role": "taxpayer",
                 "deceased": false, "dod": "MM/DD/YYYY"}],
  "addresses": [{"street": "...", "city": "...", "state": "XX", "zip": "....."}],
  "phones": [...], "emails": [...], "eins": [...], "accounts": [...], "cards": [...]
}
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
class Profile:
    people: list[Person] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    eins: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)

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
            eins=list(d.get("eins", [])),
            accounts=list(d.get("accounts", [])),
            cards=list(d.get("cards", [])),
        )


ROLES = ("taxpayer", "spouse", "dependent", "other")
DOB_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
ZIP_RE = re.compile(r"\d{5}(?:-\d{4})?")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def clean(d: dict) -> tuple[dict, dict[str, str]]:
    """A profile as typed into a form: trimmed, empty rows dropped, SSNs, EINs and dates in
    one format. Returns (profile, errors by field path like "people.0.ssn"). Error
    messages never repeat the value."""
    errors: dict[str, str] = {}  # paths use the row numbers as sent, empty rows included
    out: dict = {k: [] for k in ("people", "addresses", "phones", "emails", "eins", "accounts", "cards")}

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
        p = {k: text(raw.get(k)) for k in ("name", "ssn", "dob", "role", "dod")}
        p["deceased"] = bool(raw.get("deceased"))
        if not any(p[k] for k in ("name", "ssn", "dob", "dod")):
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

    checks = {
        "phones": (lambda v: len(_digits(v)) == 10 or len(_digits(v)) == 11 and _digits(v)[0] == "1",
                   "Enter a 10-digit US phone number."),
        "emails": (lambda v: EMAIL_RE.fullmatch(v), "Enter an email address."),
        "eins": (lambda v: len(_digits(v)) == 9, "An EIN has 9 digits."),
        "accounts": (lambda v: len(re.sub(r"[^A-Za-z0-9]", "", v)) >= 4,
                     "Enter at least 4 letters or digits."),
        "cards": (lambda v: 13 <= len(_digits(v)) <= 19, "A card number has 13 to 19 digits."),
    }
    for key, (ok, message) in checks.items():
        for i, v in enumerate(d.get(key) or []):
            v = text(v)
            if not v:
                continue
            if not ok(v):
                errors[f"{key}.{i}"] = message
            elif key == "eins":
                ds = _digits(v)
                v = f"{ds[:2]}-{ds[2:]}"
            elif key == "phones":
                ds = _digits(v)[-10:]  # without the +1 country code
                v = f"({ds[:3]}) {ds[3:6]}-{ds[6:]}"
            out[key].append(v)
    return out, errors


def save(d: dict, path: Path) -> None:
    """Write a cleaned profile, readable by this user only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.touch(mode=0o600)
    tmp.chmod(0o600)
    tmp.write_text(json.dumps(d, indent=2) + "\n")
    tmp.replace(path)
