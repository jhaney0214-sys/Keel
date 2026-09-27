"""What the samples need beyond the core files: product costs and capital
weights, an actuals file to compare with the budget, saved ad hoc queries,
new-product proposals, and a synthetic community bank.

Run by make_samples.py after the credit unions are written. Every figure is
invented; none describes a real institution.
"""

import json
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
    "first_mortgage": {"risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "first_mortgage_15": {"risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "arm_mortgage": {"risk_weight": 50.0, "servicing_cost": 0.25, "fee_yield": 0.05, "origination_cost": 1.00},
    "heloc": {"risk_weight": 100.0, "servicing_cost": 0.50, "fee_yield": 0.10, "origination_cost": 0.50},
    "credit_card": {"risk_weight": 75.0, "servicing_cost": 2.50, "fee_yield": 2.00},
    "unsecured": {"risk_weight": 75.0, "servicing_cost": 1.50, "fee_yield": 0.25, "origination_cost": 1.00},
    "commercial_re": {"risk_weight": 100.0, "servicing_cost": 0.40, "fee_yield": 0.10, "origination_cost": 0.75},
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
    "commercial_re": {"cpr": 6.0, "cpr_per_100bp": 2.0, "new_term": 60, "new_amortization": "balloon",
                      "spread": 2.50, "discount_spread": 2.50, "growth": 5.0, "charge_off": 0.20, "risk_weight": 100.0,
                      "servicing_cost": 0.35, "fee_yield": 0.15, "origination_cost": 0.60},
    "construction": {"new_term": 18, "growth": 3.0, "charge_off": 0.50, "discount_spread": 3.25,
                     "risk_weight": 150.0, "servicing_cost": 0.80, "fee_yield": 0.50, "origination_cost": 0.75},
    "residential_mortgage": {"cpr": 7.0, "cpr_per_100bp": 6.0, "cpr_floor": 3.0, "cpr_cap": 45.0, "new_term": 360,
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
                   "borrowings_to_assets_max": 15.0, "survival_months_min": 6.0, "capital_to_rwa_min": 10.5},
        "liquidity": {"stress_months": 3, "contingent": [
            {"name": "FHLB unused borrowing capacity", "capacity": round(total_assets * 0.10, -3)},
            {"name": "Federal Reserve discount window (pledged collateral)", "capacity": round(total_assets * 0.05, -3)}]},
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
    print("%-14s %d positions" % ("community-bank", write_bank()))
