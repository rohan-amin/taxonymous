"""Held-out documents: types the detectors were never tuned on.

They are written once and not changed to make a test pass. If the engine leaks
on one of them, the fix goes into the generic detectors, never a special case,
the document moves to the regular fixtures (it has been tuned on now), and a
new held-out type replaces it. The pay stub, property tax bill and childcare
statement in synthetic.py each started here and failed their first run.
"""

from pathlib import Path

from . import irs
from .fake import Household
from .manifest import DocRecord


def form_1098(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    """Mortgage interest statement: the bank is the lender, the taxpayer the borrower."""
    lender = hh.payers["bank"]
    rec = irs.fill("f1098", out_dir, "heldout_1098", hh, irs.Parties(hh.taxpayer, lender, lender.ein), seed)
    rec.source = "heldout:" + rec.source
    return rec


def build_all(out_dir: Path, hh: Household, seed: int) -> list[DocRecord]:
    return [form_1098(out_dir, hh, seed)]
