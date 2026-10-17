"""Synthetic documents that have no IRS template: brokerage consolidated
statements, IRA statements, DAF letters, receipts, a K-1's prose statement,
a pay stub, a property tax bill and a childcare provider's statement."""

from pathlib import Path
from random import Random

import pymupdf

from .fake import CHARITIES, FAKE_SSNS, OUTSIDERS, Household, address, amount, cusip
from .manifest import DocRecord

LETTER = pymupdf.paper_rect("letter")
TAX_YEAR = 2025

SECURITIES = ["Northwind Total Market Index Fd", "Contoso Intl Equity ETF", "Fabrikam Muni Bond Fund",
              "Tailspin Dividend Growth ETF", "Adatum Short-Term Treasury", "Litware Small Cap Value",
              "Proseware Aggregate Bond ETF", "Wingtip Real Estate Index", "Woodgrove Growth Fund Cl I",
              "Lucerne Emerging Mkts ETF"]


class Pen:
    """Minimal top-to-bottom text writer for one page."""

    def __init__(self, page: pymupdf.Page, x: float = 54, y: float = 60):
        self.page, self.x, self.y = page, x, y

    def line(self, text: str, size: float = 9, bold: bool = False, x: float | None = None,
             gap: float = 1.35) -> None:
        self.page.insert_text((self.x if x is None else x, self.y), text, fontsize=size,
                              fontname="hebo" if bold else "helv")
        self.y += size * gap

    def at(self, x: float, text: str, size: float = 9, bold: bool = False, right: bool = False) -> None:
        if right:
            x -= pymupdf.get_text_length(text, fontname="hebo" if bold else "helv", fontsize=size)
        self.page.insert_text((x, self.y), text, fontsize=size, fontname="hebo" if bold else "helv")

    def skip(self, h: float = 8) -> None:
        self.y += h

    def para(self, text: str, size: float = 10, width: float | None = None) -> None:
        """A wrapped paragraph; names and numbers may break across lines, as in real letters."""
        width = width or LETTER.width - 2 * self.x
        line = ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if line and pymupdf.get_text_length(trial, fontname="helv", fontsize=size) > width:
                self.line(line, size, gap=1.45)
                trial = word
            line = trial
        if line:
            self.line(line, size, gap=1.45)
        self.skip(6)

    def rule(self) -> None:
        self.page.draw_line((self.x, self.y - 6), (LETTER.width - 54, self.y - 6), width=0.5)


def _save(doc: pymupdf.Document, out_dir: Path, rec: DocRecord) -> DocRecord:
    doc.set_metadata({})
    doc.save(out_dir / rec.file, garbage=4, deflate=True, no_new_id=True)
    return rec


