"""A starter folder for an institution bringing its own files.

    python -m keel init "Riverbend FCU"            a credit union
    python -m keel init "First Community" --bank   a bank

writes, in the new folder:

    assumptions.xlsx        every setting, at Keel's starting values
    data/loans.csv          one row per loan
    data/certificates.csv   one row per certificate (or CD)
    data/shares.csv         non-maturity balances by product and balance tier
    data/investments.csv    one row per security
    data/borrowings.csv     one row per borrowing
    data/gl.csv             the trial balance on the analysis date
    data/product_map.xlsx   core product codes and ledger accounts -> Keel
    START-HERE.txt          what to replace, in order

Each data file has its columns and a few example rows, and the example rows
tie to the example ledger, so `python -m keel run <folder>` works on the
starter folder straight away. Replace the rows with the core system's export
one file at a time and run again: the reconciliation says which file no
longer ties, and by how much.
"""

import csv
import os

from keel import callreport, settings, xlsx
from keel.model import InputError

AS_OF = max(callreport.TREASURY)

LOANS = [
    # loan_id, product_code, rate_type, index, margin, reset_months, next_reset_date, rate_floor, rate_cap,
    # days_delinquent, origination_date, maturity_date, original_term_months, original_balance,
    # current_balance, rate, amortization_months, member_id, branch
    ["L1001", "AUTO", "F", "", "", "", "", "", "", 0, "2025-03-15", "2030-03-15", 60, 32000, 26450.12, 5.99,
     "", "M1001", "Main"],
    ["L1002", "AUTO", "F", "", "", "", "", "", "", 35, "2024-08-01", "2030-08-01", 72, 41000, 30112.87, 6.49,
     "", "M1002", "Main"],
    ["L1003", "MTG30", "F", "", "", "", "", "", "", 0, "2021-05-20", "2051-05-20", 360, 280000, 251338.40, 3.125,
     "", "M1003", "North"],
    ["L1004", "HELOC", "V", "PRIME", 0.50, 1, "2026-07-01", 4.00, 18.00, 0, "2023-02-10", "", "", 60000,
     38215.55, 7.75, "", "M1001", "Main"],
]
LOAN_HEAD = ["loan_id", "product_code", "rate_type", "index", "margin", "reset_months", "next_reset_date",
             "rate_floor", "rate_cap", "days_delinquent", "origination_date", "maturity_date",
             "original_term_months", "original_balance", "current_balance", "rate", "amortization_months",
             "member_id", "branch"]
CERTS = [["C2001", "CD12", "2026-01-31", "2027-01-31", 12, 25000.00, 4.10, "M1002", "Main"],
         ["C2002", "CD24", "2025-09-30", "2027-09-30", 24, 60000.00, 4.25, "M1004", "North"]]
CERT_HEAD = ["certificate_id", "product_code", "open_date", "maturity_date", "term_months", "balance", "rate",
             "member_id", "branch"]
SHARES = [["REG", 0, 2500, 90, 150000.00, 0.10], ["REG", 2500, "", 40, 480000.00, 0.25],
          ["DRAFT", 0, "", 70, 210000.00, 0.05], ["MMA", 0, "", 12, 300000.00, 2.50]]
SHARE_HEAD = ["product_code", "tier_low", "tier_high", "accounts", "balance", "rate"]
INVESTMENTS = [["S3001", "UST", 500000, "AFS", "", "", "", "2025-06-30", "US Treasury note", 3.875, "2028-06-30",
                498750.00, 3.96, ""],
               ["S3002", "MBS", 400000, "AFS", "", 240, 8, "2022-03-15", "FNMA 30-year pool", 3.00, "2052-03-01",
                352400.00, 3.20, 450000]]
INV_HEAD = ["security_id", "type", "par", "classification", "next_call_date", "wam_months", "cpr", "purchase_date",
            "description", "coupon", "maturity_date", "book_value", "book_yield", "original_face"]
BORROWINGS = [["B4001", "FHLB", "advance", 250000.00, 4.05, "2026-01-15", "2028-01-15"]]
BORROW_HEAD = ["borrowing_id", "lender", "type", "balance", "rate", "start_date", "maturity_date"]

MAP = {
    "loans": {"AUTO": "new_auto", "MTG30": "first_mortgage", "HELOC": "heloc"},
    "certificates": {"*": "certificates"},
    "shares": {"REG": "regular_shares", "DRAFT": "share_drafts", "MMA": "money_market"},
    "investments": {"UST": "investments", "MBS": "mortgage_securities"},
    "borrowings": {"FHLB": "borrowings"},
    "gl": {"cash": "1000", "fixed_assets": "1400", "ncusif": "1500", "other_assets": "1600", "allowance": "1290",
           "other_liabilities": "2100", "loans": "1200", "investments": "1100", "certificates": "3500",
           "shares": "3000, 3010, 3020", "borrowings": "2000"},
}


def _total(rows, head, column):
    i = head.index(column)
    return round(sum(float(r[i]) for r in rows), 2)


