"""Any credit union, from NCUA's public call report data.

    python -m keel callreport call-report-data-2026-06.zip --search "tech"
    python -m keel callreport call-report-data-2026-06.zip --cu 68225 --out examples/cu-68225
    python -m keel run examples/cu-68225

NCUA publishes every federally insured credit union's quarterly 5300 call
report as a zip of comma-delimited tables (FOICU for names, FS220 to FS220S
for accounts; AcctDesc.txt describes each account). From one credit union's
accounts this builds a Keel folder, `positions.csv` and `assumptions.json`,
that a report runs on straight away, and `peers.json`, its ratios against
every credit union in its NCUA peer group.

What the call report gives, and what it does not:

* **Balances** by loan type (new and used autos, cards, unsecured, first
  mortgages split fixed over and under 15 years, balloon/hybrid and
  adjustable, junior liens, commercial with and without real estate), by
  share type (regular, drafts, money market, IRA, certificates by maturity
  band), investments by maturity band, borrowings by maturity band. These
  are the credit union's own and tie to its total assets and liabilities.
* **Rates**: the call report gives the most common rate on each loan type,
  not the portfolio yield, and no share rates at all. So loan rates are
  scaled together until they earn exactly the loan interest the credit
  union reported, and share rates are set in typical proportions scaled
  until they cost exactly the dividends it reported. Both tie-outs are
  printed with the result.
* **Behaviour** (prepayment, decay, betas) and remaining terms are not
  reported. They come from Keel's credit-union defaults and are marked as
  such: this is a model of the balance sheet the credit union reports,
  good for a first look and for peers, not a substitute for its own files.
* **The curve** is the Treasury par curve for the cycle date when Keel
  knows it (below), otherwise one you give with `--curve`.

Income statement figures are year to date; they are annualized by the
number of months the cycle covers.
"""

import csv
import io
import json
import os
import zipfile

from keel.model import InputError

#: Daily Treasury par yield curve, percent, by tenor in months. From
#: home.treasury.gov (Daily Treasury Par Yield Curve Rates), read 2026-09-27.
TREASURY = {
    "2026-06-30": {1: 3.70, 2: 3.77, 3: 3.87, 4: 3.92, 6: 4.01, 12: 3.98, 24: 4.14, 36: 4.15, 60: 4.19, 84: 4.30,
                   120: 4.44, 240: 4.93, 360: 4.91},
}

# Accounts read (upper-cased as the tables name them). Descriptions from AcctDesc.txt.
A = {
    "assets": "ACCT_010", "cash": "ACCT_AS0009", "securities": "ACCT_AS0013", "other_investments": "ACCT_AS0017",
    "loans": "ACCT_025B", "allowance_cecl": "ACCT_AS0048", "allowance": "ACCT_719", "land": "ACCT_007",
    "fixed": "ACCT_008", "ncusif": "ACCT_794", "foreclosed": "ACCT_798A", "other_assets": "ACCT_AS0036",
    "inv_1": "ACCT_NV0153", "inv_3": "ACCT_NV0154", "inv_5": "ACCT_NV0155", "inv_10": "ACCT_NV0156",
    "inv_long": "ACCT_NV0157",
    "new_auto": "ACCT_385", "used_auto": "ACCT_370", "card": "ACCT_396", "unsecured": "ACCT_397",
    "mtg_long": "ACCT_RL0002", "mtg_15": "ACCT_RL0005", "mtg_balloon_long": "ACCT_RL0008",
    "mtg_balloon_5": "ACCT_RL0011", "mtg_arm": "ACCT_RL0014", "first_total": "ACCT_RL0016",
    "junior_closed_fixed": "ACCT_RL0019", "junior_closed_arm": "ACCT_RL0022", "junior_open_fixed": "ACCT_RL0025",
    "junior_open_arm": "ACCT_RL0028", "junior_total": "ACCT_RL0030", "other_re": "ACCT_RL0044",
    "commercial_re": "ACCT_718A5", "commercial_other": "ACCT_400P", "other_secured": "ACCT_698C",
    "leases": "ACCT_002",
    "rate_card": "ACCT_521", "rate_unsecured": "ACCT_522", "rate_new_auto": "ACCT_523", "rate_used_auto": "ACCT_524",
    "rate_commercial_re": "ACCT_525", "rate_commercial_other": "ACCT_526", "rate_junior": "ACCT_562A",
    "rate_other_re": "ACCT_562B", "rate_first": "ACCT_563A", "rate_leases": "ACCT_565", "rate_other_secured": "ACCT_595B",
    "shares": "ACCT_018", "regular": "ACCT_657", "drafts": "ACCT_902", "money_market": "ACCT_911",
    "ira": "ACCT_906C", "certificates": "ACCT_908C", "cert_1": "ACCT_908A", "cert_3": "ACCT_908B1",
    "cert_long": "ACCT_908B2", "other_shares": "ACCT_630", "nonmember": "ACCT_880",
    "borrowings": "ACCT_860C", "borrow_1": "ACCT_860A", "borrow_3": "ACCT_860B1", "borrow_long": "ACCT_860B2",
    "payables": "ACCT_825", "liabilities": "ACCT_LI0069", "net_worth": "ACCT_997", "net_worth_ratio": "ACCT_998",
    "loan_interest": "ACCT_110", "interest_refunded": "ACCT_119", "investment_income": "ACCT_120",
    "interest_income": "ACCT_115", "dividends": "ACCT_380", "deposit_interest": "ACCT_381",
    "borrowing_interest": "ACCT_340", "interest_expense": "ACCT_350", "noninterest_income": "ACCT_117",
    "noninterest_expense": "ACCT_671", "net_income": "ACCT_661A", "charge_offs": "ACCT_550",
    "recoveries": "ACCT_551", "fhlb_line": "ACCT_LQ0040", "clf_capacity": "ACCT_LQ0060", "aoci": "ACCT_EQ0009",
}