def brokerage(out_dir: Path, hh: Household, seed: int, pages: int = 30) -> DocRecord:
    rng = Random(f"{seed}:brokerage")
    rec = DocRecord(file="brokerage_consolidated.pdf", source="synthetic:brokerage", kind="text")
    tp, broker = hh.taxpayer, hh.payers["broker"]
    acct = hh.accounts["brokerage"]
    masked = f"XXXX-{acct[-4:]}"
    rec.add_keep(broker.name, "ORG_PUBLIC")
    rec.add_redact(acct, "ACCOUNT")
    rec.add_redact(masked, "ACCOUNT_MASKED")
    rec.add_redact(tp.name.upper(), "NAME")
    secs = [(name, rec.add_keep(cusip(rng), "CUSIP")) for name in SECURITIES]

    doc = pymupdf.open()
    rows_total, proceeds_total = 0, 0.0
    for pno in range(pages):
        page = doc.new_page(width=LETTER.width, height=LETTER.height)
        pen = Pen(page, y=40)
        pen.at(54, broker.name, 10, bold=True)
        pen.at(LETTER.width - 54, f"Account {masked}", 8, right=True)
        pen.skip(11)
        pen.at(LETTER.width - 54, tp.name.upper(), 8, right=True)
        pen.line(f"{TAX_YEAR} Consolidated Form 1099", 8)
        page.insert_text((54, LETTER.height - 30), f"Page {pno + 1} of {pages}", fontsize=7)
        page.insert_text((LETTER.width - 200, LETTER.height - 30),
                         f"Account {masked}  |  Tax Year {TAX_YEAR}", fontsize=7)
        pen.skip(14)
        pen.rule()
        if pno == 0:
            pen.line("RECIPIENT", 7, bold=True)
            pen.line(rec.add_redact(tp.name.upper(), "NAME"), 10)
            for line in hh.address.lines():
                pen.line(rec.add_redact(line.upper(), "ADDRESS"), 10)
            pen.skip()
            pen.line(f"Account Number: {acct}")
            pen.line(f"Recipient's TIN: {rec.add_redact('***-**-' + tp.ssn[-4:], 'SSN_MASKED')}")
            pen.line(f"Electronic delivery: {rec.add_redact(hh.email, 'EMAIL')}")
            pen.skip(12)
            for title, boxes in [("1099-DIV Dividends and Distributions",
                                  ["1a Total ordinary dividends", "1b Qualified dividends",
                                   "2a Total capital gain distributions", "7 Foreign tax paid"]),
                                 ("1099-INT Interest Income",
                                  ["1 Interest income",
                                   "3 Interest on U.S. Savings Bonds and Treasury obligations",
                                   "8 Tax-exempt interest"])]:
                pen.line(title, 11, bold=True)
                for box in boxes:
                    pen.at(LETTER.width - 54, rec.add_keep(amount(rng, 10, 20_000), "AMOUNT"), right=True)
                    pen.line(box)
                pen.skip(10)
            continue
        pen.line("1099-B Proceeds From Broker and Barter Exchange Transactions (Short-term, covered)",
                 9, bold=True)
        cols = [54, 250, 320, 380, 450, 520]
        headers = ["Description", "CUSIP", "Acquired", "Sold", "Proceeds", "Cost basis"]
        for x, h in zip(cols, headers, strict=True):
            pen.at(x if x < 400 else x + 40, h, 7, bold=True, right=x >= 400)
        pen.skip(12)
        while pen.y < LETTER.height - 60:
            name, cu = rng.choice(secs)
            qty = rng.randrange(1, 400)
            proceeds = rec.add_keep(amount(rng, 100, 40_000), "AMOUNT")
            basis = rec.add_keep(amount(rng, 100, 40_000), "AMOUNT")
            pen.at(54, f"{qty} sh {name}", 7)
            pen.at(250, cu, 7)
            pen.at(320, f"{rng.randrange(1, 13):02d}/{rng.randrange(1, 29):02d}/{TAX_YEAR - 1}", 7)
            pen.at(380, f"{rng.randrange(1, 13):02d}/{rng.randrange(1, 29):02d}/{TAX_YEAR}", 7)
            pen.at(490, proceeds, 7, right=True)
            pen.at(558, basis, 7, right=True)
            pen.skip(10)
            rows_total += 1
            proceeds_total += float(proceeds.replace(",", ""))
    rec.stats = {"pages": pages, "transaction_rows": rows_total,
                 "total_proceeds": round(proceeds_total, 2)}
    return _save(doc, out_dir, rec)


def ira_statement(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    rng = Random(f"{seed}:ira")
    rec = DocRecord(file="ira_statement.pdf", source="synthetic:ira", kind="text")
    tp, cust = hh.taxpayer, hh.payers["custodian"]
    acct = hh.accounts["ira"]
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page)
    pen.line(rec.add_keep(cust.name, "ORG_PUBLIC"), 14, bold=True)
    pen.line(f"Traditional IRA Year-End Statement  -  December 31, {TAX_YEAR}", 10)
    pen.skip(10)
    pen.line(rec.add_redact(tp.name, "NAME"), 10)
    for line in hh.address.lines():
        pen.line(rec.add_redact(line, "ADDRESS"), 10)
    pen.skip(6)
    pen.line(f"Account number: {rec.add_redact(acct, 'ACCOUNT')}")
    pen.line(f"Date of birth: {rec.add_redact(tp.dob, 'DOB')}")
    pen.line(f"Phone on file: {rec.add_redact(hh.phone, 'PHONE')}")
    pen.skip(12)
    pen.line("Form 5498 Information", 11, bold=True)
    for box in ["1 IRA contributions", "2 Rollover contributions", "5 Fair market value of account",
                "10 Roth IRA conversion amount"]:
        pen.at(LETTER.width - 54, rec.add_keep(amount(rng, 500, 300_000), "AMOUNT"), right=True)
        pen.line(box)
    pen.skip(12)
    pen.line("Beneficiary Designations", 11, bold=True)
    pen.at(54, "Name", 8, bold=True)
    pen.at(250, "Relationship", 8, bold=True)
    pen.at(360, "Type", 8, bold=True)
    pen.at(430, "Share", 8, bold=True)
    pen.at(480, "Date of birth", 8, bold=True)
    pen.skip(12)
    rows = [(hh.spouse, "Spouse", "Primary", "100%"),
            (hh.beneficiaries[0], "Sibling", "Contingent", "50%"),
            (hh.beneficiaries[1], "Niece/Nephew", "Contingent", "50%")]
    for person, rel, kind, share in rows:
        pen.at(54, rec.add_redact(person.name, "NAME", person.third_party))
        pen.at(250, rel)
        pen.at(360, kind)
        pen.at(430, share)
        pen.at(480, rec.add_redact(person.dob, "DOB", person.third_party))
        pen.skip(12)
    return _save(doc, out_dir, rec)


