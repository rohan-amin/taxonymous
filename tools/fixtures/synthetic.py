"""Synthetic documents that have no IRS template: brokerage consolidated
statements, IRA statements, DAF letters, and receipts."""

from pathlib import Path
from random import Random

import pymupdf

from .fake import CHARITIES, Household, amount, cusip
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


def build_all(out_dir: Path, hh: Household, seed: int, brokerage_pages: int = 30) -> list[DocRecord]:
    return [brokerage(out_dir, hh, seed, brokerage_pages), ira_statement(out_dir, hh, seed),
            daf_letter(out_dir, hh, seed), receipt(out_dir, hh, seed)]
