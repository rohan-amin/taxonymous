"""Ground truth for each generated fixture.

`redact` lists the household's identifiers written into the document, and anyone's SSN:
after redaction none of them may survive. `keep` lists values that must still be there
(amounts, CUSIPs, institutions, and other people's names and numbers, which only your
details and SSN shapes are removed for) so we notice over-redaction.
"""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Item:
    value: str
    type: str
    third_party: bool = False


@dataclass
class DocRecord:
    file: str
    source: str  # e.g. "irs:fw2", "synthetic:brokerage", "scan:w2"
    kind: str  # "text" (has a text layer), "scan" (image-only PDF), "photo" (JPEG)
    redact: list[Item] = field(default_factory=list)
    keep: list[Item] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add_redact(self, value: str, type: str, third_party: bool = False) -> str:
        if third_party and type != "SSN":
            return self.add_keep(value, type)  # not listed and not SSN-shaped: it stays
        item = Item(value, type, third_party)
        if value and item not in self.redact:
            self.redact.append(item)
        return value

    def add_keep(self, value: str, type: str) -> str:
        item = Item(value, type)
        if value and item not in self.keep:
            self.keep.append(item)
        return value

    def to_json(self) -> dict:
        return asdict(self)


def settle(records: list[DocRecord], profile: dict) -> None:
    """Square each document's `keep` with the household's profile: one of its numbers (the
    household's EIN as a nanny's employer) is removed after all, and a name with one of its
    names in it ("The Abernathy Family Trust", a beneficiary who shares the surname) is
    partly removed, so it is in neither list."""
    import re

    def digits(s: str) -> str:
        return re.sub(r"\D", "", s)
    numbers = {digits(n) for p in profile["people"] for n in [p.get("ssn", "")] if n}
    numbers |= {digits(n) for k in ("eins", "accounts", "cards", "phones") for n in profile.get(k, [])}
    words = {w.lower() for p in profile["people"] for w in p["name"].split() if len(w) > 2}
    for rec in records:
        keep = []
        for item in rec.keep:
            if len(digits(item.value)) >= 4 and digits(item.value) in numbers:
                rec.add_redact(item.value, item.type)
            elif not words & {w.lower() for w in re.findall(r"[A-Za-z]+", item.value)}:
                keep.append(item)
        rec.keep = keep