def daf_letter(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    rng = Random(f"{seed}:daf")
    rec = DocRecord(file="daf_acknowledgment.pdf", source="synthetic:daf", kind="text")
    tp, sp, daf = hh.taxpayer, hh.spouse, hh.payers["daf"]
    fund_name = rec.add_redact(f"{tp.last} Family Giving Fund", "ORG_PRIVATE")
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page, x=72, y=72)
    pen.line(rec.add_keep(daf.name, "ORG_PUBLIC"), 14, bold=True)
    for line in daf.address.lines():
        pen.line(line, 9)
    pen.skip(18)
    pen.line(f"January 22, {TAX_YEAR + 1}", 10)
    pen.skip(10)
    pen.line(f"{rec.add_redact(tp.short_name, 'NAME')} and {rec.add_redact(sp.short_name, 'NAME')}", 10)
    for line in hh.address.lines():
        pen.line(rec.add_redact(line, "ADDRESS"), 10)
    pen.skip(10)
    fund_no = rec.add_redact(hh.accounts["daf"], "ACCOUNT")
    pen.line(f"Re: {fund_name}, Fund No. {fund_no}", 10, bold=True)
    pen.skip(8)
    first_names = [rec.add_redact(p.first, "NAME_FIRST") for p in (tp, sp)]
    pen.line(f"Dear {first_names[0]} and {first_names[1]},", 10)
    pen.skip(4)
    contrib = rec.add_keep(amount(rng, 5_000, 100_000), "AMOUNT")
    body = [
        f"Thank you for your irrevocable contribution of ${contrib} to the {fund_name},",
        f"received on March 3, {TAX_YEAR}. {daf.name} has exclusive legal control over the",
        "contributed assets. No goods or services were provided in exchange for this contribution.",
        "",
        f"During {TAX_YEAR}, the following grants were recommended from your fund and approved:",
    ]
    for text in body:
        pen.line(text, 10, gap=1.5)
    pen.skip(4)
    for charity in rng.sample(CHARITIES, 4):
        pen.at(90, rec.add_keep(charity, "ORG_PUBLIC"), 10)
        pen.at(LETTER.width - 90, "$" + rec.add_keep(amount(rng, 250, 10_000), "AMOUNT"), 10, right=True)
        pen.skip(15)
    pen.skip(10)
    pen.line(f"Please contact us at {daf.phone} with any questions.", 10)
    pen.skip(20)
    pen.line("Sincerely,", 10)
    pen.skip(14)
    pen.line("Grants Administration", 10)
    return _save(doc, out_dir, rec)


