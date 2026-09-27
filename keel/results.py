"""Every number a report shows, computed once.

The HTML report, its charts and the Excel workbook all read this, so they
cannot disagree: a figure that appears in two places was computed in one.
Values are raw (dollars, decimals); formatting belongs to whoever shows them.
"""

import dataclasses

from keel import engine, measures, model
from keel.curve import Scenario
from keel.engine import CASH

INVESTMENT_EXTRAS = {"invest_cds", "fhlb_stock", "cuso"}


@dataclasses.dataclass
class Limit:
    key: str
    label: str
    kind: str            # "max" or "min"
    limit: float         # percent, or months
    value: float         # same units; None when not measurable
    default: bool        # True when the board has not set it
    status: str          # "within", "near", "breach"
    unit: str            # "%" or "months"


def evaluate(key, kind, limit, value, band):
    if value is None:
        return "within"
    margin = abs(limit) * band / 100.0
    if kind == "max":
        return "breach" if value > limit else "near" if value > limit - margin else "within"
    return "breach" if value < limit else "near" if value < limit + margin else "within"


def evaluate_limits(measured, a):
    """Every policy limit, with its value from `measured` ({key: value}) and
    its status against the board's limit, or Keel's default where none is set."""
    out = []
    for key, kind, default, label in model.LIMITS:
        value = a.limits.get(key, default)
        out.append(Limit(key, label, kind, value, measured[key], key not in a.limits,
                         evaluate(key, kind, value, measured[key], a.warning_band),
                         "months" if key.endswith("months_min") else "%"))
    return out


def survival_value(survival):
    """A survival month as a limit reads it. None: liquidity lasted the whole
    measured year, more than any number of months this can report, so it
    passes any limit up to a year. (Recorded as exactly 12 at first, which
    read as "near".)"""
    return None if survival is None else float(survival - 1)


def year_ratios(months, opening_assets, opening_liabilities):
    """Yield, cost of funds, NIM, ROA and efficiency for one year of months,
    each on average balances (the opening and every month end)."""
    assets = [opening_assets] + [m.assets for m in months]
    liabilities = [opening_liabilities] + [m.liabilities for m in months]
    avg_a = sum(assets) / len(assets)
    avg_l = sum(liabilities) / len(liabilities)
    s = measures.income_statement(months)
    revenue = s["net_interest_income"] + s["fee_income"]
    return {"yield_on_assets": s["interest_income"] / avg_a, "cost_of_funds": s["interest_expense"] / avg_l,
            "nim": s["net_interest_income"] / avg_a, "roa": s["net_income"] / avg_a,
            "efficiency": s["operating_expense"] / revenue if revenue else 0.0,
            "net_worth_ratio": months[-1].equity / months[-1].assets}


