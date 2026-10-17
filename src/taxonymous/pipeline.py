"""Workspace pipeline: inbox/ -> work/ -> verify -> ready-to-upload/.

<workspace>/
  inbox/              originals (never modified)
  work/               redacted outputs and verification reports, including failures
  ready-to-upload/    only files whose verification passed
  vault/              profile.json (your identifiers) and placeholders.json
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .detect import ProfileMatcher
from .profile import Profile
from .redact import redact_pdf
from .vault import Vault
from .verify import Known, Report, verify


@dataclass
class Outcome:
    source: Path
    report: Report
    released: list[Path]


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
        return sorted(p for p in self.inbox.iterdir() if p.suffix.lower() == ".pdf")


def process(src: Path, ws: Workspace, matcher: ProfileMatcher, vault: Vault, ocr: bool = True) -> Outcome:
    stem = Path(src).stem
    ready = [ws.ready / f"{stem}.redacted.pdf", ws.ready / f"{stem}.redacted.txt"]
    for old in ready:  # never leave a stale release behind if this run fails
        old.unlink(missing_ok=True)

    result = redact_pdf(src, ws.work, matcher, vault)
    known = [Known(f.kind, f.text) for f in result.findings]
    known += [Known(e["kind"], v) for e in vault.entries.values() for v in e["values"]]
    report = verify(result.pdf, matcher, known, result.text, ocr=ocr)
    (ws.work / f"{stem}.report.json").write_text(json.dumps(report.to_json(), indent=2) + "\n")

    released: list[Path] = []
    if report.passed:
        for built, dest in zip((result.pdf, result.text), ready, strict=True):
            shutil.copyfile(built, dest)
            released.append(dest)
    return Outcome(Path(src), report, released)


def run(ws: Workspace, ocr: bool = True) -> list[Outcome]:
    if not ws.profile_path.exists():
        raise FileNotFoundError(f"no profile at {ws.profile_path}; create it first (see README)")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    return [process(src, ws, matcher, vault, ocr) for src in ws.pending()]