def receipt(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    rng = Random(f"{seed}:receipt")
    rec = DocRecord(file="receipt.pdf", source="synthetic:receipt", kind="text")
    shop = hh.payers["merchant"]
    doc = pymupdf.open()
    page = doc.new_page(width=226, height=340)  # 80 mm till roll
    pen = Pen(page, x=14, y=30)
    pen.line(rec.add_keep(shop.name, "ORG_PUBLIC"), 12, bold=True)
    for line in shop.address.lines():
        pen.line(line, 8)
    pen.line(shop.phone, 8)
    pen.skip(6)
    pen.line(f"04/12/{TAX_YEAR}  14:{rng.randrange(10, 59)}   Reg 03   Trans {rng.randrange(1000, 9999)}", 7)
    pen.skip(6)
    subtotal = 0.0
    for item in ["Cordless Drill 20V", "Wood Screws 2in 100ct", "Paint Roller Kit", "Drop Cloth 9x12",
                 "LED Bulb 4pk"]:
        price = rec.add_keep(amount(rng, 3, 180), "AMOUNT")
        subtotal += float(price.replace(",", ""))
        pen.at(14, item, 8)
        pen.at(212, price, 8, right=True)
        pen.skip(11)
    tax = round(subtotal * 0.055, 2)
    pen.skip(4)
    for label, val in [("SUBTOTAL", subtotal), ("TAX 5.5%", tax), ("TOTAL", subtotal + tax)]:
        pen.at(14, label, 8, bold=label == "TOTAL")
        pen.at(212, rec.add_keep(f"{val:,.2f}", "AMOUNT"), 8, right=True, bold=label == "TOTAL")
        pen.skip(11)
    pen.skip(6)
    card = rec.add_redact("*" * (len(hh.card) - 4) + hh.card[-4:], "CARD_MASKED")
    brand = {"4": "VISA", "5": "MASTERCARD", "3": "AMEX"}[hh.card[0]]
    pen.line(f"{brand} {card}", 8)
    pen.line(f"AUTH {rng.randrange(100000, 999999)}", 8)
    pen.skip(6)
    pen.line(f"Rewards member: {rec.add_redact(hh.taxpayer.short_name, 'NAME')}", 8)
    pen.line(f"Member # {rec.add_redact(hh.accounts['loyalty'], 'ACCOUNT')}", 8)
    pen.line(f"Receipt sent to {rec.add_redact(hh.email, 'EMAIL')}", 7)
    pen.skip(8)
    pen.line("THANK YOU FOR SHOPPING", 8, bold=True)
    return _save(doc, out_dir, rec)


def k1_statement(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    """A K-1's attached statement: mostly prose, naming people who are not in the profile
    (the general partner, an executor, the preparer), an outsider's SSN in a sentence,
    and an account number that is labelled on page 1 but appears bare on page 2."""
    rng = Random(f"{seed}:k1_statement")
    rec = DocRecord(file="k1_statement.pdf", source="synthetic:k1_statement", kind="text")
    tp, firm = hh.taxpayer, hh.partnership
    gp, deceased, executor, preparer = [(f, last) for f, last in rng.sample(OUTSIDERS, 4)]
    used = {p.ssn for p in hh.people()}
    executor_ssn = next(s for s in FAKE_SSNS if s not in used)
    capital = f"{rng.randrange(1000, 9999)}-{rng.randrange(1000, 9999)}-{rng.randrange(10, 99)}"
    phone = f"(555) 555-01{rng.randrange(0, 100):02d}"
    email = f"{gp[0].lower()}.{gp[1].lower()}@example.com"

    def full(person: tuple[str, str]) -> str:
        return rec.add_redact(f"{person[0]} {person[1]}", "NAME", third_party=True)

    def amt(lo: float, hi: float) -> str:
        return "$" + rec.add_keep(amount(rng, lo, hi), "AMOUNT")

    firm_name = rec.add_redact(firm.name, "ORG_PRIVATE")
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page, x=72, y=72)
    pen.line(firm_name, 13, bold=True)
    pen.line(f"EIN {rec.add_redact(firm.ein, 'EIN')}", 9)
    pen.skip(8)
    pen.line(f"Schedule K-1 (Form 1065), Tax Year {TAX_YEAR}: Statement A, Supplemental Information", 10,
             bold=True)
    pen.skip(6)
    pen.line(f"Partner: {rec.add_redact(tp.name, 'NAME')}", 10)
    pen.line(f"Partner's identifying number: {rec.add_redact('XXX-XX-' + tp.ssn[-4:], 'SSN_MASKED')}", 10)
    pen.line(f"Capital account no. {rec.add_redact(capital, 'ACCOUNT')}", 10)
    pen.skip(12)
    pen.para(f"During {TAX_YEAR} the Partnership was managed by {full(gp)}, its general partner. "
             f"Following the death of {full(deceased)} in June, that interest passed to the estate, "
             f"and {full(executor)} "
             f"(SSN {rec.add_redact(executor_ssn, 'SSN', third_party=True)}) was appointed executor of "
             f"the estate. Distributions to the estate are reported on a separate Schedule K-1.")
    pen.para(f"On September 12, {TAX_YEAR} the Partnership sold its interest in the warehouse it held for "
             f"rental for {amt(400_000, 2_000_000)}. Your share of the resulting gain, "
             f"{amt(5_000, 80_000)}, is reported in box 9c as unrecaptured section 1250 gain, "
             f"and {amt(1_000, 20_000)} of "
             f"depreciation recapture is included in box 11.")
    pen.para(f"Your share of qualified business income for section 199A is {amt(1_000, 60_000)}, and your "
             f"share of W-2 wages paid by the Partnership is {amt(1_000, 30_000)}. The unadjusted basis of "
             f"qualified property immediately after acquisition is {amt(10_000, 900_000)}.")
    gp_first = rec.add_redact(gp[0], "NAME_FIRST", third_party=True)
    pen.para(f"Questions about this statement may be directed to {gp_first} at "
             f"{rec.add_redact(phone, 'PHONE', third_party=True)} or "
             f"{rec.add_redact(email, 'EMAIL', third_party=True)}.")

    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page, x=72, y=72)
    pen.line("Statement A (continued)", 10, bold=True)
    pen.skip(8)
    gp_last = rec.add_redact(gp[1], "NAME_LAST", third_party=True)
    pen.para(f"As general partner, {gp_last} also signed the Partnership's Form 8308 for the exchange of a "
             f"partnership interest described above. Your capital account, {capital}, was credited with "
             f"{amt(2_000, 90_000)} of net income and charged with distributions of {amt(1_000, 50_000)}.")
    pen.para(f"The estate of the late {rec.add_redact(deceased[1], 'NAME_LAST', third_party=True)} "
             "is not a partner for the remainder of the year, and no portion of the gain above is allocated "
             "to it.")
    pen.skip(10)
    pen.line("Sincerely,", 10)
    pen.skip(16)
    pen.line(full(preparer), 10)
    pen.line("Partnership Representative", 10)
    return _save(doc, out_dir, rec)


