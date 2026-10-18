"""Workspace pipeline: inbox/ -> work/ -> verify -> ready-to-upload/.

<workspace>/
  inbox/              originals (never modified): PDFs, scans, photos
  work/               redacted outputs, box lists and verification reports, including failures
  ready-to-upload/    only files whose verification passed
  vault/              profile.json (your identifiers), placeholders.json, and overrides/
                      (the reviewer's per-document decisions)
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .detect import ProfileMatcher
from .profile import Profile
from .redact import SUFFIXES, Overrides, Result, output_stem, redact_file
from .vault import Vault
from .verify import Issue, Known, Report, Shown, verify


@dataclass
class Outcome:
    source: Path
    report: Report
    released: list[Path]
    result: Result | None = None


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.inbox = self.root / "inbox"
        self.work = self.root / "work"
        self.ready = self.root / "ready-to-upload"
        self.vault_dir = self.root / "vault"
        self.profile_path = self.vault_dir / "profile.json"

    def init(self) -> None:
        for d in (self.inbox, self.work, self.ready, self.vault_dir):
            d.mkdir(parents=True, exist_ok=True)

    def pending(self) -> list[Path]:
        return sorted(p for p in self.inbox.iterdir() if p.suffix.lower() in SUFFIXES)

    def overrides_path(self, stem: str) -> Path:
        return self.vault_dir / "overrides" / f"{stem}.json"

    def outputs(self, stem: str) -> dict[str, Path]:
        return {"pdf": self.work / f"{stem}.redacted.pdf", "txt": self.work / f"{stem}.redacted.txt",
                "report": self.work / f"{stem}.report.json", "regions": self.work / f"{stem}.regions.json"}

    def released(self, stem: str) -> list[Path]:
        return [self.ready / f"{stem}.redacted.pdf", self.ready / f"{stem}.redacted.txt"]


def allowed_values(vault: Vault, overrides: Overrides) -> set[str]:
    """The real values behind the placeholders a reviewer kept in the clear."""
    return {v for ph in overrides.keep for v in vault.entries.get(ph, {}).get("values", [])}


def process(src: Path, ws: Workspace, matcher: ProfileMatcher, vault: Vault, ocr: bool = True,
            release: bool = True) -> Outcome:
    """Redact and verify one file. `release` copies it to ready-to-upload/ when it passes;
    the review UI releases separately, on export."""
    stem = output_stem(Path(src))
    for old in ws.released(stem):  # never leave a stale release behind if this run fails
        old.unlink(missing_ok=True)
    out = ws.outputs(stem)
    overrides = Overrides.load(ws.overrides_path(stem))

    try:
        result = redact_file(src, ws.work, matcher, vault, overrides)
    except Exception as e:  # one unreadable file (locked, corrupt) is held back; the rest still run
        result = None
        report = Report(pdf=str(src), issues=[Issue("error", type(e).__name__, detail=str(e)[:300])])
        out["regions"].unlink(missing_ok=True)
    else:
        found = [Known(f.kind, f.text) for f in result.findings]
        report = _verify(ws, stem, matcher, vault, overrides, found, ocr)
        out["regions"].write_text(json.dumps(result.regions_json(), indent=2) + "\n")
    out["report"].write_text(json.dumps(report.to_json(), indent=2) + "\n")
    outcome = Outcome(Path(src), report, [], result)
    if release:
        outcome.released = export(ws, stem)
    return outcome


def _verify(ws: Workspace, stem: str, matcher: ProfileMatcher, vault: Vault, overrides: Overrides,
            known: list[Known], ocr: bool) -> Report:
    """Everything this document's redaction found, and every value in the vault (from any
    document), except what the reviewer kept in the clear here: a whole placeholder, or
    single boxes."""
    allowed = allowed_values(vault, overrides)
    known = known + [Known(e["kind"], v) for e in vault.entries.values() for v in e["values"]]
    shown = [Shown(n, r, vault.entries.get(ph, {}).get("values", [])) for n, r, ph in overrides.show]
    out = ws.outputs(stem)
    return verify(out["pdf"], matcher, [k for k in known if k.value not in allowed], out["txt"], ocr=ocr,
                  allowed=allowed, shown=shown)


def reverify(ws: Workspace, stem: str, matcher: ProfileMatcher, vault: Vault, ocr: bool = True) -> Report:
    """Verify a document's current output again, without redacting it again."""
    for old in ws.released(stem):
        old.unlink(missing_ok=True)
    report = _verify(ws, stem, matcher, vault, Overrides.load(ws.overrides_path(stem)), [], ocr)
    ws.outputs(stem)["report"].write_text(json.dumps(report.to_json(), indent=2) + "\n")
    return report


def export(ws: Workspace, stem: str) -> list[Path]:
    """Copy a document's outputs to ready-to-upload/, only if its last verification passed."""
    out = ws.outputs(stem)
    if not out["report"].exists() or not json.loads(out["report"].read_text()).get("passed"):
        return []
    released = []
    for built, dest in zip((out["pdf"], out["txt"]), ws.released(stem), strict=True):
        shutil.copyfile(built, dest)
        released.append(dest)
    return released


def run(ws: Workspace, ocr: bool = True) -> list[Outcome]:
    if not ws.profile_path.exists():
        raise FileNotFoundError(f"no profile at {ws.profile_path}; create it first (see README)")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    return [process(src, ws, matcher, vault, ocr) for src in ws.pending()]
