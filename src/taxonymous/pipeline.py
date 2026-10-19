"""Workspace pipeline: inbox/ -> work/ -> verify -> ready-to-upload/.

<workspace>/
  inbox/              originals (never modified): PDFs, scans, photos
  work/               redacted outputs, box lists and verification reports, including failures
  ready-to-upload/    only files whose verification passed
  vault/              profile.json (your identifiers), placeholders.json, and overrides/
                      (the reviewer's per-document decisions)
"""

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .detect import ProfileMatcher, pattern_hits, strong
from .pagetext import PageText
from .profile import Profile
from .progress import NONE, Progress
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
        """What export puts in ready-to-upload/: the redacted PDF only (the .txt stays in work/),
        under a name with your details taken out, as the pages have them: Claude sees it too."""
        return [self.ready / f"{self.public_name(stem)}.redacted.pdf"]

    def stale(self, stem: str) -> list[Path]:
        """Everything an export of this document may have left in ready-to-upload/, by any name."""
        return [*self.released(stem), *(self.ready / f"{stem}.redacted.{ext}" for ext in ("pdf", "txt"))]

    def public_name(self, stem: str) -> str:
        """The file name with each of your details and anything shaped like an identifier
        replaced by its kind: "30 - Fidelity Rohan Trust 1099" -> "30 - Fidelity NAME Trust 1099"."""
        if not self.profile_path.exists():
            return stem
        spots = [(f.start, f.end, f.kind) for f in _matcher(self.profile_path).find(PageText(0, stem))]
        spots += [(m.start(), m.end(), kind) for m, kind in pattern_hits(stem)]
        merged: list[list] = []
        for start, stop, kind in sorted(spots):
            # overlapping, or one name in pieces with an initial between ("Avery F Abernathy")
            between = stem[merged[-1][1]:start] if merged else ""
            if merged and (start < merged[-1][1]
                           or merged[-1][2] == kind and re.fullmatch(r"\s*(?:\w\.?\s+)?", between)):
                merged[-1][1] = max(merged[-1][1], stop)
            else:
                merged.append([start, stop, kind])
        for start, stop, kind in reversed(merged):
            stem = stem[:start] + kind + stem[stop:]
        return stem


_matchers: dict[tuple[Path, int], ProfileMatcher] = {}


def _matcher(profile: Path) -> ProfileMatcher:
    key = (profile, profile.stat().st_mtime_ns)  # asked for every document on every listing
    if key not in _matchers:
        _matchers.clear()
        _matchers[key] = ProfileMatcher(Profile.load(profile))
    return _matchers[key]


def placeholders_of(ws: Workspace, stem: str) -> set[str]:
    """The placeholders a document's last redaction uses."""
    regions = ws.outputs(stem)["regions"]
    return {r["placeholder"] for r in json.loads(regions.read_text())} if regions.exists() else set()


def in_use(ws: Workspace, current: tuple[str, Result] | None = None) -> set[str]:
    """Placeholders that some document's latest redaction uses: from each document's regions
    file, or for the one being redacted now, from this run. A value the vault remembers from
    an earlier run that nothing redacts any more (a detector mistake since fixed) isn't one."""
    return set(users(ws, current))


def users(ws: Workspace, current: tuple[str, Result] | None = None) -> dict[str, list[str]]:
    """For each placeholder in use, the inbox file names of the documents that use it."""
    out: dict[str, list[str]] = {}
    for src in ws.pending():
        stem = output_stem(src)
        if current and stem == current[0]:
            used = {r.placeholder for r in current[1].regions}
        elif ws.outputs(stem)["regions"].exists():
            used = {r["placeholder"] for r in json.loads(ws.outputs(stem)["regions"].read_text())}
        else:
            continue
        for ph in used:
            out.setdefault(ph, []).append(src.name)
    return out


def allowed_values(vault: Vault, overrides: Overrides) -> set[str]:
    """The real values behind the placeholders a reviewer kept in the clear."""
    return {v for ph in overrides.keep for v in vault.entries.get(ph, {}).get("values", [])}