def ledger():
    """A trial balance that the example rows tie to, and that balances."""
    loans = _total(LOANS, LOAN_HEAD, "current_balance")
    investments = _total(INVESTMENTS, INV_HEAD, "book_value")
    certificates = _total(CERTS, CERT_HEAD, "balance")
    by_code = {}
    for r in SHARES:
        by_code[r[0]] = by_code.get(r[0], 0.0) + r[4]
    borrowed = _total(BORROWINGS, BORROW_HEAD, "balance")
    assets = [("1000", "Cash and cash equivalents", 250000.00), ("1100", "Investments (book value)", investments),
              ("1200", "Loans to members", loans), ("1290", "Allowance for credit losses", -3500.00),
              ("1400", "Land, buildings and equipment", 180000.00), ("1500", "NCUSIF deposit", 38000.00),
              ("1600", "Accrued interest and other assets", 22000.00)]
    liabilities = [("2000", "Borrowings", -borrowed), ("2100", "Accrued dividends and other liabilities", -15000.00),
                   ("3000", "Regular shares", -by_code["REG"]), ("3010", "Share drafts", -by_code["DRAFT"]),
                   ("3020", "Money market shares", -by_code["MMA"]), ("3500", "Share certificates", -certificates)]
    equity = -(sum(v for _, _, v in assets) + sum(v for _, _, v in liabilities))
    return assets + liabilities + [("3900", "Regular reserve and undivided earnings", round(equity, 2))]


def assumptions(bank=False):
    used = {p for section in ("loans", "certificates", "shares", "investments", "borrowings")
            for p in MAP[section].values()} | {"cash", "fixed_assets", "ncusif", "other_assets", "other_liabilities"}
    raw = {
        "notes": {
            "about": "A starter folder from `keel init`. Every setting is a Keel starting value, not this "
                     "institution's: replace them, starting with the curve, the Products sheet and the income "
                     "lines, before relying on the results.",
            "rates": "Percent. Speeds (cpr, runoff, charge_off, growth) are annual percent; beta is the percent "
                     "of a short-rate move an administered rate follows.",
            "curve": "The Treasury par curve for %s. Replace it with the curve on your analysis date." % AS_OF},
        "as_of": AS_OF, "institution": "bank" if bank else "credit_union",
        "curve": {str(k): v for k, v in sorted(callreport.TREASURY[AS_OF].items())},
        "indexes": {"PRIME": {"tenor_months": 1, "spread": 3.00}, "SOFR": {"tenor_months": 1, "spread": 0.0},
                    "TSY_1Y": {"tenor_months": 12, "spread": 0.0}},
        "rate_floor": 0.0, "short_tenor_months": 1, "horizon_months": 60, "nev_max_months": 360,
        "fee_income": 20000.0, "operating_expense": 50000.0, "expense_growth": 3.0,
        "cash_minimum": 50000.0, "overnight_spread": 0.25,
        "products": {k: dict(v) for k, v in callreport.PRODUCTS.items() if k in used},
        "extra_scenarios": [{"name": "ramp +200", "shock_bp": 200, "ramp_months": 12},
                            {"name": "ramp -200", "shock_bp": -200, "ramp_months": 12}],
        "liquidity": {"stress_months": 3, "contingent": [
            {"name": "FHLB unused borrowing capacity", "capacity": 200000.0, "secured": True}]},
    }
    if bank:
        raw["tax_rate"] = 21.0
    return raw


GUIDE = """Keel starter folder
===================

Everything here runs as it is: `python -m keel run "{folder}"` writes
report/report.html from the example rows. Then replace them with your own,
one file at a time, running again after each:

1. data/gl.csv - your trial balance on the analysis date: account,
   description, balance (debits positive, credits negative).
2. data/product_map.xlsx - one sheet per section. Map your core's product
   codes to Keel products (loans, certificates, shares, investments,
   borrowings: code | product), and on the gl sheet your ledger accounts
   (key | account): cash, fixed_assets, ncusif, other_assets, allowance,
   other_liabilities, and the accounts each detail file must tie to - loans,
   investments, certificates, shares, borrowings (a comma-separated list,
   or 30* for every account starting 30).
3. data/loans.csv, certificates.csv, shares.csv, investments.csv,
   borrowings.csv - your core export in these columns. Extra columns are
   ignored; dates are YYYY-MM-DD (Excel dates also read); rates are percent.
4. assumptions.xlsx - set as_of and the curve for your analysis date, the
   income lines on the Settings sheet, and each product's behaviour on the
   Products sheet. Every value is a Keel starting value until you change it.

After each run, the report's Reconciliation section lists every detail file
against its ledger account. A file that does not tie is named with the
difference; fix the mapping or the export before reading anything else.

START-HERE.md in the Keel repository explains each step in full.
"""


def write(folder, bank=False):
    if os.path.exists(folder) and os.listdir(folder):
        raise InputError("%s already exists and is not empty; choose a new folder" % folder)
    data = os.path.join(folder, "data")
    os.makedirs(data, exist_ok=True)
    for name, head, rows in (("loans.csv", LOAN_HEAD, LOANS), ("certificates.csv", CERT_HEAD, CERTS),
                             ("shares.csv", SHARE_HEAD, SHARES), ("investments.csv", INV_HEAD, INVESTMENTS),
                             ("borrowings.csv", BORROW_HEAD, BORROWINGS),
                             ("gl.csv", ["account", "description", "balance"],
                              [[a, d, "%.2f" % v] for a, d, v in ledger()])):
        with open(os.path.join(data, name), "w", encoding="utf-8", newline="") as handle:
            w = csv.writer(handle)
            w.writerow(head)
            w.writerows(rows)
    book = {}
    for section, codes in MAP.items():
        head = ["key", "account"] if section == "gl" else ["code", "product"]
        book[section] = [head] + [[k, v] for k, v in codes.items()]
    xlsx.write_workbook(os.path.join(data, "product_map.xlsx"), book)
    xlsx.write_workbook(os.path.join(folder, "assumptions.xlsx"), settings.to_workbook(assumptions(bank)))
    with open(os.path.join(folder, "START-HERE.txt"), "w", encoding="utf-8") as handle:
        handle.write(GUIDE.format(folder=folder))
    return folder
