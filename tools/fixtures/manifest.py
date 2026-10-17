"""Ground truth for each generated fixture.

`redact` lists every identifier written into the document; after redaction
none of them may survive. `keep` lists values that must still be there
(amounts, CUSIPs, public org names) so we notice over-redaction.
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