#: Behaviour, costs and capital weights for a credit union's products, in
#: assumptions.json units (percent). The same starting points the synthetic
#: samples use; every one is marked as a default in the notes.
PRODUCTS = {
    "cash": {"risk_weight": 0.0}, "ncusif": {"risk_weight": 0.0}, "fixed_assets": {"risk_weight": 100.0},
    "other_assets": {"risk_weight": 100.0}, "other_investments": {"risk_weight": 100.0},
    "other_liabilities": {},
    "investments": {"new_term": 36, "new_amortization": "bullet", "spread": 0.15, "discount_spread": 0.15,
                    "liquid": True, "haircut": 4.0, "risk_weight": 20.0, "servicing_cost": 0.02},
    "mortgage_securities": {"cpr": 7.0, "cpr_per_100bp": 5.0, "cpr_floor": 4.0, "cpr_cap": 40.0, "new_term": 360,
                            "new_amortization": "level", "spread": 0.60, "discount_spread": 0.60, "liquid": True,
                            "haircut": 6.0, "risk_weight": 20.0, "servicing_cost": 0.03},
    "new_auto": {"cpr": 18.0, "cpr_per_100bp": 2.0, "new_term": 66, "new_amortization": "level", "spread": 2.30,
                 "discount_spread": 2.30, "growth": 3.0, "charge_off": 0.35, "risk_weight": 75.0,
                 "servicing_cost": 0.60, "fee_yield": 0.10, "origination_cost": 0.75},
    "used_auto": {"cpr": 20.0, "cpr_per_100bp": 2.0, "new_term": 60, "new_amortization": "level", "spread": 3.20,
                  "discount_spread": 3.20, "growth": 4.0, "charge_off": 0.90, "risk_weight": 75.0,
                  "servicing_cost": 0.70, "fee_yield": 0.15, "origination_cost": 0.75},
    "credit_card": {"runoff": 35.0, "discount_spread": 9.00, "growth": 3.0, "charge_off": 3.50, "risk_weight": 75.0,
                    "servicing_cost": 2.50, "fee_yield": 2.00},
    "unsecured": {"cpr": 12.0, "new_term": 48, "new_amortization": "level", "spread": 7.50, "discount_spread": 7.50,
                  "growth": 2.0, "charge_off": 2.50, "risk_weight": 75.0, "servicing_cost": 1.50, "fee_yield": 0.25,
                  "origination_cost": 1.00},
    "other_secured": {"cpr": 15.0, "new_term": 48, "new_amortization": "level", "spread": 3.00,
                      "discount_spread": 3.00, "growth": 2.0, "charge_off": 0.80, "risk_weight": 75.0,
                      "servicing_cost": 0.80, "origination_cost": 0.75},
    "first_mortgage": {"cpr": 6.0, "cpr_per_100bp": 6.0, "cpr_floor": 3.0, "cpr_cap": 45.0, "new_term": 360,
                       "new_amortization": "level", "spread": 1.90, "discount_spread": 1.90, "growth": 3.0,
                       "charge_off": 0.04, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05,
                       "origination_cost": 1.00},
    "first_mortgage_15": {"cpr": 8.0, "cpr_per_100bp": 6.0, "cpr_floor": 4.0, "cpr_cap": 45.0, "new_term": 180,
                          "new_amortization": "level", "spread": 1.60, "discount_spread": 1.60, "growth": 1.0,
                          "charge_off": 0.03, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05,
                          "origination_cost": 1.00},
    "hybrid_mortgage": {"cpr": 9.0, "cpr_per_100bp": 5.0, "cpr_floor": 4.0, "cpr_cap": 45.0, "new_term": 360,
                        "new_amortization": "balloon", "new_amort_term": 360, "spread": 1.70, "discount_spread": 1.70,
                        "growth": 2.0, "charge_off": 0.05, "risk_weight": 50.0, "servicing_cost": 0.25,
                        "origination_cost": 1.00},
    "arm_mortgage": {"cpr": 10.0, "cpr_per_100bp": 4.0, "new_term": 360, "new_amortization": "level",
                     "discount_spread": 2.20, "growth": 2.0, "charge_off": 0.05, "risk_weight": 50.0,
                     "servicing_cost": 0.25, "origination_cost": 1.00},
    "home_equity_loan": {"cpr": 12.0, "new_term": 120, "new_amortization": "level", "spread": 2.50,
                         "discount_spread": 2.50, "growth": 2.0, "charge_off": 0.15, "risk_weight": 100.0,
                         "servicing_cost": 0.40, "origination_cost": 0.50},
    "heloc": {"runoff": 20.0, "discount_spread": 3.50, "growth": 5.0, "charge_off": 0.20, "risk_weight": 100.0,
              "servicing_cost": 0.50, "fee_yield": 0.10, "origination_cost": 0.50},
    "other_real_estate": {"cpr": 8.0, "new_term": 180, "new_amortization": "level", "spread": 2.50,
                          "discount_spread": 2.50, "growth": 2.0, "charge_off": 0.15, "risk_weight": 100.0,
                          "servicing_cost": 0.40},
    "commercial_re": {"cpr": 5.0, "cpr_per_100bp": 2.0, "new_term": 120, "new_amortization": "balloon",
                      "spread": 2.60, "discount_spread": 2.60, "growth": 5.0, "charge_off": 0.25,
                      "risk_weight": 100.0, "servicing_cost": 0.40, "fee_yield": 0.10, "origination_cost": 0.75},
    "commercial_other": {"cpr": 5.0, "new_term": 60, "new_amortization": "level", "spread": 3.00,
                         "discount_spread": 3.00, "growth": 3.0, "charge_off": 0.50, "risk_weight": 100.0,
                         "servicing_cost": 0.50, "fee_yield": 0.15, "origination_cost": 0.50},
    "leases": {"cpr": 10.0, "new_term": 48, "new_amortization": "level", "spread": 2.50, "discount_spread": 2.50,
               "growth": 2.0, "charge_off": 0.40, "risk_weight": 100.0, "servicing_cost": 0.50},
    "other_loans": {"cpr": 10.0, "new_term": 48, "new_amortization": "level", "spread": 3.00,
                    "discount_spread": 3.00, "growth": 2.0, "charge_off": 0.60, "risk_weight": 100.0,
                    "servicing_cost": 0.60},
    "regular_shares": {"runoff": 9.0, "runoff_per_100bp": 1.5, "beta": 10.0, "rate_floor": 0.05,
                       "discount_spread": 0.30, "growth": 2.0, "stress_runoff": 12.0, "servicing_cost": 0.60,
                       "fee_yield": 0.35},
    "share_drafts": {"runoff": 12.0, "runoff_per_100bp": 1.0, "beta": 5.0, "rate_floor": 0.0,
                     "discount_spread": 0.30, "growth": 2.0, "stress_runoff": 10.0, "servicing_cost": 1.60,
                     "fee_yield": 2.20},
    "money_market": {"runoff": 22.0, "runoff_per_100bp": 3.0, "beta": 55.0, "rate_floor": 0.25,
                     "discount_spread": 0.30, "growth": 3.0, "stress_runoff": 25.0, "servicing_cost": 0.30,
                     "fee_yield": 0.05},
    "ira_shares": {"runoff": 8.0, "beta": 20.0, "rate_floor": 0.10, "discount_spread": 0.30, "growth": 1.0,
                   "stress_runoff": 5.0, "servicing_cost": 0.30},
    "certificates": {"new_term": 12, "new_amortization": "bullet", "spread": -0.20, "discount_spread": -0.20,
                     "growth": 3.0, "stress_runoff": 15.0, "servicing_cost": 0.15},
    "borrowings": {"discount_spread": 0.30},
}