def pay_stub(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    """An earnings statement from the employer's payroll provider."""
    rng = Random(f"{seed}:paystub")
    rec = DocRecord(file="pay_stub.pdf", source="synthetic:paystub", kind="text")
    tp, emp = hh.taxpayer, hh.employer
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height / 2)
    pen = Pen(page, y=40)
    pen.at(54, rec.add_redact(emp.name, "ORG_PRIVATE"), 11, bold=True)
    pen.at(LETTER.width - 54, "Earnings Statement", 11, bold=True, right=True)
    pen.skip(13)
    for line in emp.address.lines():
        pen.at(54, rec.add_redact(line, "ADDRESS"), 8)
        pen.skip(10)
    pen.skip(8)
    left, right = 54, 330
    pen.at(left, rec.add_redact(tp.name.upper(), "NAME"), 9, bold=True)
    pen.at(right, "Period ending:", 8)
    pen.at(420, f"12/15/{TAX_YEAR}", 8)
    pen.skip(11)
    pen.at(left, rec.add_redact(hh.address.street.upper(), "ADDRESS"), 8)
    pen.at(right, "Pay date:", 8)
    pen.at(420, f"12/19/{TAX_YEAR}", 8)
    pen.skip(11)
    pen.at(left, rec.add_redact(hh.address.city_line.upper(), "ADDRESS"), 8)
    pen.at(right, "Employee ID:", 8)
    pen.at(420, rec.add_redact(f"{rng.randrange(100000, 999999)}", "EMPLOYEE_ID"), 8)
    pen.skip(11)
    pen.at(right, "SSN:", 8)
    pen.at(420, rec.add_redact("XXX-XX-" + tp.ssn[-4:], "SSN_MASKED"), 8)
    pen.skip(18)

    cols = [(54, "Earnings"), (230, "Rate"), (290, "Hours"), (380, "This period"), (480, "Year to date")]
    for x, h in cols:
        pen.at(x + (60 if x >= 380 else 0), h, 7, bold=True, right=x >= 380)
    pen.skip(11)
    rate = round(rng.uniform(40, 90), 4)
    for item, hours in [("Regular", 80.0), ("Overtime", round(rng.uniform(1, 12), 2)), ("Holiday", 8.0)]:
        pen.at(54, item, 8)
        pen.at(230, f"{rate:.4f}", 8)
        pen.at(290, f"{hours:.2f}", 8)
        pen.at(440, rec.add_keep(f"{rate * hours:,.2f}", "AMOUNT"), 8, right=True)
        pen.at(540, rec.add_keep(amount(rng, 5_000, 120_000), "AMOUNT"), 8, right=True)
        pen.skip(10)
    pen.skip(6)
    pen.at(54, "Deductions", 7, bold=True)
    pen.skip(11)
    for item in ["Federal Income Tax", "Social Security Tax", "Medicare Tax", f"{hh.address.state} State Tax",
                 "401(k)", "Medical Pre-Tax", "Dental Pre-Tax"]:
        pen.at(54, item, 8)
        pen.at(440, "-" + rec.add_keep(amount(rng, 20, 1_500), "AMOUNT"), 8, right=True)
        pen.at(540, rec.add_keep(amount(rng, 500, 30_000), "AMOUNT"), 8, right=True)
        pen.skip(10)
    pen.skip(8)
    pen.at(54, "Net pay", 9, bold=True)
    pen.at(440, rec.add_keep(amount(rng, 2_000, 8_000), "AMOUNT"), 9, bold=True, right=True)
    pen.skip(16)
    bank = hh.accounts["bank"]
    pen.line(f"Deposited to checking account {rec.add_redact('xxxxxx' + bank[-4:], 'ACCOUNT_MASKED')}"
             f"  Advice no. {rng.randrange(10_000_000, 99_999_999)}", 8)
    return _save(doc, out_dir, rec)


