#!/usr/bin/env python3
"""Three synthetic credit unions, written as the files a core system exports.

    python tools/make_samples.py            # writes examples/<name>/data/

Every row is invented, from a fixed seed, so the files are the same on every
run. Nothing describes a real institution or member.

For each credit union:

    data/loans.csv          one row per loan
    data/certificates.csv   one row per share certificate
    data/shares.csv         non-maturity shares by product and balance tier
    data/investments.csv    one row per security
    data/borrowings.csv     one row per borrowing
    data/gl.csv             the general-ledger trial balance they must tie to
    data/product_map.json   core product codes -> Keel products
    assumptions.json        behaviour, the plan, and liquidity

The mixes are calibrated to NCUA's Quarterly Credit Union Data Summary for
2026Q2: loans about 70% of assets, investments 17%, cash 7.5%; 1-4 family real
estate 47% of loans, auto 28% (used twice new), cards 5%, commercial 11%;
regular shares 28% of shares, certificates 29%, money market 18%; system net
worth 11.42%; net charge-offs 0.78%. Each credit union departs from the system
on purpose, so the three stress different parts of the model.
"""

import calendar
import csv
import datetime
import json
import math
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys  # noqa: E402
sys.path.insert(0, ROOT)
AS_OF = datetime.date(2026, 6, 30)

# A synthetic rate history: what a product was priced at when it was made.
# Rough shape of 2019-2026 US rates; invented, not quoted from anywhere.
HISTORY = {  # year: (mortgage 30y, new auto 60m, used auto, 12m certificate, 2y treasury)
    2016: (3.8, 3.0, 3.8, 1.0, 1.0), 2017: (4.0, 3.2, 4.0, 1.2, 1.4), 2018: (4.6, 3.8, 4.6, 1.9, 2.5),
    2019: (4.0, 4.0, 4.8, 2.0, 2.0), 2020: (3.1, 3.2, 4.0, 0.9, 0.3), 2021: (3.0, 2.9, 3.6, 0.4, 0.3),
    2022: (5.3, 4.4, 5.2, 1.8, 3.2), 2023: (6.8, 6.3, 7.2, 4.4, 4.6), 2024: (6.7, 6.2, 7.1, 4.3, 4.3),
    2025: (6.4, 5.9, 6.9, 3.9, 3.9), 2026: (6.2, 5.8, 6.8, 3.7, 3.6)}

CURVE = {"1": 4.00, "3": 3.95, "6": 3.85, "12": 3.70, "24": 3.60, "36": 3.60, "60": 3.70,
         "84": 3.85, "120": 4.00, "360": 4.40}


def months_back(n):
    """The date n months before AS_OF, on the same day of month where possible."""
    y, m = AS_OF.year, AS_OF.month - n
    while m <= 0:
        m += 12
        y -= 1
    return datetime.date(y, m, min(AS_OF.day, calendar.monthrange(y, m)[1]))


def months_ahead(date, n):
    y, m = date.year, date.month + n
    while m > 12:
        m -= 12
        y += 1
    return datetime.date(y, m, min(date.day, calendar.monthrange(y, m)[1]))


def history(year, column):
    return HISTORY[max(min(year, 2026), 2016)][column]


def amortized(original, rate, term, elapsed):
    """Scheduled balance after `elapsed` payments."""
    r = rate / 1200.0
    if elapsed >= term:
        return 0.0
    if r == 0:
        return original * (1 - elapsed / term)
    payment = original * r / (1 - (1 + r) ** -term)
    return original * (1 + r) ** elapsed - payment * ((1 + r) ** elapsed - 1) / r


def payment(balance, rate, remaining):
    r = rate / 1200.0
    return balance / remaining if r == 0 else balance * r / (1 - (1 + r) ** -remaining)


# ---------------------------------------------------------------- profiles