def compute(positions, a, name, imported=None):
    """Everything, as a dict."""
    base_scenario = a.scenarios[0]
    runs = {s.name: engine.going_concern(positions, a, s) for s in a.scenarios}
    base = runs["base"]
    _, open_assets, open_liabilities, open_equity = engine.opening(positions)

    # ---- NII and earnings at risk
    def nii(run, first, last):
        return sum(m.nii for m in run[first:last])
    b1, b2, b24 = nii(base, 0, 12), nii(base, 12, 24), nii(base, 0, 24)
    nii_rows = [{"scenario": s.name, "shape": s.shape is not None, "ramp": s.ramp_months,
                 "shock_bp": s.shock_bp if s.shape is None else None,
                 "y1": nii(runs[s.name], 0, 12), "y2": nii(runs[s.name], 12, 24),
                 "y1_change": nii(runs[s.name], 0, 12) / b1 - 1 if b1 else 0.0,
                 "m24_change": nii(runs[s.name], 0, 24) / b24 - 1 if b24 else 0.0}
                for s in a.scenarios]

    # ---- NEV, own assumptions and supervisory
    nevs = {s.name: measures.nev(positions, a, s) for s in a.scenarios if s.instantaneous}
    nev_rows = [{"scenario": k, "pv_assets": n.pv_assets, "pv_liabilities": n.pv_liabilities, "nev": n.nev,
                 "ratio": n.ratio, "change": n.nev / nevs["base"].nev - 1 if nevs["base"].nev else 0.0}
                for k, n in nevs.items()]
    sup = {k: measures.nev(positions, a, Scenario(k, bp, floor=a.rate_floor), supervisory=True)
           for k, bp in (("base", 0), ("+300", 300))}
    test = measures.ncua_test(sup["base"], sup["+300"])

    # ---- the plan
    years = a.horizon_months // 12
    statements = [measures.income_statement(measures.year(base, y)) for y in range(1, years + 1)]
    ratios = []
    prev_a, prev_l = open_assets, open_liabilities
    for y in range(1, years + 1):
        months = measures.year(base, y)
        ratios.append(year_ratios(months, prev_a, prev_l))
        prev_a, prev_l = months[-1].assets, months[-1].liabilities
    products = sorted({p.product for p in positions} - {CASH},
                      key=lambda k: ([p.side for p in positions if p.product == k][0] != "asset", k))
    sides = {p.product: p.side for p in positions}
    ends = [base[12 * y - 1] for y in range(1, years + 1)]
    balance_sheet = [{"line": "cash", "side": "asset", "values": [m.cash for m in ends]}]
    for product in products:
        balance_sheet.append({"line": product, "side": sides[product],
                              "values": [m.balances.get(product, 0.0) for m in ends]})
    balance_sheet.append({"line": "overnight_borrowing", "side": "liability", "values": [m.overnight for m in ends]})
    plan_by_scenario = [{"scenario": s.name,
                         "net_income_y1": measures.income_statement(measures.year(runs[s.name], 1))["net_income"],
                         "net_worth_m12": runs[s.name][11].equity / runs[s.name][11].assets,
                         "peak_overnight": max(m.overnight for m in runs[s.name])} for s in a.scenarios]

    # ---- liquidity
    stressed = engine.going_concern(positions, a, base_scenario, stress=True)
    survival = measures.survival(stressed)
    low = min(stressed[:measures.SURVIVAL_MONTHS], key=lambda m: m.available_liquidity)
    peak, peak_month = measures.funding_gap(base)
    liq_ratios = measures.ratios(positions, a)

    # ---- portfolios
    invest_products = {k for k, v in a.products.items() if v.liquid} | INVESTMENT_EXTRAS
    securities = measures.security_analytics(positions, a, invest_products)
    gap_rows, insensitive = measures.repricing_gap(positions, a)

    # ---- reconciliation
    checks = measures.reconcile(positions, a, runs)
    if imported is not None:
        for tie in imported.ties:
            checks.append(measures.Check("Detail ties to the general ledger: %s" % tie.line, tie.ties,
                                         "detail $%s, ledger $%s, difference $%.2f" % (
                                             "{:,.2f}".format(tie.detail), "{:,.2f}".format(tie.ledger),
                                             tie.difference)))

    # ---- limits
    parallel = {r["scenario"]: r for r in nii_rows if r["shock_bp"] is not None and r["ramp"] == 0}
    nev_by = {r["scenario"]: r for r in nev_rows}
    worst = lambda keys, rows, field: min(rows[k][field] for k in keys if k in rows)  # noqa: E731
    lowest_nw = min(m.equity / m.assets for m in base)
    measured = {
        "nii_decline_300": -100 * worst(("+300", "-300"), parallel, "y1_change"),
        "nii_decline_200": -100 * worst(("+200", "-200"), parallel, "y1_change"),
        "nev_decline_300": -100 * worst(("+300", "-300"), nev_by, "change"),
        "nev_ratio_min": 100 * worst(("+300", "-300"), nev_by, "ratio"),
        "net_worth_min": 100 * lowest_nw,
        "liquid_to_shares_min": 100 * liq_ratios["liquid_to_shares"],
        "loans_to_shares_max": 100 * liq_ratios["loans_to_shares"],
        "borrowings_to_assets_max": 100 * liq_ratios["borrowings_to_assets"],
        "survival_months_min": survival_value(survival),
    }
    limits = evaluate_limits(measured, a)

    result = {
        "name": name, "as_of": a.as_of, "notes": a.notes, "assumptions": a,
        "opening": {"assets": open_assets, "liabilities": open_liabilities, "equity": open_equity},
        "nii": nii_rows, "nii_base": {"y1": b1, "y2": b2, "m24": b24},
        "nev": nev_rows, "supervisory": {k: {"pv_assets": n.pv_assets, "pv_liabilities": n.pv_liabilities,
                                             "nev": n.nev, "ratio": n.ratio} for k, n in sup.items()},
        "test": test, "gap": gap_rows, "insensitive": insensitive,
        "plan": {"years": years, "statements": statements, "ratios": ratios, "balance_sheet": balance_sheet,
                 "totals": [{"assets": m.assets, "liabilities": m.liabilities, "equity": m.equity,
                             "net_worth_ratio": m.equity / m.assets} for m in ends],
                 "by_scenario": plan_by_scenario,
                 "net_worth_path": [m.equity / m.assets for m in base]},
        "liquidity": {"tier": measures.cfp_tier(open_assets), "ratios": liq_ratios,
                      "stress": [{"month": m.month, "cash": m.cash, "liquid": m.liquid_assets,
                                  "overnight": m.overnight, "available": m.available_liquidity}
                                 for m in stressed[:measures.SURVIVAL_MONTHS]],
                      "survival": survival, "lowest": low.available_liquidity, "lowest_month": low.month,
                      "funding_peak": peak, "funding_peak_month": peak_month,
                      "contractual": measures.contractual_gap(positions, a, base_scenario, 12),
                      "contingent": list(a.contingent), "stress_months": a.stress_months},
        "securities": securities, "security_groups": measures.by_product(securities),
        "imported": imported, "positions": len(positions),
        "checks": checks, "limits": limits,
        "base_run": base,
    }
    result["findings"] = findings(result)
    return result