def property_tax_bill(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    """A county's real property tax statement with its payment coupon."""
    rng = Random(f"{seed}:property_tax")
    rec = DocRecord(file="property_tax.pdf", source="synthetic:property_tax", kind="text")
    tp, sp, home = hh.taxpayer, hh.spouse, hh.address
    owners = f"{tp.last.upper()} {tp.first.upper()} {tp.middle} & {sp.first.upper()} {sp.middle}"
    parcel = f"{rng.randrange(1000, 9999)}-{rng.randrange(10, 99)}-{rng.randrange(1000, 9999)}"
    tax_acct = f"{rng.randrange(10**8, 10**9)}"
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page, y=50)
    pen.line(f"{home.city.upper()} COUNTY TREASURER", 12, bold=True)
    pen.line(f"{TAX_YEAR} Real Property Tax Statement", 10)
    pen.skip(14)
    pen.at(54, "Parcel Number", 7, bold=True)
    pen.at(220, "Tax Account", 7, bold=True)
    pen.at(380, "Statement Date", 7, bold=True)
    pen.skip(10)
    pen.at(54, rec.add_redact(parcel, "PARCEL"), 9)
    pen.at(220, rec.add_redact(tax_acct, "ACCOUNT"), 9)
    pen.at(380, f"02/14/{TAX_YEAR}", 9)
    pen.skip(20)
    pen.line("Taxpayer", 7, bold=True)
    pen.line(rec.add_redact(owners, "NAME"), 9)
    pen.line(rec.add_redact(home.street.upper(), "ADDRESS"), 9)
    pen.line(rec.add_redact(home.city_line.upper(), "ADDRESS"), 9)
    pen.skip(10)
    pen.line(f"Site address: {rec.add_redact(home.street.upper(), 'ADDRESS')}", 8)
    pen.line("Legal description: LOT 14 BLK 3 ORCHARD HEIGHTS ADD", 8)
    pen.skip(14)
    pen.line("Assessed Value", 9, bold=True)
    land, improvements = amount(rng, 80_000, 300_000), amount(rng, 150_000, 700_000)
    for label, value in [("Land", land), ("Improvements", improvements)]:
        pen.at(54, label, 8)
        pen.at(300, rec.add_keep(value, "AMOUNT"), 8, right=True)
        pen.skip(11)
    pen.skip(8)
    pen.line("Taxing District", 9, bold=True)
    total = 0.0
    for district in ["State School Levy", "County General", "City of " + home.city, "School District 10",
                     "Port District", "Fire District 6", "Library District"]:
        levy = amount(rng, 80, 2_500)
        total += float(levy.replace(",", ""))
        pen.at(54, district, 8)
        pen.at(300, rec.add_keep(levy, "AMOUNT"), 8, right=True)
        pen.skip(11)
    pen.at(54, "Total tax due", 8, bold=True)
    pen.at(300, rec.add_keep(f"{total:,.2f}", "AMOUNT"), 8, bold=True, right=True)
    pen.skip(11)
    half = f"{total / 2:,.2f}"
    pen.at(54, "First half due 04/30", 8)
    pen.at(300, rec.add_keep(half, "AMOUNT"), 8, right=True)
    pen.skip(28)
    page.draw_line((54, pen.y - 10), (LETTER.width - 54, pen.y - 10), dashes="[3] 0", width=0.5)
    pen.line("RETURN THIS PORTION WITH YOUR PAYMENT", 8, bold=True)
    pen.skip(4)
    pen.at(54, f"Parcel {parcel}", 9)
    pen.at(380, f"First half  ${half}", 9)
    pen.skip(12)
    pen.at(54, owners, 9)
    pen.skip(12)
    pen.at(54, f"{tp.first} {tp.last}, {home.street}", 8)
    return _save(doc, out_dir, rec)


