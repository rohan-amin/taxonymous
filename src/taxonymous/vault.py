"""Placeholder assignment and the placeholder -> real value map.

The same identity always gets the same placeholder, across pages, documents
and runs, so Claude can still tell that two documents mention the same
account. The map is the only place real values are stored; it lives in the
vault folder and is never uploaded.
"""

import json
import string
from pathlib import Path

LETTER_KINDS = {"SSN", "NAME"}  # <SSN_A>, <NAME_B>; everything else is numbered: <ACCT_1>


class Vault:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.by_key: dict[str, str] = {}
        self.entries: dict[str, dict] = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.entries = data.get("placeholders", {})
            self.by_key = {e["key"]: ph for ph, e in self.entries.items()}

    def placeholder(self, kind: str, key: str, value: str) -> str:
        ph = self.by_key.get(key)
        if ph is None:
            n = sum(1 for p in self.entries if p.startswith(f"<{kind}_"))
            suffix = _letters(n) if kind in LETTER_KINDS else str(n + 1)
            ph = f"<{kind}_{suffix}>"
            self.by_key[key] = ph
            self.entries[ph] = {"kind": kind, "key": key, "values": []}
        values = self.entries[ph]["values"]
        if value not in values:
            values.append(value)
        return ph

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"placeholders": self.entries}, indent=2) + "\n")


def _letters(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s
