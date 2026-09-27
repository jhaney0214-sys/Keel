"""What the samples need beyond the core files: product costs and capital
weights, an actuals file to compare with the budget, saved ad hoc queries,
new-product proposals, and a synthetic community bank.

Run by make_samples.py after the credit unions are written. Every figure is
invented; none describes a real institution.
"""

import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EXAMPLES = os.path.join(ROOT, "examples")

# Direct costs, fees and capital weights by product, percent. Servicing and
# fees are the product's own (collections, statements, interchange), not a
# share of overhead: most operating expense stays unallocated, as it does in
# practice. Weights follow the shape of NCUA's risk-based capital rule
# (12 CFR 702.104): current consumer loans 75%, first-lien residential 50%,
# commercial 100%, Treasuries 0%, agencies 20%.
COSTS = {
    "cash": {"risk_weight": 0.0}, "ncusif": {"risk_weight": 0.0}, "fixed_assets": {"risk_weight": 100.0},
    "other_assets": {"risk_weight": 100.0}, "fhlb_stock": {"risk_weight": 100.0}, "cuso": {"risk_weight": 100.0},
    "treasuries": {"risk_weight": 0.0, "servicing_cost": 0.02},
    "agency_bullets": {"risk_weight": 20.0, "servicing_cost": 0.02},
    "agency_callables": {"risk_weight": 20.0, "servicing_cost": 0.02},
    "agency_mbs": {"risk_weight": 20.0, "servicing_cost": 0.03},
    "agency_cmo": {"risk_weight": 20.0, "servicing_cost": 0.03},
    "municipals": {"risk_weight": 20.0, "servicing_cost": 0.03},
    "invest_cds": {"risk_weight": 20.0, "servicing_cost": 0.02},
    "new_auto": {"risk_weight": 75.0, "servicing_cost": 0.60, "fee_yield": 0.10, "origination_cost": 0.75},
    "used_auto": {"risk_weight": 75.0, "servicing_cost": 0.70, "fee_yield": 0.15, "origination_cost": 0.75},
    "first_mortgage": {"collateral_value": 75.0, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "first_mortgage_15": {"collateral_value": 75.0, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "arm_mortgage": {"collateral_value": 75.0, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "heloc": {"collateral_value": 50.0, "risk_weight": 100.0, "servicing_cost": 0.50, "fee_yield": 0.10, "origination_cost": 0.50},
    "credit_card": {"risk_weight": 75.0, "servicing_cost": 2.50, "fee_yield": 2.00},
    "unsecured": {"risk_weight": 75.0, "servicing_cost": 1.50, "fee_yield": 0.25, "origination_cost": 1.00},
    "commercial_re": {"collateral_value": 60.0, "risk_weight": 100.0, "servicing_cost": 0.40, "fee_yield": 0.10, "origination_cost": 0.75},
    "regular_shares": {"servicing_cost": 0.60, "fee_yield": 0.35},
    "share_drafts": {"servicing_cost": 1.60, "fee_yield": 2.20},
    "money_market": {"servicing_cost": 0.30, "fee_yield": 0.05},
    "ira_shares": {"servicing_cost": 0.30},
    "certificates": {"servicing_cost": 0.15},
}

QUERIES = {
    "mid-cu": [
        {"name": "Loans by rate band", "table": "positions", "by": ["product", "rate_band"],
         "measures": ["count", "sum balance", "wavg rate balance", "wavg spread balance"],
         "where": ["side = asset", "risk_weight >= 50", "product != fixed_assets", "product != fhlb_stock",
                   "product != cuso", "product != other_assets"],
         "sort": "-sum balance", "limit": 25},
        {"name": "Delinquency by loan type", "table": "loans", "by": ["product_code"],
         "measures": ["count", "sum current_balance", "wavg rate current_balance", "avg days_delinquent"],
         "where": ["days_delinquent >= 30"], "sort": "-sum current_balance"},
        {"name": "Certificates maturing by term", "table": "certificates", "by": ["term_months"],
         "measures": ["count", "sum balance", "wavg rate balance"], "sort": "term_months"},
    ],
    "large-cu": [
        {"name": "Mortgages by rate band", "table": "positions", "by": ["rate_band"],
         "measures": ["count", "sum balance", "wavg ftp_rate balance", "wavg spread balance"],
         "where": ["product in first_mortgage,first_mortgage_15,arm_mortgage"], "sort": "rate_band"},
        {"name": "Delinquency by loan type", "table": "loans", "by": ["product_code"],
         "measures": ["count", "sum current_balance", "avg days_delinquent"], "where": ["days_delinquent >= 30"],
         "sort": "-sum current_balance"},
    ],
}

PROPOSALS = {
    "green-auto.json": {
        "name": "72-month green auto loan", "product": "green_auto", "like": "new_auto", "side": "asset",
        "rate": 5.49, "term_months": 72, "amortization": "level", "launch_balance": 10000000, "growth": 40,
        "average_size": 32000,
        "behaviour": {"cpr": 16, "charge_off": 0.45, "servicing_cost": 0.55, "origination_cost": 1.0, "risk_weight": 75}},
    "cd-special.json": {
        "name": "13-month certificate special", "product": "cd_special", "like": "certificates", "side": "liability",
        "rate": 4.35, "term_months": 13, "amortization": "bullet", "launch_balance": 25000000, "growth": 0,
        "average_size": 28000, "behaviour": {"servicing_cost": 0.15}},
}


def _months(n=12):
    """YYYY-MM for plan months 1..n after the samples' analysis date, 2026-06-30."""
    out, y, m = [], 2026, 6
    for _ in range(n):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        out.append("%04d-%02d" % (y, m))
    return out


def budget_spec(name, spec):
    """The mid-size sample budgets the way a finance team does: on a rate
    forecast (the Fed cutting 75bp over a year), with monthly loan
    production and deposit offering rates, and expenses by line."""
    if name != "mid-cu":
        return {}
    months = _months()
    fee, opex = spec["fee_income"], spec["operating_expense"]
    drivers = []
    for i, month in enumerate(months):
        drivers += [{"product": "new_auto", "month": month, "volume": 1650000 if i % 12 not in (2, 3, 4) else 1950000},
                    {"product": "used_auto", "month": month, "volume": 3000000 if i % 12 not in (2, 3, 4) else 3400000},
                    {"product": "first_mortgage", "month": month, "volume": 850000}]
    drivers += [{"product": "certificates", "month": months[0], "rate": 4.05},
                {"product": "certificates", "month": months[3], "rate": 3.85},
                {"product": "certificates", "month": months[6], "rate": 3.65},
                {"product": "money_market", "month": months[0], "rate": 2.80},
                {"product": "money_market", "month": months[3], "rate": 2.60}]
    return {
        "base_case": "forecast",
        "rate_forecast": [
            {"month": months[2], "tenor_months": 1, "rate": 3.75},
            {"month": months[5], "tenor_months": 1, "rate": 3.50}, {"month": months[5], "tenor_months": 120, "rate": 4.05},
            {"month": months[11], "tenor_months": 1, "rate": 3.25}, {"month": months[11], "tenor_months": 12, "rate": 3.30},
            {"month": months[11], "tenor_months": 120, "rate": 4.10},
            {"month": 24, "tenor_months": 1, "rate": 3.25}, {"month": 24, "tenor_months": 120, "rate": 4.25}],
        "drivers": drivers,
        "noninterest": [
            {"line": "Interchange income", "kind": "income", "annual": round(fee * 0.45, -3), "growth": 3.0},
            {"line": "Service charges and fees", "kind": "income", "annual": round(fee * 0.30, -3), "growth": 1.0},
            {"line": "Loan fees", "kind": "income", "annual": round(fee * 0.10, -3), "growth": 2.0},
            {"line": "Other income", "kind": "income", "annual": round(fee * 0.15, -3), "growth": 0.0},
            {"line": "Salaries and benefits", "kind": "expense", "annual": round(opex * 0.52, -3), "growth": 3.5},
            {"line": "Occupancy", "kind": "expense", "annual": round(opex * 0.11, -3), "growth": 2.0},
            {"line": "Data processing", "kind": "expense", "annual": round(opex * 0.14, -3), "growth": 4.0},
            {"line": "Marketing", "kind": "expense", "annual": round(opex * 0.05, -3), "growth": 2.0},
            {"line": "Professional services", "kind": "expense", "annual": round(opex * 0.05, -3), "growth": 2.0},
            {"line": "Other operating expense", "kind": "expense", "annual": round(opex * 0.13, -3), "growth": 2.0},
            {"line": "Two new lending officers", "kind": "expense", "annual": 240000, "growth": 3.5,
             "start_month": months[3]}],
    }


def add_costs(products):
    for name, costs in COSTS.items():
        if name in products:
            products[name].update(costs)
    return products


def write_queries():
    for name, specs in QUERIES.items():
        folder = os.path.join(EXAMPLES, name, "queries")
        os.makedirs(folder, exist_ok=True)
        for old in os.listdir(folder):
            if old.endswith(".json"):
                os.remove(os.path.join(folder, old))
        for spec in specs:
            slug = spec["name"].lower().replace(" ", "-")
            with open(os.path.join(folder, slug + ".json"), "w", encoding="utf-8") as handle:
                json.dump(spec, handle, indent=2)


def write_proposals():
    folder = os.path.join(EXAMPLES, "proposals")
    os.makedirs(folder, exist_ok=True)
    for name, spec in PROPOSALS.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
            json.dump(spec, handle, indent=2)


def write_actuals(name="mid-cu", months=3):
    """Three months of actuals near the budget, with the kind of misses a real
    quarter has: used autos ahead on volume, certificates repricing faster,
    money market running off, fees light and expenses heavy."""
    from keel import budget, engine
    from keel.__main__ import load
    folder = os.path.join(EXAMPLES, name)
    for stale in ("actuals.csv", "actuals.xlsx"):
        if os.path.isfile(os.path.join(folder, stale)):
            os.remove(os.path.join(folder, stale))
    positions, a, _, _ = load(folder)
    b = budget.build(positions, a, engine.going_concern(positions, a, a.scenarios[0]))
    rng = random.Random(5)
    misses = {"used_auto": (0.025, -0.0010), "new_auto": (-0.015, 0.0), "certificates": (0.010, 0.0020),
              "money_market": (-0.040, 0.0005), "first_mortgage": (0.005, 0.0), "credit_card": (0.0, -0.0040)}
    rows = []
    for t in range(months):
        month = b["labels"][t]
        for p in b["products"]:
            if p["product"] == "cash" or p["average"][t] <= 0:
                continue
            volume, rate = misses.get(p["product"], (rng.uniform(-0.004, 0.004), 0.0))
            grow = (t + 1) / months
            balance = p["average"][t] * (1 + volume * grow)
            yield_ = p["interest"][t] * 12 / p["average"][t] + rate * grow
            rows.append([month, p["product"], round(balance, 2), round(balance * yield_ / 12, 2)])
        income = b["income"][t]
        rows.append([month, "fee_income", "", round(income["fee_income"] * 0.96, 2)])
        rows.append([month, "operating_expense", "", round(income["operating_expense"] * 1.02, 2)])
        rows.append([month, "credit_losses", "", round(income["credit_losses"] * 1.15, 2)])
    import csv
    with open(os.path.join(folder, "actuals.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["month", "line", "average_balance", "amount"])
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------- the bank

BANK_PRODUCTS = {
    "cash": {"risk_weight": 0.0}, "fed_funds_sold": {"new_term": 1, "new_amortization": "bullet", "liquid": True,
                                                     "risk_weight": 20.0},
    "premises": {"risk_weight": 100.0}, "other_assets": {"risk_weight": 100.0},
    "treasuries": {"new_term": 24, "new_amortization": "bullet", "liquid": True, "haircut": 2.0, "risk_weight": 0.0,
                   "servicing_cost": 0.02},
    "agency_mbs": {"cpr": 7.0, "cpr_per_100bp": 5.0, "cpr_floor": 4.0, "cpr_cap": 40.0, "new_term": 360,
                   "new_amortization": "level", "spread": 0.60, "discount_spread": 0.60, "liquid": True,
                   "haircut": 6.0, "risk_weight": 20.0, "servicing_cost": 0.03},
    "municipals": {"new_term": 120, "new_amortization": "bullet", "spread": 0.30, "discount_spread": 0.40,
                   "liquid": True, "haircut": 10.0, "risk_weight": 20.0, "servicing_cost": 0.03},
    "commercial_industrial": {"new_term": 36, "growth": 6.0, "charge_off": 0.35, "discount_spread": 2.75,
                              "risk_weight": 100.0, "servicing_cost": 0.60, "fee_yield": 0.30,
                              "origination_cost": 0.50},
    "commercial_re": {"collateral_value": 60.0, "cpr": 6.0, "cpr_per_100bp": 2.0, "new_term": 60, "new_amortization": "balloon",
                      "spread": 2.50, "discount_spread": 2.50, "growth": 5.0, "charge_off": 0.20, "risk_weight": 100.0,
                      "servicing_cost": 0.35, "fee_yield": 0.15, "origination_cost": 0.60},
    "construction": {"new_term": 18, "growth": 3.0, "charge_off": 0.50, "discount_spread": 3.25,
                     "risk_weight": 150.0, "servicing_cost": 0.80, "fee_yield": 0.50, "origination_cost": 0.75},
    "residential_mortgage": {"collateral_value": 75.0, "cpr": 7.0, "cpr_per_100bp": 6.0, "cpr_floor": 3.0, "cpr_cap": 45.0, "new_term": 360,
                             "new_amortization": "level", "spread": 1.80, "discount_spread": 1.80, "growth": 2.0,
                             "charge_off": 0.05, "risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05,
                             "origination_cost": 1.00},
    "consumer": {"cpr": 15.0, "new_term": 60, "new_amortization": "level", "spread": 4.00, "discount_spread": 4.00,
                 "growth": 1.0, "charge_off": 1.20, "risk_weight": 100.0, "servicing_cost": 1.00,
                 "fee_yield": 0.20, "origination_cost": 0.75},
    "noninterest_checking": {"runoff": 14.0, "runoff_per_100bp": 2.0, "discount_spread": 0.30, "growth": 2.0,
                             "stress_runoff": 15.0, "servicing_cost": 1.40, "fee_yield": 1.10},
    "interest_checking": {"runoff": 12.0, "runoff_per_100bp": 1.5, "beta": 20.0, "rate_floor": 0.05,
                          "discount_spread": 0.30, "growth": 2.0, "stress_runoff": 12.0, "servicing_cost": 0.90,
                          "fee_yield": 0.40},
    "savings": {"runoff": 10.0, "runoff_per_100bp": 1.5, "beta": 15.0, "rate_floor": 0.05, "discount_spread": 0.30,
                "growth": 1.0, "stress_runoff": 10.0, "servicing_cost": 0.50, "fee_yield": 0.10},
    "money_market": {"runoff": 24.0, "runoff_per_100bp": 3.0, "beta": 60.0, "rate_floor": 0.25,
                     "discount_spread": 0.30, "growth": 3.0, "stress_runoff": 25.0, "servicing_cost": 0.30},
    "time_deposits": {"new_term": 12, "new_amortization": "bullet", "spread": -0.10, "discount_spread": -0.10,
                      "growth": 3.0, "stress_runoff": 15.0, "servicing_cost": 0.15},
    "brokered_cds": {"new_term": 12, "new_amortization": "bullet", "spread": 0.15, "discount_spread": 0.15,
                     "stress_runoff": 0.0, "servicing_cost": 0.05},
    "borrowings": {"discount_spread": 0.30},
    "other_liabilities": {},
}


def write_bank():
    """Lakeshore Community Bank (synthetic): $1.2B, commercial-heavy, with
    prime-based C&I and construction lines and a brokered-CD sleeve."""
    from keel import settings, xlsx
    from make_samples import AS_OF, CURVE
    folder = os.path.join(EXAMPLES, "community-bank")
    os.makedirs(folder, exist_ok=True)
    rng = random.Random(41)
    assets = 1.2e9
    rows = []

    def add(pid, name, product, side, balance, rate, rate_type="fixed", term=0, amortization="none", index="",
            margin="", reset="", amort_months="", floor=""):
        rows.append([pid, name, product, side, round(balance, 2), "%.3f" % rate if rate != "" else "", rate_type,
                     index, margin, reset, term or "", amortization, floor, "", amort_months])

    add("cash", "Cash and due from banks", "cash", "asset", 0.035 * assets, 0.0, "none")
    add("ffs", "Fed funds sold", "fed_funds_sold", "asset", 0.02 * assets, 4.05, "fixed", 1, "bullet")
    for i, (share, term, rate) in enumerate(((0.03, 8, 4.35), (0.03, 20, 3.95), (0.02, 44, 3.60))):
        add("tsy%d" % i, "US Treasuries", "treasuries", "asset", share * assets, rate, "fixed", term, "bullet")
    for i, (share, term, rate) in enumerate(((0.05, 300, 2.40), (0.04, 330, 3.10), (0.03, 350, 5.10))):
        add("mbs%d" % i, "Agency MBS", "agency_mbs", "asset", share * assets, rate, "fixed", term, "level")
    add("muni", "Municipal bonds", "municipals", "asset", 0.03 * assets, 2.60, "fixed", 96, "bullet")
    for i in range(8):
        add("ci%d" % i, "C&I lines, prime-based", "commercial_industrial", "asset", 0.16 * assets / 8,
            7.25 + rng.uniform(-0.5, 0.75), "variable", rng.choice((12, 24, 36)), "bullet", "PRIME",
            "%.2f" % rng.uniform(-0.25, 1.0), 1, floor="4.00")
    for i in range(10):
        term = rng.choice((12, 24, 36, 48, 60))
        add("cre%d" % i, "Commercial real estate", "commercial_re", "asset", 0.30 * assets / 10,
            rng.uniform(4.25, 7.25), "fixed", term, "balloon", amort_months=term + 240)
    for i in range(4):
        add("con%d" % i, "Construction, SOFR-based", "construction", "asset", 0.06 * assets / 4,
            7.10 + rng.uniform(-0.3, 0.3), "variable", rng.choice((12, 18, 24)), "bullet", "SOFR",
            "%.2f" % rng.uniform(2.75, 3.50), 1)
    for i in range(6):
        add("res%d" % i, "Residential mortgages", "residential_mortgage", "asset", 0.12 * assets / 6,
            rng.uniform(3.0, 6.8), "fixed", rng.choice((240, 290, 330, 350)), "level")
    for i in range(3):
        add("cons%d" % i, "Consumer loans", "consumer", "asset", 0.03 * assets / 3, rng.uniform(7.5, 10.5), "fixed",
            rng.choice((24, 40, 52)), "level")
    add("prem", "Premises and equipment", "premises", "asset", 0.012 * assets, 0.0, "none")
    add("alll", "Allowance for credit losses", "other_assets", "asset", -0.0125 * 0.67 * assets, 0.0, "none")
    add("oth", "Other assets", "other_assets", "asset", 0.02 * assets, 0.0, "none")
    total_assets = sum(r[4] for r in rows)
    equity = 0.095 * total_assets
    add("dda", "Noninterest-bearing checking", "noninterest_checking", "liability", 0.22 * total_assets, 0.0,
        "administered", amortization="nonmaturity")
    add("now", "Interest checking", "interest_checking", "liability", 0.14 * total_assets, 0.35, "administered",
        amortization="nonmaturity")
    add("sav", "Savings", "savings", "liability", 0.10 * total_assets, 0.25, "administered", amortization="nonmaturity")
    add("mmda", "Money market deposits", "money_market", "liability", 0.16 * total_assets, 2.85, "administered",
        amortization="nonmaturity")
    for i, term in enumerate((3, 6, 9, 12, 18, 24)):
        add("td%d" % i, "Time deposits", "time_deposits", "liability", 0.14 * total_assets / 6,
            4.10 - 0.05 * i + rng.uniform(-0.1, 0.1), "fixed", term, "bullet")
    add("bcd", "Brokered CDs", "brokered_cds", "liability", 0.04 * total_assets, 4.45, "fixed", 9, "bullet")
    add("fhlb", "FHLB advances", "borrowings", "liability", 0.04 * total_assets, 4.30, "fixed", 30, "bullet")
    liabilities = sum(r[4] for r in rows if r[3] == "liability")
    add("othl", "Other liabilities", "other_liabilities", "liability", total_assets - liabilities - equity, 0.0,
        "none")
    import csv
    head = ["id", "name", "product", "side", "balance", "rate", "rate_type", "index", "margin", "reset_months",
            "term_months", "amortization", "floor", "cap", "amort_months"]
    with open(os.path.join(folder, "positions.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(head)
        w.writerows(rows)
    spec = {
        "institution": "bank", "tax_rate": 21.0, "target_capital": 10.5, "hurdle_rate": 15.0,
        "notes": {"about": "Lakeshore Community Bank (synthetic). A $1.2B commercial bank: prime-based C&I, "
                           "CRE balloons, construction, and a brokered-CD sleeve. Every figure is invented by "
                           "tools/sample_extras.py; none describes a real institution.",
                  "rates": "Percent. Risk weights follow the shape of the US standardized approach."},
        "as_of": AS_OF.isoformat(), "curve": CURVE,
        "indexes": {"PRIME": {"tenor_months": 1, "spread": 3.25}, "SOFR": {"tenor_months": 1, "spread": 0.0}},
        "rate_floor": 0.0, "short_tenor_months": 1, "horizon_months": 60, "nev_max_months": 360,
        "fee_income": round(total_assets * 0.0060, -3), "operating_expense": round(total_assets * 0.0235, -3),
        "expense_growth": 3.0, "cash_minimum": round(total_assets * 0.02, -3), "overnight_spread": 0.25,
        "products": BANK_PRODUCTS,
        "extra_scenarios": [{"name": "ramp +200", "shock_bp": 200, "ramp_months": 12},
                            {"name": "ramp -200", "shock_bp": -200, "ramp_months": 12},
                            {"name": "flattener", "shape": {"1": 200, "24": 100, "120": 0}},
                            {"name": "steepener", "shape": {"1": 0, "24": 50, "120": 200}}],
        "limits": {"nii_decline_300": 15.0, "nii_decline_200": 10.0, "nev_decline_300": 30.0, "nev_ratio_min": 7.0,
                   "net_worth_min": 8.0, "liquid_to_shares_min": 12.0, "loans_to_shares_max": 95.0,
                   "borrowings_to_assets_max": 15.0, "survival_months_min": 6.0, "capital_to_rwa_min": 10.5,
                   "uninsured_shares_max": 50.0},
        "liquidity": {"stress_months": 3, "contingent": [
            {"name": "FHLB unused borrowing capacity", "capacity": round(total_assets * 0.10, -3), "secured": True},
            {"name": "Federal Reserve discount window (pledged collateral)", "capacity": round(total_assets * 0.05, -3),
             "secured": True}]},
    }
    for stale in ("assumptions.json", "assumptions.xlsx"):
        if os.path.isfile(os.path.join(folder, stale)):
            os.remove(os.path.join(folder, stale))
    xlsx.write_workbook(os.path.join(folder, "assumptions.xlsx"), settings.to_workbook(spec))
    return len(rows)


def run():
    write_queries()
    write_proposals()
    print("%-14s %d rows of actuals" % ("mid-cu", write_actuals()))
    print("%-14s %d months of product history, %d account-months" % (("mid-cu",) + write_deposit_history()))
    print("%-14s %d members" % ("mid-cu", write_depositors("mid-cu", 46000, 81)))
    print("%-14s %d members" % ("large-cu", write_depositors("large-cu", 150000, 83, sigma=1.6)))
    print("%-14s %d positions" % ("community-bank", write_bank()))
    print("%-14s %d depositors" % ("community-bank", write_depositors("community-bank", 18000, 85, sigma=2.3)))
    print("%-14s %d positions, and its June run in history/" % ("backtest-cu", write_backtest()))


# --------------------------------------------------------------- the back-test

def write_backtest():
    """Summit Valley Credit Union (synthetic): the hand-written sample's book,
    run as of 2026-06-30 and saved to history, then the same credit union a
    quarter later. September's book is June's forecast for September with the
    misses a real quarter has: money market running off faster than assumed,
    certificates repricing faster than their beta, auto loans ahead of plan,
    and the short rate 25bp below where June assumed it would be."""
    import copy
    import csv
    from keel import history, model, results
    source = os.path.join(EXAMPLES, "sample-cu")
    folder = os.path.join(EXAMPLES, "backtest-cu")
    if os.path.isdir(os.path.join(folder, "history")):
        for old in os.listdir(os.path.join(folder, "history")):
            os.remove(os.path.join(folder, "history", old))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(source, "assumptions.json"), encoding="utf-8") as handle:
        june_raw = json.load(handle)
    june_raw["notes"] = {"about": "Summit Valley Credit Union (synthetic). The hand-written sample's book a quarter "
                                  "on, with its June run in history/ for the back-test. Every figure is invented by "
                                  "tools/sample_extras.py; none describes a real institution."}
    june = model.parse_assumptions(june_raw)
    positions = model.read_positions(os.path.join(source, "positions.csv"))
    r = results.compute(positions, june, "Summit Valley Credit Union (synthetic)", assumption_tests=False)
    history.save(folder, history.snapshot(r))

    k = 3
    run = r["base_run"]
    misses = {"money_market": -0.06, "certificates": 0.02, "new_auto": 0.04, "used_auto": 0.03, "regular_shares": -0.01,
              "first_mortgage": 0.01}
    rate_misses = {"certificates": 0.0020, "money_market": 0.0015}
    by_product = {}
    for p in positions:
        by_product.setdefault(p.product, []).append(p)
    september = []
    for product, group in by_product.items():
        if product == "cash":
            continue
        total = sum(p.balance for p in group)
        target = run[k - 1].balances.get(product, total) * (1.0 + misses.get(product, 0.0))
        spec = june.products[product]
        forecast_rate = None
        if run[k - 1].balances.get(product):
            average = (run[k - 2].balances[product] + run[k - 1].balances[product]) / 2.0
            forecast_rate = 12.0 * run[k - 1].interest.get(product, 0.0) / average if average else None
        for p in group:
            q = copy.copy(p)
            q.balance = round(p.balance * (target / total if total else 1.0), 2)
            if q.term_months:
                q.term_months = q.term_months - k if q.term_months > k else max(spec.new_term, 1)
            if q.rate_type == "administered" and forecast_rate is not None:
                q.rate = forecast_rate + rate_misses.get(product, 0.0)
            september.append(q)
    equity = run[k - 1].equity - 150000.0          # a little below plan: fees light, expenses heavy
    assets = sum(p.balance for p in september if p.side == "asset")
    liabilities = sum(p.balance for p in september if p.side == "liability")
    cash = [p for p in positions if p.product == "cash"][0]
    september.insert(0, model.Position(id=cash.id, name=cash.name, product="cash", side="asset",
                                       balance=round(liabilities + equity - assets, 2), rate=0.0, rate_type="none",
                                       amortization="none"))
    head = ["id", "name", "product", "side", "balance", "rate", "rate_type", "index", "margin", "reset_months",
            "term_months", "amortization", "floor", "cap"]
    with open(os.path.join(folder, "positions.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(head)
        for p in september:
            w.writerow([p.id, p.name, p.product, p.side, "%.2f" % p.balance, "%.3f" % (100 * p.rate), p.rate_type,
                        p.index, "%.3f" % (100 * p.margin) if p.margin else "", p.reset_months or "",
                        p.term_months or "", p.amortization,
                        "" if p.floor is None else "%.3f" % (100 * p.floor), "" if p.cap is None else "%.3f" % (100 * p.cap)])
    sept_raw = copy.deepcopy(june_raw)
    sept_raw["as_of"] = "2026-09-30"
    sept_raw["curve"] = {t: round(v - (0.25 if float(t) <= 12 else 0.10), 2) for t, v in june_raw["curve"].items()}
    sept_raw["products"]["money_market"]["beta"] = 65.0
    sept_raw["products"]["money_market"]["runoff"] = 26.0
    sept_raw["products"]["certificates"]["spread"] = sept_raw["products"]["certificates"].get("spread", 0) + 0.10
    sept_raw["limits"] = {"nev_decline_300": 45.0}
    with open(os.path.join(folder, "assumptions.json"), "w", encoding="utf-8") as handle:
        json.dump(sept_raw, handle, indent=2)
    # July to September, as the general ledger reported them: the forecast
    # with the same misses, so the NII back-test has something to find.
    rows = []
    labels = r["budget"]["labels"][:k]
    for t, month in enumerate(labels):
        for product in by_product:
            if product == "cash":
                continue
            interest = run[t].interest.get(product, 0.0)
            miss = misses.get(product, 0.0) * (t + 1) / k
            rows.append([month, product, "", round(interest * (1 + miss + (rate_misses.get(product, 0.0) * 12 * (t + 1) / k
                                                                            if product in rate_misses else 0.0)), 2)])
    with open(os.path.join(folder, "actuals.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["month", "line", "average_balance", "amount"])
        w.writerows(rows)
    return len(september)


# --------------------------------------------------------------- deposit history

#: The short market rate at month-ends ending 2026-06, in percent: near zero,
#: the 2022-23 hikes, a plateau, then cuts. Linear between these points.
MARKET_PATH = (("2021-07", 0.08), ("2022-03", 0.30), ("2023-07", 5.30), ("2024-08", 5.30), ("2025-06", 4.30),
               ("2026-06", 4.00))
#: The behaviour the synthetic members actually show, which the study should
#: recover: (up beta, down beta, lag months, annual decay, runoff per 100bp of
#: spread). Set a little away from the settings' assumptions on purpose, so a
#: study has something to find.
TRUE_DEPOSITS = {"regular_shares": (0.12, 0.10, 3, 0.11, 0.015), "share_drafts": (0.03, 0.03, 2, 0.13, 0.010),
                 "money_market": (0.55, 0.75, 2, 0.28, 0.045), "ira_shares": (0.25, 0.25, 4, 0.07, 0.008)}


def _month_add(label, n):
    y, m = int(label[:4]), int(label[5:7]) + n
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return "%04d-%02d" % (y, m)


def _market():
    points = [(int(l[:4]) * 12 + int(l[5:7]), r) for l, r in MARKET_PATH]
    out = []
    for i in range(points[0][0], points[-1][0] + 1):
        for (a, ra), (b, rb) in zip(points, points[1:]):
            if a <= i <= b:
                out.append(("%04d-%02d" % ((i - 1) // 12, (i - 1) % 12 + 1), ra + (rb - ra) * (i - a) / float(b - a)))
                break
    return out


def write_depositors(name, members, seed, sigma=1.45):
    """One row per member: a lognormal spread of balances scaled to the
    credit union's total shares, so the report can measure uninsured
    balances and large-depositor concentration."""
    import csv
    from keel.__main__ import load
    folder = os.path.join(EXAMPLES, name)
    positions, _, _, _ = load(folder)
    total = sum(p.balance for p in positions if p.side == "liability"
                and p.product not in ("borrowings", "other_liabilities"))
    rng = random.Random(seed)
    raw = [rng.lognormvariate(0.0, sigma) for _ in range(members)]
    scale = total / sum(raw)
    with open(os.path.join(folder, "depositors.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["member_id", "balance"])
        for i, x in enumerate(raw):
            w.writerow(["M%06d" % i, round(x * scale, 2)])
    return members


def write_deposit_history(name="mid-cu", accounts_per_product=1200, account_months=36):
    """Monthly product history (60 months) and three years of account balances."""
    import csv
    from keel.__main__ import load
    folder = os.path.join(EXAMPLES, name)
    positions, a, _, _ = load(folder)
    market = _market()
    rng = random.Random(77)
    rows = []
    for product, (up, down, lag, decay, sens) in TRUE_DEPOSITS.items():
        book = [p for p in positions if p.product == product]
        balance_now = sum(p.balance for p in book)
        rate_now = sum(p.balance * p.rate for p in book) / balance_now * 100.0
        n = len(market)
        rates = [0.0] * n
        for t in range(1, n):
            source = max(0, t - lag)
            change = market[source][1] - market[max(0, source - 1)][1]
            rates[t] = rates[t - 1] + (up if change > 0 else down) * change + rng.gauss(0, 0.01)
        shift = rate_now - rates[-1]
        floor = 100 * a.products[product].rate_floor
        rates = [max(floor, r + shift) for r in rates]
        growth = [0.0] * n
        for t in range(1, n):
            spread = market[t][1] - rates[t]
            growth[t] = 0.03 / 12 - sens * spread / 12.0 + rng.gauss(0, 0.004)
        balances = [0.0] * n
        balances[-1] = balance_now
        for t in range(n - 1, 0, -1):
            balances[t - 1] = balances[t] / math.exp(growth[t])
        for t in range(n):
            rows.append([market[t][0], product, round(balances[t], 2), round(rates[t], 3), round(market[t][1], 3)])
    with open(os.path.join(folder, "deposit_history.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["month", "product", "balance", "rate", "market_rate"])
        w.writerows(rows)
    # Accounts: a cohort open in the first month, each closing or drawing
    # down so the cohort's balance decays at the true rate, plus new accounts.
    labels = [m for m, _ in market][-(account_months + 1):]
    account_rows = []
    for product, (_, _, _, decay, _) in TRUE_DEPOSITS.items():
        monthly_keep = (1.0 - decay) ** (1.0 / 12.0)
        close = 1.0 - monthly_keep ** 0.5            # half the runoff is closures ...
        draw = 1.0 - monthly_keep / (1.0 - close)    # ... and half is balances drawn down
        accounts = {"%s-%05d" % (product[:3], i): rng.lognormvariate(8.5, 1.1) for i in range(accounts_per_product)}
        opened = 0
        for t, month in enumerate(labels):
            if t:
                for key in list(accounts):
                    if rng.random() < close:
                        del accounts[key]
                    else:
                        accounts[key] *= (1.0 - draw) * math.exp(rng.gauss(0, 0.05) - 0.05 ** 2 / 2)
                for _ in range(int(accounts_per_product * close * 1.1)):
                    opened += 1
                    accounts["%s-N%05d" % (product[:3], opened)] = rng.lognormvariate(8.2, 1.1)
            for key, value in accounts.items():
                account_rows.append([month, key, product, round(value, 2)])
    with open(os.path.join(folder, "deposit_accounts.csv"), "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["month", "account_id", "product", "balance"])
        w.writerows(account_rows)
    return len(rows), len(account_rows)
