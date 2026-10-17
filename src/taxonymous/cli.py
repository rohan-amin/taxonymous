"""Command line: taxonymous redact FILE... --profile PROFILE.json --out DIR --vault DIR"""

import argparse
import sys
from pathlib import Path

from .detect import ProfileMatcher
from .profile import Profile
from .redact import redact_pdf
from .vault import Vault


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taxonymous")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("redact", help="redact text-layer PDFs")
    r.add_argument("files", nargs="+", type=Path)
    r.add_argument("--profile", type=Path, required=True, help="your identifiers (JSON)")
    r.add_argument("--out", type=Path, default=Path("work"), help="output folder")
    r.add_argument("--vault", type=Path, default=Path("vault"), help="placeholder map folder")
    args = ap.parse_args(argv)

    matcher = ProfileMatcher(Profile.load(args.profile))
    vault = Vault(args.vault / "placeholders.json")
    for f in args.files:
        res = redact_pdf(f, args.out, matcher, vault)
        summary = ", ".join(f"{k} {n}" for k, n in sorted(res.counts().items()))
        print(f"{f.name}: {len(res.findings)} redactions ({summary}) -> {res.pdf}")
    print("Not verified yet: review the output before uploading anything.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