#: Typical rates in proportion to one another (percent): scaled together to
#: cost what the credit union reported in dividends.
SHARE_WEIGHTS = {"regular_shares": 0.25, "share_drafts": 0.10, "money_market": 2.40, "ira_shares": 1.50,
                 "certificates": 4.00}

#: (product, account, rate account, remaining months, amortization, extra) for each loan line.
LOANS = (
    ("new_auto", "new_auto", "rate_new_auto", 42, "level", {}),
    ("used_auto", "used_auto", "rate_used_auto", 38, "level", {}),
    ("credit_card", "card", "rate_card", 0, "nonmaturity", {}),
    ("unsecured", "unsecured", "rate_unsecured", 30, "level", {}),
    ("first_mortgage", "mtg_long", "rate_first", 290, "level", {}),
    ("first_mortgage_15", "mtg_15", "rate_first", 120, "level", {}),
    ("hybrid_mortgage", "mtg_balloon_long", "rate_first", 70, "balloon", {"amort_months": 330}),
    ("hybrid_mortgage", "mtg_balloon_5", "rate_first", 36, "balloon", {"amort_months": 330}),
    ("arm_mortgage", "mtg_arm", "rate_first", 320, "level", {"variable": ("TSY_1Y", 2.50, 12)}),
    ("home_equity_loan", "junior_closed_fixed", "rate_junior", 110, "level", {}),
    ("home_equity_loan", "junior_closed_arm", "rate_junior", 110, "level", {"variable": ("PRIME", 0.0, 12)}),
    ("heloc", "junior_open_fixed", "rate_junior", 0, "nonmaturity", {}),
    ("heloc", "junior_open_arm", "rate_junior", 0, "nonmaturity", {"variable": ("PRIME", 0.0, 1)}),
    ("other_real_estate", "other_re", "rate_other_re", 150, "level", {}),
    ("commercial_re", "commercial_re", "rate_commercial_re", 60, "balloon", {"amort_months": 300}),
    ("commercial_other", "commercial_other", "rate_commercial_other", 40, "level", {}),
    ("other_secured", "other_secured", "rate_other_secured", 40, "level", {}),
    ("leases", "leases", "rate_leases", 36, "level", {}),
)