PROFILES = {
    "small-cu": {
        "label": "Prairie Community Credit Union (synthetic)",
        "seed": 11, "assets": 85e6, "net_worth": 0.123,
        "cash": 0.11, "investments": 0.25,
        "loans": {"NEWAUTO": 0.20, "USEDAUTO": 0.34, "MTG30": 0.12, "MTG15": 0.06, "HELOC": 0.06,
                  "CARD": 0.07, "SIG": 0.15},
        "shares": {"REG": 0.40, "DRAFT": 0.20, "MMA": 0.08, "IRA": 0.05, "CERT": 0.27},
        "borrowings": [],
        "fee_income": 0.0110, "opex": 0.042,
        "note": "Just over NCUA's $50M line: consumer lending, very liquid, few mortgages.",
        "formats": {"settings": "json", "data": "csv"},
    },
    "mid-cu": {
        "label": "Riverbend Federal Credit Union (synthetic)",
        "seed": 23, "assets": 560e6, "net_worth": 0.108,
        "cash": 0.07, "investments": 0.18,
        "loans": {"NEWAUTO": 0.12, "USEDAUTO": 0.22, "MTG30": 0.19, "MTG15": 0.08, "ARM51": 0.09,
                  "HELOC": 0.10, "CARD": 0.05, "SIG": 0.04, "MBLCRE": 0.11},
        "shares": {"REG": 0.29, "DRAFT": 0.17, "MMA": 0.19, "IRA": 0.05, "CERT": 0.30},
        "borrowings": [(0.015, 36, "FHLB"), (0.01, 18, "FHLB")],
        "fee_income": 0.0110, "opex": 0.036,
        "note": "Close to the system's own mix, with some commercial real estate.",
        "formats": {"settings": "xlsx", "data": "csv"},
    },
    "large-cu": {
        "label": "Harbor State Credit Union (synthetic)",
        "seed": 37, "assets": 2.4e9, "net_worth": 0.094,
        "cash": 0.04, "investments": 0.13,
        "loans": {"NEWAUTO": 0.07, "USEDAUTO": 0.14, "MTG30": 0.41, "MTG15": 0.06, "ARM51": 0.08,
                  "HELOC": 0.07, "CARD": 0.04, "SIG": 0.02, "MBLCRE": 0.11},
        "shares": {"REG": 0.24, "DRAFT": 0.13, "MMA": 0.22, "IRA": 0.04, "CERT": 0.37},
        "borrowings": [(0.03, 12, "FHLB"), (0.025, 24, "FHLB"), (0.02, 48, "FHLB"), (0.01, 6, "CORPORATE")],
        "fee_income": 0.0090, "opex": 0.029,
        "note": "Mortgage-heavy and certificate-funded, with FHLB borrowing: the shape the NEV test bites.",
        "formats": {"settings": "xlsx", "data": "xlsx"},
    },
}

# Product catalogue: core code -> behaviour when generating loans.
LOAN_TYPES = {
    #          avg size, orig terms (months), history column, spread over history, amortization
    "NEWAUTO": (32000, (60, 72, 84), 1, 0.0, "level"),
    "USEDAUTO": (21000, (48, 60, 72), 2, 0.0, "level"),
    "MTG30": (215000, (360,), 0, 0.0, "level"),
    "MTG15": (160000, (180,), 0, -0.6, "level"),
    "ARM51": (260000, (360,), 0, -0.5, "level"),
    "HELOC": (48000, (0,), None, 0.0, "revolving"),
    "CARD": (3100, (0,), None, 0.0, "revolving"),
    "SIG": (6500, (24, 36, 48, 60), 2, 3.5, "level"),
    "MBLCRE": (900000, (120, 180), 0, 0.4, "balloon"),
}


