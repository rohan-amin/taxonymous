"""Placeholder assignment and the placeholder -> real value map.

The same identity always gets the same placeholder, across pages, documents
and runs, so Claude can still tell that two documents mention the same
account. The map is the only place real values are stored; it lives in the
vault folder and is never uploaded.
"""

import fcntl
import json
import string
import threading
from contextlib import contextmanager
from pathlib import Path

from .files import write_json

LETTER_KINDS = {"SSN", "NAME"}  # <SSN_A>, <NAME_B>; everything else is numbered: <ACCT_1>
_threads = threading.Lock()  # flock is per process: threads in one also take turns


class Vault:
    """The map in memory, kept in step with the file: documents are cleaned in several
    processes at once (and the command line may run beside the app), so whenever one needs
    a placeholder it hasn't got, it locks the file, reads what the others wrote, and only
    then names a new one. The same value gets the same placeholder whoever asks first.
    Each process writes back only what it changed (new values seen for a placeholder), on
    top of what the file has, so one forgotten elsewhere is never brought back."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.by_key: dict[str, str] = {}
        self.entries: dict[str, dict] = {}
        self._seen: dict[str, list[str]] = {}  # values added here since the last write
        if self.path and self.path.exists():
            self._use(self._read())

    def _read(self) -> dict[str, dict]:
        if self.path and self.path.exists():
            return json.loads(self.path.read_text()).get("placeholders", {})
        return {}

    def _use(self, entries: dict[str, dict]) -> None:
        self.entries = entries
        self.by_key = {e["key"]: ph for ph, e in entries.items()}

    @contextmanager
    def _locked(self):
        """The file to ourselves, read again with what was seen here put in; written back after."""
        if not self.path:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _threads, open(self.path.with_name(f".{self.path.name}.lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                entries = self._read()
                for ph, values in self._seen.items():
                    if ph in entries:  # gone from the file: forgotten meanwhile, stays gone
                        entries[ph]["values"] += [v for v in values if v not in entries[ph]["values"]]
                self._seen = {}
                self._use(entries)
                yield
                write_json(self.path, {"placeholders": self.entries})
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def placeholder(self, kind: str, key: str, value: str) -> str:
        ph = self.by_key.get(key)
        if ph is None:
            with self._locked():
                ph = self.by_key.get(key)  # another process may have named it meanwhile
                if ph is None:
                    n = sum(1 for p in self.entries if p.startswith(f"<{kind}_"))
                    ph = _name(kind, n)
                    while ph in self.entries:  # a forgotten entry left a gap: never reuse a name still taken
                        n += 1
                        ph = _name(kind, n)
                    self.by_key[key] = ph
                    self.entries[ph] = {"kind": kind, "key": key, "values": []}
                if value and value not in self.entries[ph]["values"]:
                    self.entries[ph]["values"].append(value)
            return ph
        values = self.entries[ph]["values"]
        if value and value not in values:
            values.append(value)
            if self.path:
                self._seen.setdefault(ph, []).append(value)  # written by save()
        return ph

    def forget(self, key: str) -> None:
        """Drop an identity (a reviewer removed the box they drew), so its value is no
        longer a known value that verification looks for."""
        with self._locked():
            ph = self.by_key.pop(key, None)
            if ph is not None:
                self.entries.pop(ph, None)

    def save(self) -> None:
        with self._locked():
            pass


def _name(kind: str, n: int) -> str:
    return f"<{kind}_{_letters(n) if kind in LETTER_KINDS else n + 1}>"


def _letters(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s