def _money(v):
    v = abs(v)
    if v >= 1e9:
        return "$%.2f billion" % (v / 1e9)
    if v >= 1e6:
        return "$%.1f million" % (v / 1e6)
    return "${:,.0f}".format(v)


def findings(r):
    """The report's opening, in plain words: what an ALCO member needs from it."""
    out = []
    parallel = [x for x in r["nii"] if x["shock_bp"] is not None and x["ramp"] == 0 and x["shock_bp"]]
    worst = min(parallel, key=lambda x: x["y1_change"])
    out.append(("Earnings", "Year-one net interest income is %s. The worst parallel shock, %s, %s it %.1f%%%s." % (
        _money(r["nii_base"]["y1"]), worst["scenario"] + "bp", "lowers" if worst["y1_change"] < 0 else "raises",
        abs(100 * worst["y1_change"]),
        "; every shock raises it" if worst["y1_change"] >= 0 else "")))
    t = r["test"]
    nev_worst = min((x for x in r["nev"] if x["scenario"] in ("+300", "-300")), key=lambda x: x["ratio"])
    out.append(("Economic value", "On the credit union's own assumptions, the NEV ratio falls to %.1f%% in the %s "
                "shock. NCUA's supervisory test, with its standardized share values, rates the ratio %s (%.1f%%) "
                "and the change %s (%.0f%%)." % (100 * nev_worst["ratio"], nev_worst["scenario"] + "bp",
                                                  t["ratio_rating"], 100 * t["post_shock_ratio"],
                                                  t["sensitivity_rating"], -100 * t["sensitivity_value_decline"])))
    L = r["liquidity"]
    if L["survival"] is None:
        text = "Liquidity lasts through the first year of the %d-month stress; its low point is %s, in month %d." % (
            L["stress_months"], _money(L["lowest"]), L["lowest_month"])
    else:
        text = "Liquidity runs out in month %d of the stress." % L["survival"]
    if L["funding_peak"] > 0:
        text += " The plan itself borrows up to %s overnight (month %d), because loans outgrow shares." % (
            _money(L["funding_peak"]), L["funding_peak_month"])
    out.append(("Liquidity", text))
    p = r["plan"]
    out.append(("The plan", "Net income of %s in year one and %s in year %d; net worth goes from %.1f%% to %.1f%%." % (
        _money(p["statements"][0]["net_income"]), _money(p["statements"][-1]["net_income"]), p["years"],
        100 * r["opening"]["equity"] / r["opening"]["assets"], 100 * p["totals"][-1]["net_worth_ratio"])))
    breach = [x for x in r["limits"] if x.status == "breach"]
    near = [x for x in r["limits"] if x.status == "near"]
    defaults = sum(1 for x in r["limits"] if x.default)
    if breach:
        text = "%d of %d limits breached: %s." % (len(breach), len(r["limits"]),
                                                  "; ".join(x.label for x in breach))
    else:
        text = "All %d limits met." % len(r["limits"])
    if near:
        text += " Near a limit: %s." % "; ".join(x.label for x in near)
    if defaults:
        text += " %d of them are Keel's defaults, not the board's; set them in the Limits sheet." % defaults
    out.append(("Limits", text))
    failed = [c for c in r["checks"] if not c.passed]
    out.append(("Reconciliation", "All %d checks pass: the rate-risk, plan and liquidity numbers are one model%s." % (
        len(r["checks"]), " and the detail ties to the general ledger" if r["imported"] is not None else "")
        if not failed else "%d of %d checks FAIL; read the reconciliation before relying on anything here."
        % (len(failed), len(r["checks"]))))
    return out