class CallReport(object):
    """One cycle of NCUA call report data: {cu number: {account: value}}."""

    def __init__(self, path):
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as error:
            raise InputError("%s: not an NCUA call report zip (%s)" % (path, error))
        wanted = set(A.values()) | {"ACCT_998"}
        self.data = {}
        self.names = {}
        self.cycle = None
        with z:
            files = {n.upper(): n for n in z.namelist()}
            if "FOICU.TXT" not in files:
                raise InputError("%s: no FOICU.txt, so not an NCUA call report zip" % path)
            for row in self._rows(z, files["FOICU.TXT"]):
                self.names[row["CU_NUMBER"]] = row
                self.cycle = self.cycle or row.get("CYCLE_DATE")
            for upper, name in sorted(files.items()):
                if not (upper.startswith("FS220") and upper.endswith(".TXT")):
                    continue
                for row in self._rows(z, name):
                    d = self.data.setdefault(row["CU_NUMBER"], {})
                    for key, value in row.items():
                        if key in wanted:
                            try:
                                d[key] = float(value) if value not in ("", None) else 0.0
                            except ValueError:
                                pass
        month, day, year = self.cycle.split(" ")[0].split("/")
        self.as_of = "%04d-%02d-%02d" % (int(year), int(month), int(day))
        self.months = int(month)          # months the year-to-date figures cover

    @staticmethod
    def _rows(z, name):
        reader = csv.reader(io.StringIO(z.read(name).decode("latin-1")))
        head = [h.strip().upper() for h in next(reader)]
        for row in reader:
            yield dict(zip(head, row))

    def get(self, cu, key):
        return self.data.get(str(cu), {}).get(A[key], 0.0)

    def annual(self, cu, key):
        return self.get(cu, key) * 12.0 / self.months

    def name(self, cu):
        row = self.names.get(str(cu), {})
        return "%s (%s, %s)" % (row.get("CU_NAME", "credit union %s" % cu).strip(), row.get("CITY", "").strip(),
                                row.get("STATE", "").strip())

    def search(self, text):
        text = text.lower()
        out = []
        for cu, row in self.names.items():
            if text in row.get("CU_NAME", "").lower() or text == cu or text in row.get("CITY", "").lower():
                out.append((cu, row["CU_NAME"].strip(), row.get("CITY", "").strip(), row.get("STATE", "").strip(),
                            self.get(cu, "assets")))
        return sorted(out, key=lambda x: -x[4])


