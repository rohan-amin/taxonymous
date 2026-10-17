"""The user's own identifiers, entered once and matched everywhere.

Schema (plain JSON, lives in the vault folder, never committed):
{
  "people":    [{"name": "First M Last", "ssn": "...", "dob": "MM/DD/YYYY", "role": "taxpayer"}],
  "addresses": [{"street": "...", "city": "...", "state": "XX", "zip": "....."}],
  "phones": [...], "emails": [...], "eins": [...], "accounts": [...], "cards": [...]
}
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Person:
    name: str
    ssn: str = ""
    dob: str = ""
    role: str = ""

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
