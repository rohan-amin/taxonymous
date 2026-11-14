"""Placeholder assignment and the placeholder -> real value map.

The same identity always gets the same placeholder, across pages, documents
and runs, so the AI can still tell that two documents mention the same
account. A person's label says who they are on the return, not who they are: their role
(<TAXPAYER>, <SPOUSE>, <DEPENDENT_2>, <SUCCESSOR_TRUSTEE_1>), with each of their details
under it (<TAXPAYER_SSN>, <DEPENDENT_2_DOB>), so the AI never pairs one person's name with
another's SSN, and reads relationships as such. A person keeps their label (the "base",
kept here by their permanent id) until their role changes; a label once used is never
given to anyone else. A preparer is <PREPARER_1> (<PREPARER_1_PTIN>...); an SSN not in the
profile <UNKNOWN_SSN_1>; a family surname on its own <SURNAME_1>; everything else is
numbered: <ACCT_1>. The map is the only place real values are stored; it lives in the
vault folder and is never uploaded.
"""

import fcntl
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path

from .files import write_json

ID = r"(\d{1,3}|e[0-9a-f]{8})"  # a profile entry's permanent id (profile.py)
PERSON_PART = re.compile(rf"(person|ssn|dob|occupation|employer):{ID}")  # a person's detail, by id
PERSON_SUFFIX = {"person": "", "ssn": "_SSN", "dob": "_DOB", "occupation": "_OCCUPATION",
                 "employer": "_EMPLOYER"}
PREPARER_PART = re.compile(rf"preparer:{ID}:(name|firm|addr|phone|ein|ptin)")
PREPARER_SUFFIX = {"name": "", "firm": "_FIRM", "addr": "_ADDR", "phone": "_PHONE", "ein": "_EIN",
                   "ptin": "_PTIN"}
# a role's label; the taxpayer and the spouse are one each, everyone else is numbered
ROLE_LABEL = {"taxpayer": "TAXPAYER", "spouse": "SPOUSE", "dependent": "DEPENDENT", "grantor": "GRANTOR",
              "trustee": "TRUSTEE", "successor_trustee": "SUCCESSOR_TRUSTEE", "beneficiary": "BENEFICIARY",
              "executor": "EXECUTOR", "decedent": "DECEDENT", "other": "PERSON", "preparer": "PREPARER"}