def process(src: Path, ws: Workspace, matcher: ProfileMatcher, vault: Vault, ocr: bool = True,
            release: bool = True, progress: Progress = NONE) -> Outcome:
    """Redact and verify one file. `release` copies it to ready-to-upload/ when it passes;
    the review UI releases separately, on export."""
    stem = output_stem(Path(src))
    for old in ws.stale(stem):  # never leave a stale release behind if this run fails
        old.unlink(missing_ok=True)
    out = ws.outputs(stem)
    overrides = Overrides.load(ws.overrides_path(stem))

    try:
        result = redact_file(src, ws.work, matcher, vault, overrides, progress=progress)
    except Exception as e:  # one unreadable file (locked, corrupt) is held back; the rest still run
        result = None
        report = Report(pdf=str(src), issues=[Issue("error", type(e).__name__, detail=str(e)[:300])])
        out["regions"].unlink(missing_ok=True)
    else:
        found = [Known(f.kind, f.text) for f in result.findings]
        report = _verify(ws, stem, matcher, vault, overrides, found, ocr, result, progress)
        retry = _text_layer_mismatch(report, result)
        if retry:
            # what OCR sees on these pages isn't what their text layer says (a broken font
            # encoding, text drawn as outlines): redact them from what they show instead
            result = redact_file(src, ws.work, matcher, vault, overrides, as_images=retry, progress=progress)
            found = [Known(f.kind, f.text) for f in result.findings]
            report = _verify(ws, stem, matcher, vault, overrides, found, ocr, result, progress)
            pages = ", ".join(str(n + 1) for n in sorted(retry))
            report.notes.append(f"Page(s) {pages}: the text layer didn't match what the page shows, "
                                "so the page was rebuilt as an image from OCR.")
        out["regions"].write_text(json.dumps(result.regions_json(), indent=2) + "\n")
    out["report"].write_text(json.dumps(report.to_json(), indent=2) + "\n")
    outcome = Outcome(Path(src), report, [], result)
    if release:
        outcome.released = export(ws, stem)
    return outcome


def _text_layer_mismatch(report: Report, result: Result) -> frozenset[int]:
    """0-based text pages where OCR of the output found something their text layer doesn't
    have. When the text layer has it too, the value is plainly there, and rebuilding the
    page from OCR would find no more than the text layer did."""
    in_text = {(i.page, i.kind) for i in report.issues if i.check == "text"}
    return frozenset(i.page - 1 for i in report.issues
                     if i.check == "ocr" and i.page and i.page not in result.image_pages
                     and (i.page, i.kind) not in in_text)


def _verify(ws: Workspace, stem: str, matcher: ProfileMatcher, vault: Vault, overrides: Overrides,
            known: list[Known], ocr: bool, result: Result | None, progress: Progress = NONE) -> Report:
    """Everything this document's redaction (`result`, None to check its last one) found, and
    every value in the vault that some document's redaction uses (another document's only
    when it is telling: see `strong`), except what the reviewer kept in the clear here: a
    whole placeholder, or single boxes."""
    allowed = allowed_values(vault, overrides)
    used = in_use(ws, (stem, result) if result else None)
    here = {r.placeholder for r in result.regions} if result else placeholders_of(ws, stem)
    known = known + [Known(e["kind"], v) for ph in used & vault.entries.keys()
                     for e in [vault.entries[ph]] for v in e["values"]
                     if ph in here or strong(e["kind"], v)]  # another document's: only the telling ones
    shown = [Shown(n, r, vault.entries.get(ph, {}).get("values", [])) for n, r, ph in overrides.show]
    out = ws.outputs(stem)
    return verify(out["pdf"], matcher, [k for k in known if k.value not in allowed], out["txt"], ocr=ocr,
                  allowed=allowed, shown=shown, progress=progress)


def reverify(ws: Workspace, stem: str, matcher: ProfileMatcher, vault: Vault, ocr: bool = True,
             progress: Progress = NONE) -> Report:
    """Verify a document's current output again, without redacting it again."""
    for old in ws.stale(stem):
        old.unlink(missing_ok=True)
    overrides = Overrides.load(ws.overrides_path(stem))
    report = _verify(ws, stem, matcher, vault, overrides, [], ocr, None, progress)
    ws.outputs(stem)["report"].write_text(json.dumps(report.to_json(), indent=2) + "\n")
    return report


def export(ws: Workspace, stem: str) -> list[Path]:
    """Copy a document's redacted PDF to ready-to-upload/, only if its last verification passed."""
    out = ws.outputs(stem)
    if not out["report"].exists() or not json.loads(out["report"].read_text()).get("passed"):
        return []
    for old in ws.stale(stem):  # a .txt (exports used to include it), or the file under its old name
        old.unlink(missing_ok=True)
    released = []
    for built, dest in zip((out["pdf"],), ws.released(stem), strict=True):
        shutil.copyfile(built, dest)
        released.append(dest)
    return released


def run(ws: Workspace, ocr: bool = True) -> list[Outcome]:
    if not ws.profile_path.exists():
        raise FileNotFoundError(f"no profile at {ws.profile_path}; create it first (see README)")
    matcher = ProfileMatcher(Profile.load(ws.profile_path))
    vault = Vault(ws.vault_dir / "placeholders.json")
    return [process(src, ws, matcher, vault, ocr) for src in ws.pending()]
