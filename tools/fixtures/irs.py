"""Fill blank IRS fillable PDFs with fake data.

The IRS PDFs have no useful field names or tooltips, so each field is
classified from the label printed above (or below) it on the page, e.g.
"RECIPIENT'S TIN" or "2 Federal income tax withheld". Unrecognized fields
get a dollar amount.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from random import Random

import pymupdf

from .fake import Household, Org, Person, amount, cusip
from .manifest import DocRecord

BLANK_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "irs_blank"

ISSUER_RE = re.compile(r"\b(employer|payer|partnership|corporation|trust|estate|filer|trustee|issuer|lender)")
SUBJECT_RE = re.compile(r"\b(employee|recipient|partner|shareholder|beneficiary|participant|borrower)\b")


@dataclass
class Parties:
    subject: Person  # who the form is about (employee, recipient, partner, ...)
    issuer: Org | Person  # who issued it
    issuer_tin: str


def _label(words: list, rect: pymupdf.Rect, others: list[pymupdf.Rect]) -> str:
    """Text of the label printed nearest above the field (or inside/below it)."""
    def overlapping(ws: list) -> list:
        return [w for w in ws if w[0] < rect.x1 and w[2] > rect.x0]

    # a label above another field belongs to that field, not this one
    ceiling = max([o.y1 for o in others if o.x0 < rect.x1 and o.x1 > rect.x0
                   and o.y1 <= rect.y0 + 2 and o != rect] + [rect.y0 - 36])
    above = overlapping([w for w in words if w[3] <= rect.y0 + 2 and w[1] >= ceiling - 1])
    if above:
        # keep the block of lines closest to the field; a gap > 12pt starts another box
        above.sort(key=lambda w: -w[3])
        block, edge = [], above[0][1]
        for w in above:
            if w[3] < edge - 12:
                break
            block.append(w)
            edge = min(edge, w[1])
        hits = block
    else:
        hits = overlapping([w for w in words if rect.y0 - 2 <= w[1] <= rect.y1 + 12])
    hits.sort(key=lambda w: (round(w[1]), w[0]))
    return " ".join(w[4] for w in hits).replace("\u2019", "'").lower()


def _classify(label: str, rect: pymupdf.Rect, maxlen: int) -> tuple[str, str]:
    """Return (field type, role) where role is 'issuer' or 'subject'."""
    iss, sub = ISSUER_RE.search(label), SUBJECT_RE.search(label)
    role = "issuer" if iss and (not sub or iss.start() < sub.start()) else "subject"
    if "state identification" in label or "state id" in label or "state no" in label:
        return "STATE_ID", role
    if "control number" in label:
        return "CONTROL", role
    if "account number" in label:
        return "ACCOUNT", role
    if re.search(r"\brtn\b", label):
        return "ROUTING", role
    if "cusip" in label:
        return "CUSIP", role
    if "first name" in label:
        return "FIRST", role
    if "last name" in label:
        return "LAST", role
    if "suff" in label or "locality" in label:
        return "SKIP", role
    if ("social security number" in label or re.search(r"\b(ssn|tin|ein)\b", label)
            or "identification number" in label or "identifying number" in label):
        return "TIN", role
    if "name" in label and ("address" in label or rect.height > 30):
        return "NAME_ADDR", role
    if "name" in label:
        return "NAME", role
    if "street address" in label:
        return "STREET", role
    if "city or town" in label:
        return "CITY", role
    if "address" in label:
        return "ADDRESS", role
    if maxlen in (9, 10, 11):
        return "TIN", role
    if 0 < maxlen <= 4 or rect.width < 35 or "foreign" in label or "state" in label:
        return "SKIP", role
    return "AMOUNT", role


def _party_lines(rec: DocRecord, who: Org | Person, with_name: bool, with_phone: bool) -> list[str]:
    if isinstance(who, Org):
        lines = [who.name] if with_name else []
        if who.public:
            rec.add_keep(who.name, "ORG_PUBLIC")
            lines += who.address.lines()
            if with_phone:
                lines.append(who.phone)
        else:
            rec.add_redact(who.name, "ORG_PRIVATE")
            lines += [rec.add_redact(x, "ADDRESS") for x in who.address.lines()]
            if with_phone:
                lines.append(rec.add_redact(who.phone, "PHONE"))
        return lines
    tp = who.third_party
    lines = [rec.add_redact(who.name, "NAME", tp)] if with_name else []
    lines += [rec.add_redact(x, "ADDRESS", tp) for x in who.address.lines()]
    return lines


def _name(rec: DocRecord, who: Org | Person) -> str:
    if isinstance(who, Org):
        return rec.add_keep(who.name, "ORG_PUBLIC") if who.public else rec.add_redact(who.name, "ORG_PRIVATE")
    return rec.add_redact(who.name, "NAME", who.third_party)


def fill(form: str, out_dir: Path, name: str, hh: Household, parties: Parties, seed: int,
         flatten: bool = True) -> DocRecord:
    rec = DocRecord(file=f"{name}.pdf", source=f"irs:{form}", kind="text")
    doc = pymupdf.open(BLANK_DIR / f"{form}.pdf")
    amounts: dict[str, str] = {}
    for page in doc:
        words = page.get_text("words")
        widgets = list(page.widgets())
        rects = [w.rect for w in widgets]
        for idx, w in enumerate(widgets):
            if w.field_type_string != "Text":
                continue
            label = _label(words, w.rect, rects)
            ftype, role = _classify(label, w.rect, w.text_maxlen)
            who = parties.issuer if role == "issuer" else parties.subject
            tp = isinstance(who, Person) and who.third_party
            value = ""
            if ftype == "TIN":
                if role == "issuer":
                    value = rec.add_redact(parties.issuer_tin, "EIN")
                else:
                    value = rec.add_redact(parties.subject.ssn, "SSN", tp)
            elif ftype == "STATE_ID":
                value = rec.add_redact(f"{hh.address.state}-{parties.issuer_tin[3:]}-1", "STATE_ID")
            elif ftype == "CONTROL":
                value = rec.add_redact(f"C{parties.issuer_tin[3:]}{seed % 97:02d}", "CONTROL_NUMBER")
            elif ftype == "ACCOUNT":
                value = rec.add_redact(hh.accounts["brokerage" if "b" in form else "bank"], "ACCOUNT")
            elif ftype == "ROUTING":
                value = rec.add_redact(hh.routing, "ROUTING")
            elif ftype == "CUSIP":
                value = rec.add_keep(cusip(Random(f"{seed}:{name}:cusip")), "CUSIP")
            elif ftype == "FIRST" and isinstance(who, Person):
                value = rec.add_redact(f"{who.first} {who.middle}", "NAME", tp)
                rec.add_redact(who.name, "NAME", tp)
            elif ftype == "LAST" and isinstance(who, Person):
                value = rec.add_redact(who.last, "NAME", tp)
            elif ftype == "NAME_ADDR":
                value = "\n".join(_party_lines(rec, who, True, "teleph" in label))
            elif ftype == "NAME":
                value = _name(rec, who)
            elif ftype == "STREET":
                value = rec.add_redact(who.address.street, "ADDRESS", tp)
            elif ftype == "CITY":
                value = rec.add_redact(who.address.city_line, "ADDRESS", tp)
            elif ftype == "ADDRESS":
                value = "\n".join(_party_lines(rec, who, False, False))
            elif ftype == "AMOUNT":
                key = label or f"#{idx}"
                if key not in amounts:
                    amounts[key] = amount(Random(f"{seed}:{name}:{key}"), 5, 25_000)
                value = rec.add_keep(amounts[key], "AMOUNT")
            if not value:
                continue
            if w.text_maxlen and len(value) > w.text_maxlen:
                value = value.replace("-", "")[: w.text_maxlen]
            w.field_value = value
            n_lines = value.count("\n") + 1
            w.text_fontsize = min(8, w.rect.height / (n_lines * 1.3))
            w.update()
    doc.set_metadata({})
    if flatten:
        doc.bake()
    doc.save(out_dir / rec.file, garbage=4, deflate=True, no_new_id=True)
    return rec


def build_all(out_dir: Path, hh: Household, seed: int) -> list[DocRecord]:
    p = hh.payers
    tp = hh.taxpayer
    house = Person(tp.first, tp.middle, tp.last, tp.ssn, tp.dob, "taxpayer", hh.address)
    jobs = [
        ("fw2", "w2", Parties(tp, hh.employer, hh.employer.ein), True),
        ("fw2", "w2_fillable", Parties(tp, hh.employer, hh.employer.ein), False),
        ("fw2", "w2_household_employee", Parties(hh.employee, house, hh.household_ein), True),
        ("f1099int", "1099_int", Parties(tp, p["bank"], p["bank"].ein), True),
        ("f1099div", "1099_div", Parties(tp, p["broker"], p["broker"].ein), True),
        ("f1099b", "1099_b", Parties(tp, p["broker"], p["broker"].ein), True),
        ("f1099msc", "1099_misc", Parties(tp, p["consultancy"], p["consultancy"].ein), True),
        ("f1099nec", "1099_nec", Parties(hh.spouse, p["consultancy"], p["consultancy"].ein), True),
        ("f1099r", "1099_r", Parties(tp, p["custodian"], p["custodian"].ein), True),
        ("f1099g", "1099_g", Parties(tp, p["state"], p["state"].ein), True),
        ("f1065sk1", "k1_1065", Parties(tp, hh.partnership, hh.partnership.ein), True),
        ("f1120ssk", "k1_1120s", Parties(hh.spouse, hh.s_corp, hh.s_corp.ein), True),
        ("f1041sk1", "k1_1041", Parties(tp, hh.trust, hh.trust.ein), True),
        ("f1040sh", "schedule_h_draft", Parties(tp, house, hh.household_ein), True),
        ("f5498", "5498", Parties(tp, p["custodian"], p["custodian"].ein), True),
    ]
    return [fill(form, out_dir, name, hh, parties, seed, flatten)
            for form, name, parties, flatten in jobs]