class Generator(object):

    def __init__(self, name, profile):
        self.name, self.p = name, profile
        self.rng = random.Random(profile["seed"])
        self.dir = os.path.join(ROOT, "examples", name)
        self.data = os.path.join(self.dir, "data")

    def run(self):
        os.makedirs(self.data, exist_ok=True)
        p = self.p
        assets = p["assets"]
        loan_total = assets * (1 - p["cash"] - p["investments"] - 0.03)
        loans = self.loans(loan_total)
        investments = self.investments(assets * p["investments"])
        equity = assets * p["net_worth"]
        other_liabilities = assets * 0.008
        borrowings = self.borrowings(assets)
        share_total = assets - equity - other_liabilities - sum(b["balance"] for b in borrowings)
        certificates = self.certificates(share_total * p["shares"]["CERT"])
        shares = self.shares(share_total)
        self.write_csv("loans.csv", loans)
        self.write_csv("certificates.csv", certificates)
        self.write_csv("shares.csv", shares)
        self.write_csv("investments.csv", investments)
        self.write_csv("borrowings.csv", borrowings, ["borrowing_id", "lender", "type", "balance", "rate",
                                                      "start_date", "maturity_date"])
        self.gl(loans, certificates, shares, investments, borrowings, assets, equity, other_liabilities)
        self.product_map()
        self.assumptions(assets)
        return {"loans": len(loans), "certificates": len(certificates), "securities": len(investments)}

    # ---------------------------------------------------------------- loans

    def loans(self, total):
        rows = []
        for code, share in self.p["loans"].items():
            target = total * share
            avg, terms, column, spread, kind = LOAN_TYPES[code]
            made = 0.0
            while made < target:
                row = self.loan(code, avg, terms, column, spread, kind, len(rows) + 1)
                if row is None:
                    continue
                rows.append(row)
                made += row["current_balance"]
        return rows

    def loan(self, code, avg, terms, column, spread, kind, n):
        rng = self.rng
        size = avg * math.exp(rng.gauss(0, 0.45)) * (0.6 if kind == "revolving" else 1.0)
        base = {"loan_id": "L%07d" % n, "product_code": code, "rate_type": "F", "index": "", "margin": "",
                "reset_months": "", "next_reset_date": "", "rate_floor": "", "rate_cap": "",
                "days_delinquent": 0 if rng.random() > 0.012 else rng.choice((35, 65, 95, 125))}
        if kind == "revolving":
            opened = months_back(rng.randint(1, 180))
            if code == "HELOC":
                base.update(rate_type="V", index="PRIME", margin="%.2f" % rng.choice((0.0, 0.25, 0.5, 0.75, 1.0)),
                            reset_months=1, rate_floor="4.00")
                rate = 7.00 + float(base["margin"])
            else:
                rate = rng.choice((9.9, 11.9, 12.9, 14.9, 17.9))
            balance = size * rng.uniform(0.2, 1.0)
            base.update(origination_date=opened.isoformat(), maturity_date="", original_term_months=0,
                        original_balance=round(size, 2), current_balance=round(balance, 2),
                        rate="%.3f" % rate, scheduled_payment="")
            return base
        term = rng.choice(terms)
        age = int(rng.triangular(0, term * (0.55 if term > 180 else 0.9), term * 0.1))
        opened = months_back(age)
        rate = history(opened.year, column) + spread + rng.gauss(0, 0.35)
        rate = max(1.9, round(rate * 8) / 8)
        if code == "ARM51":
            fixed_period = 60
            base.update(rate_type="V", index="TSY_1Y", margin="2.75", reset_months=12, rate_floor="2.75",
                        rate_cap="%.2f" % (rate + 5))
            if age >= fixed_period:
                rate = min(float(base["rate_cap"]), max(2.75, 3.70 + 2.75 + rng.gauss(0, 0.2)))
                next_reset = months_ahead(opened, fixed_period + 12 * ((age - fixed_period) // 12 + 1))
            else:
                next_reset = months_ahead(opened, fixed_period)
            base["next_reset_date"] = next_reset.isoformat()
        amort_term = 300 if kind == "balloon" else term
        balance = amortized(size, rate, amort_term, age) * rng.uniform(0.92, 1.0)
        if balance < 250:
            return None
        maturity = months_ahead(opened, term)
        base.update(origination_date=opened.isoformat(), maturity_date=maturity.isoformat(),
                    original_term_months=term, original_balance=round(size, 2),
                    current_balance=round(balance, 2), rate="%.3f" % rate,
                    scheduled_payment="%.2f" % payment(balance, rate, max(1, amort_term - age)))
        if kind == "balloon":
            base["amortization_months"] = amort_term
        return base

    # ---------------------------------------------------------------- shares

    def certificates(self, total):
        rng, rows, made = self.rng, [], 0.0
        while made < total:
            term = rng.choice((6, 12, 12, 12, 18, 24, 24, 36, 60))
            opened = months_back(rng.randint(0, term - 1))
            rate = history(opened.year, 3) * (1 + 0.08 * math.log(term / 12.0 + 0.01)) + 0.35 + rng.gauss(0, 0.1)
            balance = 26000 * math.exp(rng.gauss(0, 0.8))
            rows.append({"certificate_id": "C%07d" % (len(rows) + 1),
                         "product_code": "IRACD" if rng.random() < 0.12 else "CD%d" % term,
                         "open_date": opened.isoformat(), "maturity_date": months_ahead(opened, term).isoformat(),
                         "term_months": term, "balance": round(balance, 2), "rate": "%.3f" % max(0.25, rate)})
            made += balance
        return rows

    def shares(self, share_total):
        tiers = {"REG": ((0, 2500, 0.15, 0.30), (2500, 25000, 0.30, 0.45), (25000, None, 0.50, 0.25)),
                 "DRAFT": ((0, 5000, 0.05, 0.40), (5000, None, 0.15, 0.60)),
                 "MMA": ((0, 25000, 1.75, 0.20), (25000, 100000, 2.75, 0.40), (100000, None, 3.40, 0.40)),
                 "IRA": ((0, None, 1.50, 1.0),)}
        avg = {"REG": 3100, "DRAFT": 4200, "MMA": 38000, "IRA": 22000}
        rows = []
        for code, pieces in tiers.items():
            product_total = share_total * self.p["shares"][code]
            for low, high, rate, share in pieces:
                balance = product_total * share
                rows.append({"product_code": code, "tier_low": low, "tier_high": high if high else "",
                             "accounts": max(1, int(balance / avg[code] * self.rng.uniform(0.8, 1.2))),
                             "balance": round(balance, 2), "rate": "%.3f" % rate})
        return rows

    # ---------------------------------------------------------------- investments

    def investments(self, total):
        """Securities in NCUA's system maturity mix: about a quarter each under
        1 year, 1-3, 3-5 and 5-10 years, and 4% beyond."""
        rng, rows = self.rng, []
        mix = (("UST", 0.22), ("AGENCY", 0.14), ("AGENCYCALL", 0.12), ("MBS", 0.30), ("CMO", 0.08),
               ("MUNI", 0.06), ("CD", 0.05), ("FHLBSTOCK", 0.02), ("CUSO", 0.01))
        for kind, share in mix:
            target, made = total * share, 0.0
            while made < target:
                par = min(target - made, rng.choice((1, 2, 2.5, 3, 5)) * 1e6 * (0.25 if total < 30e6 else 1))
                if kind == "CD":
                    par = min(target - made, 249000)
                par = max(par, 50000)
                rows.append(self.security(kind, par, len(rows) + 1))
                made += par
        return rows

    def security(self, kind, par, n):
        rng = self.rng
        bought = months_back(rng.randint(1, 72))
        row = {"security_id": "SYN%05d" % n, "type": kind, "par": round(par, 2), "classification":
               "HTM" if kind in ("MUNI",) or rng.random() < 0.15 else "AFS", "next_call_date": "",
               "wam_months": "", "cpr": "", "purchase_date": bought.isoformat()}
        if kind in ("FHLBSTOCK", "CUSO"):
            dividend = "%.2f" % (7.0 if kind == "FHLBSTOCK" else 0.0)
            row.update(description="FHLB membership stock" if kind == "FHLBSTOCK" else "CUSO investment",
                       coupon=dividend, book_yield=dividend, maturity_date="", book_value=round(par, 2))
            return row
        years = {"UST": rng.choice((1, 2, 3, 5, 7)), "AGENCY": rng.choice((2, 3, 5, 7)),
                 "AGENCYCALL": rng.choice((3, 5, 7, 10)), "MUNI": rng.choice((5, 7, 10, 12)),
                 "CD": rng.choice((1, 2, 3)), "MBS": rng.choice((15, 20, 30)), "CMO": rng.choice((10, 15))}[kind]
        maturity = months_ahead(bought, 12 * years)
        if maturity <= AS_OF:
            maturity = months_ahead(AS_OF, rng.randint(3, 18))
        coupon = history(bought.year, 4) + {"UST": 0, "AGENCY": 0.15, "AGENCYCALL": 0.6, "MUNI": 0.3,
                                            "CD": 0.25, "MBS": 1.0, "CMO": 0.8}[kind] + rng.gauss(0, 0.2)
        coupon = max(0.5, round(coupon * 8) / 8)
        premium = rng.uniform(-0.01, 0.015)
        row.update(description={"UST": "US Treasury note", "AGENCY": "Agency bullet",
                                "AGENCYCALL": "Agency callable", "MUNI": "Municipal bond",
                                "CD": "Negotiable CD", "MBS": "Agency MBS pass-through",
                                "CMO": "Agency CMO"}[kind],
                   coupon="%.3f" % coupon, maturity_date=maturity.isoformat(),
                   book_value=round(par * (1 + premium), 2), book_yield="%.3f" % (coupon - premium * 100 / years))
        if kind == "AGENCYCALL":
            row["next_call_date"] = max(months_ahead(bought, 12), months_ahead(AS_OF, 1)).isoformat()
        if kind in ("MBS", "CMO"):
            factor = rng.uniform(0.45, 0.95)
            row["par"] = round(par, 2)
            row["book_value"] = round(par * (1 + premium), 2)
            row["wam_months"] = int((maturity.year - AS_OF.year) * 12 + maturity.month - AS_OF.month)
            row["cpr"] = "%.1f" % rng.uniform(5, 12)
            row["original_face"] = round(par / factor, 2)
        return row

    # ---------------------------------------------------------------- borrowings, GL

    def borrowings(self, assets):
        rows = []
        for i, (share, months, lender) in enumerate(self.p["borrowings"], 1):
            start = months_back(self.rng.randint(1, 24))
            rows.append({"borrowing_id": "B%03d" % i, "lender": lender,
                         "type": "fixed advance" if lender == "FHLB" else "term loan",
                         "balance": round(assets * share, 2),
                         "rate": "%.3f" % (history(start.year, 4) + 0.35),
                         "start_date": start.isoformat(),
                         "maturity_date": months_ahead(AS_OF, months).isoformat()})
        return rows

    def gl(self, loans, certificates, shares, investments, borrowings, assets, equity, other_liabilities):
        loan_total = sum(r["current_balance"] for r in loans)
        allowance = loan_total * 0.011
        invest_total = sum(r["book_value"] for r in investments)
        cert_total = sum(r["balance"] for r in certificates)
        share_by = {}
        for r in shares:
            share_by[r["product_code"]] = share_by.get(r["product_code"], 0) + r["balance"]
        borrowed = sum(r["balance"] for r in borrowings)
        liabilities = cert_total + sum(share_by.values()) + borrowed + other_liabilities
        equity = equity
        fixed = assets * 0.018
        ncusif = assets * 0.0085 * (sum(share_by.values()) + cert_total) / assets
        other_assets = assets * 0.006
        cash = liabilities + equity - loan_total + allowance - invest_total - fixed - ncusif - other_assets
        lines = [("1000", "Cash and cash equivalents", cash), ("1100", "Investments (book value)", invest_total),
                 ("1200", "Loans to members", loan_total),
                 ("1290", "Allowance for credit losses on loans", -allowance),
                 ("1400", "Land and buildings, equipment", fixed), ("1500", "NCUSIF capitalization deposit", ncusif),
                 ("1600", "Accrued interest and other assets", other_assets),
                 ("2000", "Borrowings", -borrowed), ("2100", "Accrued dividends and other liabilities", -other_liabilities)]
        names = {"REG": "Regular shares", "DRAFT": "Share drafts", "MMA": "Money market shares", "IRA": "IRA shares"}
        for i, (code, total) in enumerate(sorted(share_by.items())):
            lines.append(("30%d0" % i, names[code], -total))
        lines.append(("3500", "Share certificates", -cert_total))
        undivided = equity * 0.86
        lines += [("3900", "Regular reserve", -(equity - undivided)), ("3910", "Undivided earnings", -undivided)]
        rows = [{"account": a, "description": d, "balance": round(b, 2)} for a, d, b in lines]
        drift = round(sum(r["balance"] for r in rows), 2)
        rows[0]["balance"] = round(rows[0]["balance"] - drift, 2)
        self.write_csv("gl.csv", rows)

    def product_map(self):
        mapping = {
            "loans": {"NEWAUTO": "new_auto", "USEDAUTO": "used_auto", "MTG30": "first_mortgage",
                      "MTG15": "first_mortgage_15", "ARM51": "arm_mortgage", "HELOC": "heloc",
                      "CARD": "credit_card", "SIG": "unsecured", "MBLCRE": "commercial_re"},
            "certificates": {"IRACD": "certificates", "*": "certificates"},
            "shares": {"REG": "regular_shares", "DRAFT": "share_drafts", "MMA": "money_market",
                       "IRA": "ira_shares"},
            "investments": {"UST": "treasuries", "AGENCY": "agency_bullets", "AGENCYCALL": "agency_callables",
                            "MBS": "agency_mbs", "CMO": "agency_cmo", "MUNI": "municipals", "CD": "invest_cds",
                            "FHLBSTOCK": "fhlb_stock", "CUSO": "cuso"},
            "borrowings": {"FHLB": "borrowings", "CORPORATE": "borrowings"},
            "gl": {"cash": "1000", "fixed_assets": "1400", "ncusif": "1500", "other_assets": "1600",
                   "allowance": "1290", "other_liabilities": "2100"},
        }
        for stale in ("product_map.json", "product_map.xlsx"):
            if os.path.isfile(os.path.join(self.data, stale)):
                os.remove(os.path.join(self.data, stale))
        if self.p["formats"]["settings"] == "xlsx":
            from keel import xlsx
            sheets = {}
            for section, codes in mapping.items():
                head = ["key", "account"] if section == "gl" else ["code", "product"]
                sheets[section] = [head] + [[k, v] for k, v in codes.items()]
            xlsx.write_workbook(os.path.join(self.data, "product_map.xlsx"), sheets)
        else:
            with open(os.path.join(self.data, "product_map.json"), "w", encoding="utf-8") as handle:
                json.dump(mapping, handle, indent=2)

    def assumptions(self, assets):
        p = self.p
        spec = {
            "notes": {
                "about": "%s. %s Every figure is invented from a fixed seed by tools/make_samples.py; "
                         "none describes a real institution." % (p["label"], p["note"]),
                "rates": "Percent. Speeds (cpr, runoff, charge_off, growth) are annual percent; beta is the "
                         "percent of a short-rate move an administered rate follows.",
                "non_maturity_shares": "Decay, beta and discount spread for non-maturity shares are "
                                       "management assumptions. The NCUA supervisory test replaces them with "
                                       "standardized prices (99.00 base, 95.04 at +300bp)."},
            "as_of": AS_OF.isoformat(), "curve": CURVE,
            "indexes": {"PRIME": {"tenor_months": 1, "spread": 3.00}, "SOFR": {"tenor_months": 1, "spread": 0.0},
                        "TSY_1Y": {"tenor_months": 12, "spread": 0.0}},
            "rate_floor": 0.0, "short_tenor_months": 1, "horizon_months": 60, "nev_max_months": 360,
            "fee_income": round(assets * p["fee_income"], -3),
            "operating_expense": round(assets * p["opex"], -3), "expense_growth": 3.0,
            "cash_minimum": round(assets * 0.025, -3), "overnight_spread": 0.25,
            "products": BEHAVIOUR,
            "extra_scenarios": [{"name": "ramp +200", "shock_bp": 200, "ramp_months": 12},
                                {"name": "ramp -200", "shock_bp": -200, "ramp_months": 12},
                                {"name": "flattener", "shape": {"1": 200, "24": 100, "120": 0}},
                                {"name": "steepener", "shape": {"1": 0, "24": 50, "120": 200}},
                                {"name": "short end +200", "shape": {"1": 200, "12": 200, "36": 0}}],
            "liquidity": {"stress_months": 3, "contingent": [
                {"name": "FHLB unused borrowing capacity", "capacity": round(assets * 0.12, -3)},
                {"name": "Central Liquidity Facility (through a corporate credit union)",
                 "capacity": round(assets * 0.04, -3)}]},
        }
        for stale in ("assumptions.json", "assumptions.xlsx"):
            if os.path.isfile(os.path.join(self.dir, stale)):
                os.remove(os.path.join(self.dir, stale))
        if self.p["formats"]["settings"] == "xlsx":
            from keel import settings, xlsx
            xlsx.write_workbook(os.path.join(self.dir, "assumptions.xlsx"), settings.to_workbook(spec))
        else:
            with open(os.path.join(self.dir, "assumptions.json"), "w", encoding="utf-8") as handle:
                json.dump(spec, handle, indent=2)

    def write_csv(self, name, rows, fields=None):
        """A data table in this credit union's data format: CSV, or a workbook."""
        fields = fields or list(dict.fromkeys(k for r in rows for k in r))
        for stale in (".csv", ".xlsx"):
            path = os.path.join(self.data, os.path.splitext(name)[0] + stale)
            if os.path.isfile(path):
                os.remove(path)
        if self.p["formats"]["data"] == "xlsx":
            from keel import xlsx
            sheet = [fields] + [[_cell(r.get(f)) for f in fields] for r in rows]
            xlsx.write_workbook(os.path.join(self.data, os.path.splitext(name)[0] + ".xlsx"),
                                {os.path.splitext(name)[0]: sheet})
            return
        with open(os.path.join(self.data, name), "w", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)


# Behaviour by Keel product. Percent units, as in assumptions.json. Plausible
# starting points for a synthetic credit union, not a study of anyone's data.
BEHAVIOUR = {
    "cash": {}, "fixed_assets": {}, "ncusif": {}, "other_assets": {}, "other_liabilities": {},
    "treasuries": {"new_term": 24, "new_amortization": "bullet", "discount_spread": 0.0, "liquid": True, "haircut": 2.0},
    "agency_bullets": {"new_term": 36, "new_amortization": "bullet", "spread": 0.15, "discount_spread": 0.15,
                       "liquid": True, "haircut": 4.0},
    "agency_callables": {"new_term": 60, "new_amortization": "callable", "spread": 0.60, "discount_spread": 0.60,
                         "call_threshold": 0.25, "liquid": True, "haircut": 5.0},
    "agency_mbs": {"cpr": 7.0, "cpr_per_100bp": 5.0, "cpr_floor": 4.0, "cpr_cap": 40.0, "new_term": 360,
                   "new_amortization": "level", "spread": 0.60, "discount_spread": 0.60, "liquid": True, "haircut": 6.0},
    "agency_cmo": {"cpr": 8.0, "cpr_per_100bp": 4.0, "cpr_floor": 4.0, "cpr_cap": 35.0, "new_term": 180,
                   "new_amortization": "level", "spread": 0.70, "discount_spread": 0.70, "liquid": True, "haircut": 8.0},
    "municipals": {"new_term": 120, "new_amortization": "bullet", "spread": 0.30, "discount_spread": 0.40,
                   "liquid": True, "haircut": 10.0},
    "invest_cds": {"new_term": 24, "new_amortization": "bullet", "spread": 0.25, "discount_spread": 0.25},
    "fhlb_stock": {}, "cuso": {},
    "new_auto": {"cpr": 18.0, "cpr_per_100bp": 2.0, "new_term": 66, "new_amortization": "level", "spread": 2.30,
                 "discount_spread": 2.30, "growth": 3.0, "charge_off": 0.35},
    "used_auto": {"cpr": 20.0, "cpr_per_100bp": 2.0, "new_term": 60, "new_amortization": "level", "spread": 3.20,
                  "discount_spread": 3.20, "growth": 4.0, "charge_off": 0.90},
    "first_mortgage": {"cpr": 6.0, "cpr_per_100bp": 6.0, "cpr_floor": 3.0, "cpr_cap": 45.0, "new_term": 360,
                       "new_amortization": "level", "spread": 1.90, "discount_spread": 1.90, "growth": 3.0, "charge_off": 0.04},
    "first_mortgage_15": {"cpr": 8.0, "cpr_per_100bp": 6.0, "cpr_floor": 4.0, "cpr_cap": 45.0, "new_term": 180,
                          "new_amortization": "level", "spread": 1.60, "discount_spread": 1.60, "growth": 1.0, "charge_off": 0.03},
    "arm_mortgage": {"cpr": 10.0, "cpr_per_100bp": 4.0, "new_term": 360, "new_amortization": "level",
                     "discount_spread": 2.20, "growth": 2.0, "charge_off": 0.05},
    "heloc": {"runoff": 20.0, "discount_spread": 3.50, "growth": 5.0, "charge_off": 0.20},
    "credit_card": {"runoff": 35.0, "discount_spread": 9.00, "growth": 3.0, "charge_off": 3.50},
    "unsecured": {"cpr": 12.0, "new_term": 48, "new_amortization": "level", "spread": 7.50, "discount_spread": 7.50,
                  "growth": 2.0, "charge_off": 2.50},
    "commercial_re": {"cpr": 5.0, "cpr_per_100bp": 2.0, "new_term": 120, "new_amortization": "balloon",
                      "spread": 2.60, "discount_spread": 2.60, "growth": 5.0, "charge_off": 0.25},
    "regular_shares": {"runoff": 9.0, "runoff_per_100bp": 1.5, "beta": 10.0, "rate_floor": 0.05,
                       "discount_spread": 0.30, "growth": 2.0, "stress_runoff": 12.0},
    "share_drafts": {"runoff": 12.0, "runoff_per_100bp": 1.0, "beta": 5.0, "rate_floor": 0.0,
                     "discount_spread": 0.30, "growth": 2.0, "stress_runoff": 10.0},
    "money_market": {"runoff": 22.0, "runoff_per_100bp": 3.0, "beta": 55.0, "rate_floor": 0.25,
                     "discount_spread": 0.30, "growth": 3.0, "stress_runoff": 25.0},
    "ira_shares": {"runoff": 8.0, "beta": 20.0, "rate_floor": 0.10, "discount_spread": 0.30, "growth": 1.0,
                   "stress_runoff": 5.0},
    "certificates": {"new_term": 12, "new_amortization": "bullet", "spread": -0.20, "discount_spread": -0.20,
                     "growth": 3.0, "stress_runoff": 15.0},
    "borrowings": {"discount_spread": 0.30},
}


def _cell(value):
    """A value as a workbook cell: numbers stay numbers, the rest text."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if str(value).isdigit() else number


def main():
    for name, profile in PROFILES.items():
        counts = Generator(name, profile).run()
        print("%-9s %s" % (name, ", ".join("%d %s" % (v, k) for k, v in counts.items())))


if __name__ == "__main__":
    main()
