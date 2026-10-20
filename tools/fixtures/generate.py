"""Generate the synthetic fixture corpus.

Writes every document plus:
  household.json  - the fake profile a user would enter (own household only)
  manifest.json   - per-document ground truth (what must be redacted / kept)

Usage: uv run python -m tools.fixtures.generate [--out DIR] [--seed N] [--brokerage-pages N]
"""

import argparse
import json
import sys
from pathlib import Path

from . import degrade, fake, heldout, irs, synthetic
from . import manifest as manifest_mod

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "generated"


def generate(out_dir: Path, seed: int = 7, brokerage_pages: int = 30) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    hh = fake.household(seed)
    records = irs.build_all(out_dir, hh, seed) + synthetic.build_all(out_dir, hh, seed, brokerage_pages)
    records += heldout.build_all(out_dir, hh, seed)
    by_file = {r.file: r for r in records}
    records += [
        degrade.scan(by_file["w2.pdf"], out_dir, out_dir, "w2_scan", [1], seed),
        degrade.scan(by_file["k1_1065.pdf"], out_dir, out_dir, "k1_1065_scan", [0], seed),
        degrade.scan(by_file["1099_int.pdf"], out_dir, out_dir, "1099_int_scan", [0], seed),
        degrade.scan(by_file["1099_r.pdf"], out_dir, out_dir, "1099_r_scan", [0], seed),
        degrade.scan(by_file["ira_statement.pdf"], out_dir, out_dir, "ira_statement_scan", [0], seed),
        degrade.scan(by_file["brokerage_consolidated.pdf"], out_dir, out_dir, "brokerage_scan", [0, 1], seed),
        degrade.scan(by_file["k1_statement.pdf"], out_dir, out_dir, "k1_statement_scan", [0, 1], seed),
        degrade.photo(by_file["receipt.pdf"], out_dir, out_dir, "receipt_photo", seed),
        degrade.photo(by_file["daf_acknowledgment.pdf"], out_dir, out_dir, "daf_photo", seed),
        degrade.scanner_ocr(by_file["1099_div.pdf"], out_dir, out_dir, "1099_div_scanner_ocr", seed),
        degrade.attach(by_file["daf_acknowledgment.pdf"], by_file["receipt.pdf"], out_dir, out_dir,
                       "daf_with_receipt"),
    ]
    manifest_mod.settle(records, hh.profile())
    manifest = {"seed": seed, "documents": [r.to_json() for r in records]}
    (out_dir / "household.json").write_text(json.dumps(hh.profile(), indent=2) + "\n")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--brokerage-pages", type=int, default=30)
    args = ap.parse_args()
    manifest = generate(args.out, args.seed, args.brokerage_pages)
    for d in manifest["documents"]:
        print(f"{d['file']:30} {d['kind']:6} redact={len(d['redact']):3} keep={len(d['keep'])}")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