ONE_OF = {"TAXPAYER", "SPOUSE"}
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
        self.roles: dict[str, str] = {}  # a person's id (a preparer's "preparer:<id>") -> role
        self.bases: dict[str, str] = {}  # that id -> its label base ("DEPENDENT_2"): kept in the file
        self.retired: set[str] = set()  # bases given up (a role changed): never given to anyone else
        if self.path and self.path.exists():
            data = self._read_all()
            self.bases = data.get("bases", {})
            self.retired = set(data.get("retired", []))
            self._use(data.get("placeholders", {}))

    def _read_all(self) -> dict:
        if self.path and self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def _read(self) -> dict[str, dict]:
        return self._read_all().get("placeholders", {})

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
                data = self._read_all()
                entries = data.get("placeholders", {})
                self.bases = {**self.bases, **data.get("bases", {})}  # the file's win: one base, one person
                self.retired |= set(data.get("retired", []))
                for ph, values in self._seen.items():
                    if ph in entries:  # gone from the file: forgotten meanwhile, stays gone
                        entries[ph]["values"] += [v for v in values if v not in entries[ph]["values"]]
                self._seen = {}
                self._use(entries)
                yield
                write_json(self.path, {"placeholders": self.entries, "bases": self.bases,
                                       "retired": sorted(self.retired)})
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def placeholder(self, kind: str, key: str, value: str) -> str:
        ph = self.by_key.get(key)
        if ph is None:
            with self._locked():
                ph = self.by_key.get(key)  # another process may have named it meanwhile
                if ph is None:
                    ph = self._new_name(kind, key)
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

    def _new_name(self, kind: str, key: str) -> str:
        """A name not taken, by the module's scheme."""
        name = self._expected(kind, key)
        if name and name not in self.entries:
            return name
        prefix = ("UNKNOWN_SSN" if kind == "SSN" else "SURNAME" if key.startswith("surname:") else kind)
        return self._next(prefix)

    def _expected(self, kind: str, key: str) -> str | None:
        """A profile person's or preparer's name for this detail, from their label base."""
        if m := PERSON_PART.fullmatch(key):
            who = m.group(2)
            return f"<{self._base(who, self.roles.get(who, 'other'))}{PERSON_SUFFIX[m.group(1)]}>"
        if m := PREPARER_PART.fullmatch(key):
            return f"<{self._base('preparer:' + m.group(1), 'preparer')}{PREPARER_SUFFIX[m.group(2)]}>"
        return None

    def _base(self, who: str, role: str) -> str:
        """Their label base: kept while their role is the same; a new one when it changes,
        never one anyone has had."""
        label = ROLE_LABEL.get(role, "PERSON")
        base = self.bases.get(who)
        if base and (base == label or re.fullmatch(rf"{label}_\d+", base)):
            return base
        if base:
            self.retired.add(base)  # their old label is no one's from now on
        used = set(self.bases.values()) | self.retired
        if label in ONE_OF and label not in used:
            base = label
        else:
            n = 1 + max((int(b.rsplit("_", 1)[1]) for b in used if re.fullmatch(rf"{label}_\d+", b)),
                        default=1 if label in ONE_OF else 0)
            base = f"{label}_{n}"
        self.bases[who] = base
        return base

    def _next(self, prefix: str) -> str:
        n = 1
        while f"<{prefix}_{n}>" in self.entries:  # never reuse a name still taken
            n += 1
        return f"<{prefix}_{n}>"

    def relabel(self) -> bool:
        """Every label of a profile person or preparer as their role says now (a role changed,
        or the vault is from before role labels), and an unlisted SSN or a lone surname as
        such. Returns whether any changed: then the documents are cleaned again."""
        def wanted(ph: str, e: dict) -> str | None:
            name = self._expected(e["kind"], e["key"])
            if name is None and e["kind"] == "SSN" and not ph.startswith("<UNKNOWN_SSN_"):
                return ""
            if name is None and e["key"].startswith("surname:") and not ph.startswith("<SURNAME_"):
                return ""
            return name

        def in_profile(key: str) -> bool:  # a person or preparer the profile still has
            if m := PERSON_PART.fullmatch(key):
                return m.group(2) in self.roles
            if m := PREPARER_PART.fullmatch(key):
                return f"preparer:{m.group(1)}" in self.roles
            return True  # not a person's: an unlisted SSN, a surname, an account...

        todo = [ph for ph, e in self.entries.items()
                if in_profile(e["key"]) and wanted(ph, e) not in (None, ph)]
        if not todo:
            return False
        with self._locked():
            changed = False
            for ph in [p for p in list(self.entries) if p in todo]:
                e = self.entries[ph]
                name = wanted(ph, e)
                if name == "":  # an unlisted SSN or a surname still under its old name
                    name = self._next("UNKNOWN_SSN" if e["kind"] == "SSN" else "SURNAME")
                if name and name != ph and name not in self.entries:
                    self.entries[name] = self.entries.pop(ph)
                    changed = True
            self._use(self.entries)
        return changed

    def rekey(self, keys: dict[str, str]) -> None:
        """Entries known by another key from now on (old key -> new); one already under the
        new key wins. For phones and emails, once known by their place in the list."""
        if not any(e["key"] in keys for e in self.entries.values()):
            return
        with self._locked():
            taken = {e["key"] for e in self.entries.values()}
            for e in self.entries.values():
                if e["key"] in keys and keys[e["key"]] not in taken:
                    e["key"] = keys[e["key"]]
            self._use(self.entries)

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