# --------------------------------------------------------------- building a folder

def build(report, cu, curve=None):
    """(positions rows, assumptions dict, tie-out notes) for credit union `cu`."""
    cu = str(cu)
    if cu not in report.data:
        raise InputError("credit union %s is not in this call report" % cu)
    g = lambda key: report.get(cu, key)  # noqa: E731
    assets = g("assets")
    if assets <= 0:
        raise InputError("credit union %s reports no assets" % cu)
    curve = curve or TREASURY.get(report.as_of)
    if curve is None:
        raise InputError("no Treasury curve for %s is built in; give one with --curve" % report.as_of)
    rows, notes = [], []

    def add(pid, name, product, side, balance, rate=0.0, rate_type="fixed", term=0, amortization="none", **kw):
        # Only the non-earning catch-all lines may be negative: some credit
        # unions report negative other assets or payables (unrealized losses,
        # suspense items), and the totals tie only if those carry through.
        if abs(balance) < 0.5 or (balance < 0 and product not in ("other_assets", "other_liabilities")):
            return
        row = {"id": pid, "name": name, "product": product, "side": side, "balance": round(balance, 2),
               "rate": rate, "rate_type": rate_type, "term_months": term, "amortization": amortization}
        row.update(kw)
        rows.append(row)

    # ---- loans: each line at its most common rate, then all scaled to the reported interest
    loan_rows = []
    covered = 0.0
    for product, key, rate_key, term, amortization, extra in LOANS:
        balance = g(key)
        if balance <= 0:
            continue
        covered += balance
        rate = g(rate_key) / 100.0 or None
        loan_rows.append([product, key, balance, rate, term, amortization, extra])
    total_loans = g("loans")
    if total_loans - covered > 1000:
        loan_rows.append(["other_loans", "other", total_loans - covered, None, 48, "level", {}])
    known = [r for r in loan_rows if r[3]]
    average = (sum(r[2] * r[3] for r in known) / sum(r[2] for r in known)) if known else 6.0
    for r in loan_rows:
        r[3] = r[3] or average
    reported = report.annual(cu, "loan_interest") - report.annual(cu, "interest_refunded")
    implied = sum(r[2] * r[3] / 100.0 for r in loan_rows)
    loan_scale = reported / implied if implied and reported > 0 else 1.0
    for product, key, balance, rate, term, amortization, extra in loan_rows:
        rate = rate * loan_scale
        kind = "fixed"
        variable = extra.get("variable")
        kw = {}
        if variable:
            index, _, reset = variable
            kind = "variable"
            # The margin that makes today's rate: index plus margin equals the calibrated rate.
            kw = {"index": index, "reset_months": reset, "margin": round(rate - _index_rate(index, curve), 3)}
        if amortization == "nonmaturity" and not variable:
            kind = "fixed"
        if "amort_months" in extra:
            kw["amort_months"] = extra["amort_months"]
        add("%s_%s" % (product, key), key.replace("_", " "), product, "asset", balance, round(rate, 3), kind,
            term, amortization, **kw)
    notes.append("Loan rates: the call report's most common rate for each loan type, scaled by %.3f so the "
                 "portfolio earns the $%s a year of loan interest reported (year to date, annualized)." % (
                     loan_scale, "{:,.0f}".format(reported)))

    # ---- investments and cash
    cash = g("cash")
    investments = g("securities")
    bands = [("inv_1", 6, "bullet", "investments"), ("inv_3", 24, "bullet", "investments"),
             ("inv_5", 48, "bullet", "investments"), ("inv_10", 90, "bullet", "investments"),
             ("inv_long", 300, "level", "mortgage_securities")]
    banded = sum(g(b[0]) for b in bands)
    income = report.annual(cu, "investment_income")
    earning = cash + investments
    inv_yield = 100.0 * income / earning if earning else 0.0
    scale = investments / banded if banded else 0.0
    add("cash", "Cash and other deposits", "cash", "asset", cash, 0.0, "none")
    if banded:
        for key, term, amortization, product in bands:
            add("inv_" + key, "Investments, %s" % key.replace("inv_", "maturing band "), product, "asset",
                g(key) * scale, round(inv_yield, 3), "fixed", term, amortization)
    elif investments:
        add("inv", "Investments", "investments", "asset", investments, round(inv_yield, 3), "fixed", 24, "bullet")
    add("other_inv", "Other investments (FHLB stock, CUSOs, other)", "other_investments", "asset",
        g("other_investments"), 0.0, "none")
    notes.append("Investment yield %.2f%%: investment income over cash and securities, annualized; securities "
                 "placed in the call report's maturity bands." % inv_yield)

    # ---- other assets, the allowance, and whatever does not tie
    add("fixed", "Land, buildings and other fixed assets", "fixed_assets", "asset", g("land") + g("fixed"), 0.0, "none")
    add("ncusif", "NCUSIF deposit", "ncusif", "asset", g("ncusif"), 0.0, "none")
    allowance = g("allowance_cecl") or g("allowance")
    listed = sum(r["balance"] for r in rows if r["side"] == "asset")
    other = assets - listed + allowance
    add("other_assets", "Foreclosed, accrued and other assets", "other_assets", "asset", other, 0.0, "none")
    if allowance:
        rows.append({"id": "allowance", "name": "Allowance for credit losses", "product": "other_assets",
                     "side": "asset", "balance": -round(allowance, 2), "rate": 0.0, "rate_type": "none",
                     "term_months": 0, "amortization": "none"})

    # ---- shares, scaled to the reported dividends
    lines = [("regular_shares", "regular", "Regular shares", 0, "nonmaturity"),
             ("share_drafts", "drafts", "Share drafts", 0, "nonmaturity"),
             ("money_market", "money_market", "Money market shares", 0, "nonmaturity"),
             ("ira_shares", "ira", "IRA/Keogh accounts", 0, "nonmaturity"),
             ("regular_shares", "other_shares", "All other shares", 0, "nonmaturity")]
    share_rows = [(p, g(k), name, t, am) for p, k, name, t, am in lines if g(k) > 0]
    cert_total = g("certificates")
    cert_bands = [(g("cert_1"), 7), (g("cert_3"), 22), (g("cert_long"), 44)]
    banded = sum(b for b, _ in cert_bands)
    if cert_total > 0:
        for amount, term in cert_bands:
            if amount > 0:
                share_rows.append(("certificates", amount * cert_total / banded, "Share certificates, about %d months"
                                   % term, term, "bullet"))
        if not banded:
            share_rows.append(("certificates", cert_total, "Share certificates", 12, "bullet"))
    if g("nonmember") > 0:
        share_rows.append(("certificates", g("nonmember"), "Non-member deposits", 12, "bullet"))
    shares = g("shares")
    listed = sum(r[1] for r in share_rows)
    if shares - listed > 1000:
        share_rows.append(("regular_shares", shares - listed, "Other shares and deposits", 0, "nonmaturity"))
    dividends = report.annual(cu, "dividends") + report.annual(cu, "deposit_interest")
    weighted = sum(balance * SHARE_WEIGHTS[p] / 100.0 for p, balance, _, _, _ in share_rows)
    share_scale = dividends / weighted if weighted else 1.0
    for i, (product, balance, name, term, amortization) in enumerate(share_rows):
        add("sh%d_%s" % (i, product), name, product, "liability", balance,
            round(SHARE_WEIGHTS[product] * share_scale, 3), "administered" if amortization == "nonmaturity"
            else "fixed", term, amortization)
    notes.append("Share rates: not in the call report. Typical relative rates (regular %.2f, drafts %.2f, money "
                 "market %.2f, IRA %.2f, certificates %.2f) scaled by %.3f so shares cost the $%s a year of "
                 "dividends reported." % (SHARE_WEIGHTS["regular_shares"], SHARE_WEIGHTS["share_drafts"],
                                         SHARE_WEIGHTS["money_market"], SHARE_WEIGHTS["ira_shares"],
                                         SHARE_WEIGHTS["certificates"], share_scale, "{:,.0f}".format(dividends)))

    # ---- borrowings
    borrowed = g("borrowings")
    cost = report.annual(cu, "borrowing_interest")
    rate = 100.0 * cost / borrowed if borrowed else 0.0
    bands = [(g("borrow_1"), 6), (g("borrow_3"), 24), (g("borrow_long"), 48)]
    banded = sum(b for b, _ in bands)
    for amount, term in bands:
        if amount > 0:
            add("borrow_%d" % term, "Borrowings, about %d months" % term, "borrowings", "liability",
                amount * borrowed / banded, round(rate, 3), "fixed", term, "bullet")
    if borrowed and not banded:
        add("borrow", "Borrowings", "borrowings", "liability", borrowed, round(rate, 3), "fixed", 24, "bullet")
    liabilities = g("liabilities") or (shares + borrowed + g("payables"))
    listed = sum(r["balance"] for r in rows if r["side"] == "liability")
    add("other_liabilities", "Accounts payable and other liabilities", "other_liabilities", "liability",
        liabilities - listed, 0.0, "none")

    # ---- the settings
    products = {k: dict(v) for k, v in PRODUCTS.items() if any(r["product"] == k for r in rows)}
    net_charge_offs = report.annual(cu, "charge_offs") - report.annual(cu, "recoveries")
    modeled = sum(r["balance"] * products[r["product"]].get("charge_off", 0.0) / 100.0 for r in rows
                  if r["side"] == "asset" and r["balance"] > 0)
    if modeled > 0 and net_charge_offs > 0:
        factor = net_charge_offs / modeled
        for spec in products.values():
            if spec.get("charge_off"):
                spec["charge_off"] = round(spec["charge_off"] * factor, 4)
        notes.append("Charge-off rates: Keel's defaults scaled by %.3f to the $%s a year of net charge-offs "
                     "reported." % (factor, "{:,.0f}".format(net_charge_offs)))
    capacity = []
    if g("fhlb_line") > borrowed:
        capacity.append({"name": "FHLB line, unused (call report)", "capacity": round(g("fhlb_line") - borrowed, -3)})
    if g("clf_capacity") > 0:
        capacity.append({"name": "Central Liquidity Facility capacity (call report)", "capacity": round(g("clf_capacity"), -3)})
    raw = {
        "notes": {
            "about": "%s, from NCUA's call report for %s. Balances and totals are the credit union's own; "
                     "rates are calibrated to its reported income and expense; behaviour and terms are Keel's "
                     "defaults." % (report.name(cu), report.as_of),
            "source": "NCUA 5300 call report quarterly data, cycle %s, charter %s." % (report.as_of, cu),
            "calibration": " ".join(notes),
            "defaults": "Prepayment, decay, betas, remaining terms, costs and capital weights are Keel's "
                        "credit-union defaults, not the credit union's own. Replace them with its data before "
                        "relying on the rate-risk results.",
        },
        "as_of": report.as_of, "curve": {str(k): v for k, v in sorted(curve.items())},
        "indexes": {"PRIME": {"tenor_months": 1, "spread": 3.00}, "SOFR": {"tenor_months": 1, "spread": 0.0},
                    "TSY_1Y": {"tenor_months": 12, "spread": 0.0}},
        "rate_floor": 0.0, "short_tenor_months": 1, "horizon_months": 60, "nev_max_months": 360,
        "fee_income": round(report.annual(cu, "noninterest_income"), -3),
        "operating_expense": round(report.annual(cu, "noninterest_expense"), -3), "expense_growth": 3.0,
        "cash_minimum": round(min(cash, assets * 0.02), -3), "overnight_spread": 0.25,
        "products": products,
        "extra_scenarios": [{"name": "ramp +200", "shock_bp": 200, "ramp_months": 12},
                            {"name": "ramp -200", "shock_bp": -200, "ramp_months": 12}],
        "liquidity": {"stress_months": 3, "contingent": capacity},
    }
    ties = {"assets": (assets, sum(r["balance"] for r in rows if r["side"] == "asset")),
            "liabilities": (liabilities, sum(r["balance"] for r in rows if r["side"] == "liability")),
            "loan_interest": (reported, sum(r["balance"] * r["rate"] / 100.0 for r in rows
                                            if r["side"] == "asset" and r["product"] not in
                                            ("cash", "investments", "mortgage_securities")
                                            and r["rate_type"] != "none")),
            "dividends": (dividends, sum(r["balance"] * r["rate"] / 100.0 for r in rows
                                         if r["side"] == "liability" and r["product"] != "borrowings"))}
    return rows, raw, ties