def childcare_statement(out_dir: Path, hh: Household, seed: int) -> DocRecord:
    """A year-end statement from a home daycare run by a person (so the provider's own SSN
    is the taxpayer ID on it), for the parents' dependent care credit."""
    rng = Random(f"{seed}:childcare")
    rec = DocRecord(file="childcare.pdf", source="synthetic:childcare", kind="text")
    tp, sp, child = hh.taxpayer, hh.spouse, hh.dependent
    used = {p.ssn for p in hh.people()}
    provider_ssn = next(s for s in reversed(FAKE_SSNS) if s not in used)
    first, last = rng.choice(OUTSIDERS[::-1])
    provider = f"{first} {last}"
    where = address(rng)
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER.width, height=LETTER.height)
    pen = Pen(page, x=72, y=72)
    pen.line(f"{rec.add_redact(last, 'NAME_LAST', third_party=True)}'s Little Sprouts Home Daycare", 13,
             bold=True)
    pen.line(rec.add_redact(where.street, "ADDRESS", third_party=True), 9)
    pen.line(rec.add_redact(where.city_line, "ADDRESS", third_party=True), 9)
    pen.skip(16)
    pen.line(f"{TAX_YEAR} Year-End Child Care Statement", 11, bold=True)
    pen.skip(6)
    pen.line(f"Provider: {rec.add_redact(provider, 'NAME', third_party=True)}", 10)
    pen.line(f"Provider taxpayer ID (SSN): {rec.add_redact(provider_ssn, 'SSN', third_party=True)}", 10)
    pen.skip(6)
    pen.line(f"Parents: {rec.add_redact(tp.short_name, 'NAME')} and {rec.add_redact(sp.short_name, 'NAME')}",
             10)
    pen.line(f"Child: {rec.add_redact(child.short_name, 'NAME')}   Date of birth: "
             f"{rec.add_redact(child.dob, 'DOB')}", 10)
    pen.skip(14)
    pen.at(72, "Month", 9, bold=True)
    pen.at(300, "Days", 9, bold=True)
    pen.at(470, "Paid", 9, bold=True, right=True)
    pen.skip(13)
    total = 0.0
    for month in ["January", "February", "March", "April", "May", "June", "July", "August",
                  "September", "October", "November", "December"]:
        paid = amount(rng, 600, 1_400)
        total += float(paid.replace(",", ""))
        pen.at(72, month, 9)
        pen.at(300, str(rng.randrange(15, 23)), 9)
        pen.at(470, rec.add_keep(paid, "AMOUNT"), 9, right=True)
        pen.skip(12)
    pen.at(72, "Total paid", 9, bold=True)
    pen.at(470, rec.add_keep(f"{total:,.2f}", "AMOUNT"), 9, bold=True, right=True)
    pen.skip(24)
    pen.para(f"I certify that I received the payments above for the care of {child.first} during {TAX_YEAR}. "
             f"Please keep this statement for your records; you will need my taxpayer ID to claim the "
             f"credit for child and dependent care expenses.", 10)
    pen.skip(12)
    pen.line("Thank you,", 10)
    pen.skip(6)
    pen.line(first, 10)
    rec.add_redact(first, "NAME_FIRST", third_party=True)
    return _save(doc, out_dir, rec)


def build_all(out_dir: Path, hh: Household, seed: int, brokerage_pages: int = 30) -> list[DocRecord]:
    return [brokerage(out_dir, hh, seed, brokerage_pages), ira_statement(out_dir, hh, seed),
            daf_letter(out_dir, hh, seed), receipt(out_dir, hh, seed), k1_statement(out_dir, hh, seed),
            pay_stub(out_dir, hh, seed), property_tax_bill(out_dir, hh, seed),
            childcare_statement(out_dir, hh, seed)]
