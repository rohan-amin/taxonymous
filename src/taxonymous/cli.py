"""Command line.

  taxonymous init   --workspace DIR     create inbox/ work/ ready-to-upload/ vault/
  taxonymous run    --workspace DIR     redact + verify everything in inbox/
  taxonymous redact FILE... --profile P --out DIR --vault DIR     (no verification)
  taxonymous verify FILE --profile P [--vault DIR] [--no-ocr]
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .detect import ProfileMatcher
from .pipeline import Workspace, run
from .profile import Profile
from .redact import redact_pdf
from .vault import Vault
from .verify import Known, verify


def _workspace_arg(p: argparse.ArgumentParser) -> None:
    default = os.environ.get("TAXONYMOUS_WORKSPACE")
    p.add_argument("--workspace", type=Path, default=Path(default) if default else None,
                   required=default is None, help="workspace folder (or set TAXONYMOUS_WORKSPACE)")


def _summary(issues) -> str:
    return ", ".join(f"{check}:{kind} x{n}" for (check, kind), n in
                     Counter((i.check, i.kind) for i in issues).most_common())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taxonymous")
    sub = ap.add_subparsers(dest="command", required=True)

    _workspace_arg(sub.add_parser("init", help="create the workspace folders"))
    r = sub.add_parser("run", help="redact and verify every PDF in inbox/")
    _workspace_arg(r)
    r.add_argument("--no-ocr", action="store_true", help="skip OCR verification (not for real use)")

    rd = sub.add_parser("redact", help="redact text-layer PDFs without verifying")
    rd.add_argument("files", nargs="+", type=Path)
    rd.add_argument("--profile", type=Path, required=True, help="your identifiers (JSON)")
    rd.add_argument("--out", type=Path, default=Path("work"), help="output folder")
    rd.add_argument("--vault", type=Path, default=Path("vault"), help="placeholder map folder")

    vf = sub.add_parser("verify", help="check a redacted PDF for leaks")
    vf.add_argument("file", type=Path)
    vf.add_argument("--profile", type=Path, required=True)
    vf.add_argument("--vault", type=Path, help="placeholder map folder (adds every vaulted value)")
    vf.add_argument("--txt", type=Path, help="placeholder text file to check too")
    vf.add_argument("--no-ocr", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "init":
        ws = Workspace(args.workspace)
        ws.init()
        print(f"Workspace ready at {ws.root}. Put your identifiers in {ws.profile_path} "
              f"and your PDFs in {ws.inbox}.")
        return 0

    if args.command == "run":
        ws = Workspace(args.workspace)
        outcomes = run(ws, ocr=not args.no_ocr)
        for o in outcomes:
            status = "PASS -> ready-to-upload" if o.report.passed else f"FAIL ({_summary(o.report.issues)})"
            print(f"{o.source.name}: {status}")
        failed = [o for o in outcomes if not o.report.passed]
        if failed:
            print(f"{len(failed)} file(s) held back; details in {ws.work}/*.report.json", file=sys.stderr)
        if not outcomes:
            print(f"Nothing to do: {ws.inbox} has no PDFs.")
        return 1 if failed else 0

    matcher = ProfileMatcher(Profile.load(args.profile))
    if args.command == "redact":
        vault = Vault(args.vault / "placeholders.json")
        for f in args.files:
            res = redact_pdf(f, args.out, matcher, vault)
            summary = ", ".join(f"{k} {n}" for k, n in sorted(res.counts().items()))
            print(f"{f.name}: {len(res.findings)} redactions ({summary}) -> {res.pdf}")
        print("Not verified: run `taxonymous verify` or use `taxonymous run`.", file=sys.stderr)
        return 0

    known = []
    if args.vault:
        data = json.loads((args.vault / "placeholders.json").read_text())
        known = [Known(e["kind"], v) for e in data["placeholders"].values() for v in e["values"]]
    report = verify(args.file, matcher, known, args.txt, ocr=not args.no_ocr)
    print(f"{args.file.name}: {'PASS' if report.passed else 'FAIL (' + _summary(report.issues) + ')'}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