def _index_rate(index, curve):
    short = curve[1]
    return short + 3.00 if index == "PRIME" else curve.get(12, short) if index == "TSY_1Y" else short


def write(folder, rows, raw, peer=None):
    os.makedirs(folder, exist_ok=True)
    head = ["id", "name", "product", "side", "balance", "rate", "rate_type", "index", "margin", "reset_months",
            "term_months", "amortization", "floor", "cap", "amort_months"]
    with open(os.path.join(folder, "positions.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(head)
        for r in rows:
            w.writerow(["" if r.get(h) in (None, 0) and h in ("term_months", "reset_months", "amort_months") else
                        r.get(h, "") for h in head])
    for stale in ("assumptions.xlsx",):
        if os.path.isfile(os.path.join(folder, stale)):
            os.remove(os.path.join(folder, stale))
    with open(os.path.join(folder, "assumptions.json"), "w", encoding="utf-8") as handle:
        json.dump(raw, handle, indent=2)
    if peer is not None:
        with open(os.path.join(folder, "peers.json"), "w", encoding="utf-8") as handle:
            json.dump(peer, handle, indent=2)


# --------------------------------------------------------------- peers

RATIOS = (  # key, label, better when, function of (report, cu) -> percent or None
    ("net_worth_ratio", "Net worth ratio", "higher",
     lambda r, c: 100 * r.get(c, "net_worth") / r.get(c, "assets")),
    ("roa", "Return on assets", "higher", lambda r, c: 100 * r.annual(c, "net_income") / r.get(c, "assets")),
    ("nim", "Net interest margin", "higher",
     lambda r, c: 100 * (r.annual(c, "interest_income") - r.annual(c, "interest_expense")) / r.get(c, "assets")),
    ("yield_on_loans", "Yield on loans", "higher",
     lambda r, c: 100 * (r.annual(c, "loan_interest") - r.annual(c, "interest_refunded")) / r.get(c, "loans")
     if r.get(c, "loans") else None),
    ("cost_of_funds", "Cost of funds", "lower",
     lambda r, c: 100 * r.annual(c, "interest_expense") / (r.get(c, "shares") + r.get(c, "borrowings"))
     if r.get(c, "shares") else None),
    ("efficiency", "Efficiency ratio", "lower",
     lambda r, c: 100 * r.annual(c, "noninterest_expense") / (r.annual(c, "interest_income")
                                                              - r.annual(c, "interest_expense")
                                                              + r.annual(c, "noninterest_income"))
     if (r.annual(c, "interest_income") - r.annual(c, "interest_expense") + r.annual(c, "noninterest_income")) > 0
     else None),
    ("loans_to_shares", "Loans to shares", None,
     lambda r, c: 100 * r.get(c, "loans") / r.get(c, "shares") if r.get(c, "shares") else None),
    ("liquid_to_assets", "Cash and investments to assets", None,
     lambda r, c: 100 * (r.get(c, "cash") + r.get(c, "securities")) / r.get(c, "assets")),
    ("certificates_to_shares", "Certificates to shares", None,
     lambda r, c: 100 * r.get(c, "certificates") / r.get(c, "shares") if r.get(c, "shares") else None),
    ("long_fixed_mortgages", "Fixed first mortgages over 15 years, to assets", "lower",
     lambda r, c: 100 * r.get(c, "mtg_long") / r.get(c, "assets")),
    ("borrowings_to_assets", "Borrowings to assets", None,
     lambda r, c: 100 * r.get(c, "borrowings") / r.get(c, "assets")),
    ("net_charge_offs", "Net charge-offs to loans", "lower",
     lambda r, c: 100 * (r.annual(c, "charge_offs") - r.annual(c, "recoveries")) / r.get(c, "loans")
     if r.get(c, "loans") else None),
)
PEER_GROUPS = {"1": "under $2M", "2": "$2M to $10M", "3": "$10M to $50M", "4": "$50M to $100M",
               "5": "$100M to $500M", "6": "$500M and over"}


def peers(report, cu):
    """The credit union's ratios against its NCUA peer group (by assets)."""
    cu = str(cu)
    group = str(report.names.get(cu, {}).get("PEER_GROUP", "")).strip()
    members = [c for c, row in report.names.items() if str(row.get("PEER_GROUP", "")).strip() == group
               and report.get(c, "assets") > 0]
    out = []
    for key, label, better, f in RATIOS:
        values = []
        for c in members:
            try:
                v = f(report, c)
            except ZeroDivisionError:
                v = None
            if v is not None:
                values.append(v)
        try:
            mine = f(report, cu)
        except ZeroDivisionError:
            mine = None
        if mine is None or not values:
            continue
        values.sort()
        n = len(values)
        rank = sum(1 for v in values if v < mine) + 0.5 * sum(1 for v in values if v == mine)
        out.append({"key": key, "label": label, "better": better, "value": mine,
                    "p25": values[n // 4], "median": values[n // 2], "p75": values[(3 * n) // 4],
                    "percentile": 100.0 * rank / n})
    return {"cycle": report.as_of, "cu": cu, "name": report.name(cu), "peer_group": PEER_GROUPS.get(group, group),
            "count": len(members), "ratios": out}
