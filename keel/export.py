"""Every table in the report, as an Excel workbook.

ALCO packets get assembled in Excel, so the numbers go there too: one sheet
per table, raw values (dollars, percents as percents) rather than the report's
rounded thousands, so a formula built on them adds up to the cent. It reads
the same `results.compute` output the HTML does.
"""

import re

from keel import xlsx


def _pct(value):
    return None if value is None else round(100.0 * value, 6)


def sheets(r):
    a = r["assumptions"]
    out = {}
    out["Summary"] = [["heading", "finding"]] + [[h, t] for h, t in r["findings"]] + [
        [], ["credit union", r["name"]], ["as of", r["as_of"]]]
    out["Limits"] = [["measure", "kind", "limit", "value", "unit", "status", "board-set"]] + [
        [x.label, x.kind, x.limit, x.value, x.unit, x.status, not x.default] for x in r["limits"]]
    out["NII"] = [["scenario", "year1_nii", "year1_vs_base_pct", "year2_nii", "months24_vs_base_pct"]] + [
        [x["scenario"], x["y1"], _pct(x["y1_change"]), x["y2"], _pct(x["m24_change"])] for x in r["nii"]]
    out["NEV"] = [["scenario", "pv_assets", "pv_liabilities", "nev", "nev_ratio_pct", "nev_vs_base_pct"]] + [
        [x["scenario"], x["pv_assets"], x["pv_liabilities"], x["nev"], _pct(x["ratio"]), _pct(x["change"])]
        for x in r["nev"]]
    t = r["test"]
    out["NCUA test"] = ([["basis", "pv_assets", "pv_liabilities", "nev", "nev_ratio_pct"]]
                        + [[k, v["pv_assets"], v["pv_liabilities"], v["nev"], _pct(v["ratio"])]
                           for k, v in r["supervisory"].items()]
                        + [[], ["post-shock ratio (pct)", _pct(t["post_shock_ratio"]), t["ratio_rating"]],
                           ["NEV decline (pct)", _pct(t["sensitivity_value_decline"]), t["sensitivity_rating"]]])
    out["Repricing gap"] = [["band", "assets", "liabilities", "gap", "cumulative", "cumulative_to_assets_pct"]] + [
        [g["band"], g["assets"], g["liabilities"], g["gap"], g["cumulative"], _pct(g["cumulative_to_assets"])]
        for g in r["gap"]]
    p = r["plan"]
    years = ["year_%d" % y for y in range(1, p["years"] + 1)]
    lines = ("interest_income", "interest_expense", "net_interest_income", "fee_income", "operating_expense",
             "credit_losses", "net_income")
    out["Plan income"] = [["line"] + years] + [[k] + [s[k] for s in p["statements"]] for k in lines]
    ratio_keys = ("yield_on_assets", "cost_of_funds", "nim", "roa", "efficiency", "net_worth_ratio")
    out["Plan ratios"] = [["ratio_pct"] + years] + [[k] + [_pct(x[k]) for x in p["ratios"]] for k in ratio_keys]
    out["Plan balance sheet"] = ([["line", "side"] + years]
                                 + [[b["line"], b["side"]] + b["values"] for b in p["balance_sheet"]]
                                 + [["total_assets", ""] + [x["assets"] for x in p["totals"]],
                                    ["total_liabilities", ""] + [x["liabilities"] for x in p["totals"]],
                                    ["net_worth", ""] + [x["equity"] for x in p["totals"]]])
    out["Plan by scenario"] = [["scenario", "year1_net_income", "net_worth_ratio_m12_pct", "peak_overnight"]] + [
        [x["scenario"], x["net_income_y1"], _pct(x["net_worth_m12"]), x["peak_overnight"]] for x in p["by_scenario"]]
    L = r["liquidity"]
    out["Liquidity stress"] = [["month", "cash", "liquid_after_haircut", "overnight", "available"]] + [
        [m["month"], m["cash"], m["liquid"], m["overnight"], m["available"]] for m in L["stress"]]
    out["Contractual gap"] = [["month", "net_inflow", "cumulative"]] + [list(x) for x in L["contractual"]]
    if r["securities"]:
        out["Investments"] = [["id", "name", "product", "book", "market", "gain", "yield_pct", "wal_years",
                               "duration"]] + [[s["id"], s["name"], s["product"], s["book"], s["market"], s["gain"],
                                                _pct(s["yield"]), s["wal"], s["duration"]] for s in r["securities"]]
    imported = r["imported"]
    if imported is not None:
        out["Loans"] = [["product", "count", "balance", "rate_pct", "term_months", "delinquent_60", "nonaccrual"]] + [
            [x["product"], x["count"], x["balance"], x["rate"], x["term"], x["delinquent"], x["nonaccrual"]]
            for x in imported.summaries["loans"]]
        out["Certificates"] = [["band", "count", "balance", "rate_pct"]] + [
            [x["band"], x["count"], x["balance"], x["rate"]] for x in imported.summaries["certificates"]]
    P = r["profitability"]
    out["Profitability"] = [["product", "side", "balance", "interest", "ftp", "spread", "capital_credit", "fees",
                             "servicing", "expected_loss", "tax", "net", "rwa", "capital", "raroc_pct"]] + [
        [x.product, x.side, x.balance, x.interest, x.ftp, x.spread, x.capital_credit, x.fees, x.servicing,
         x.expected_loss, x.tax, x.net, x.rwa, x.capital, _pct(x.raroc)] for x in P["lines"]] + [
        [], ["treasury margin", "", "", "", "", P["treasury"]]] + [
        [key, "", "", "", "", value] for key, value in P["totals"].items()]
    b = r["budget"]
    for title, key in (("Budget balances", "average"), ("Budget interest", "interest")):
        out[title] = [["product", "side"] + b["labels"]] + [[p["product"], p["side"]] + p[key] for p in b["products"]]
    keys = ("interest_income", "interest_expense", "nii", "fee_income", "operating_expense", "credit_losses",
            "income_tax", "net_income")
    if b.get("lines"):
        out["Budget noninterest"] = [["line", "kind"] + b["labels"]] + [[x["line"], x["kind"]] + x["months"]
                                                                        for x in b["lines"]]
    if b.get("drivers"):
        out["Budget drivers"] = [["product", "month", "volume", "balance", "rate_pct"]] + [
            [d["product"], d["month"], d["volume"], d["balance"], _pct(d["rate"])] for d in b["drivers"]]
    path = r.get("rate_path")
    if path and path["kind"] != "flat":
        out["Rate path"] = [["month"] + ["rate_%dm" % t for t in path["tenors"]]] + [
            [x["month"]] + x["rates"] for x in path["rows"]]
    out["Budget income"] = [["line"] + b["labels"]] + [[key] + [m[key] for m in b["income"]] for key in keys]
    v = r.get("variance")
    if v:
        out["Variance"] = ([["product", "side", "budget_balance", "actual_balance", "budget_interest",
                             "actual_interest", "volume", "rate", "nii_variance"]]
                           + [[x["product"], x["side"], x["budget_balance"], x["actual_balance"], x["budget_interest"],
                               x["actual_interest"], x["volume"], x["rate"], x["nii_variance"]] for x in v["products"]]
                           + [[], ["line", "budget", "actual", "better (worse)"]]
                           + [[x["line"], x["budget"], x["actual"], x["variance"]] for x in v["statement"]])
    for i, q in enumerate(r.get("queries") or [], 1):
        title = re.sub(r"[\[\]:*?/\\]", "-", "Q%d %s" % (i, q["name"]))[:31]   # characters Excel refuses
        out[title] = [q["columns"]] + q["rows"] + ([q["total"]] if q["total"] else [])
    cap = r.get("capital")
    if cap:
        out["Capital"] = ([["measure", "value_pct", "well_capitalized_pct", "adequate_pct", "status", "note"]]
                          + [[x["measure"], _pct(x["value"]), _pct(x["well"]), _pct(x["adequate"]), x["status"],
                              x["note"]] for x in cap["rows"]]
                          + [[], ["securities at market", "today", "after +300bp"],
                             ["book", cap["lens"]["book"], cap["lens"]["book"]],
                             ["market", cap["lens"]["market"], cap["lens"]["market_300"]],
                             ["net worth ratio with it (pct)", _pct(cap["lens"]["ratio_now"]),
                              _pct(cap["lens"]["ratio_300"])]])
    c = r.get("credit")
    if c:
        out["Credit scenarios"] = [["scenario", "multiplier", "months", "reversion_months", "losses_y1", "losses_y2",
                                    "net_income_2y", "lifetime_loss", "allowance_build", "net_worth_after_build_pct",
                                    "lowest_net_worth_pct"]] + [
            [s["name"], s["multiplier"], s["months"], s["reversion_months"], s["losses_y1"], s["losses_y2"],
             s["net_income_2y"], s["lifetime"], s["allowance_build"], _pct(s["net_worth_after_build"]),
             _pct(s["lowest_net_worth"])] for s in c["scenarios"]]
        out["CECL estimate"] = ([["product", "balance", "annual_loss_rate_pct", "remaining_life_years", "lifetime_loss"]]
                                + [[x["product"], x["balance"], _pct(x["rate"]), x["wal_years"], x["lifetime"]]
                                   for x in c["products"]]
                                + [[], ["estimate", c["estimate"]], ["booked allowance", c["booked"]]])
    L = r["liquidity"]
    if L.get("scenarios"):
        out["Stress scenarios"] = [["scenario", "runoff_multiplier", "haircut_add_pts", "contingent_available_pct",
                                    "months", "uninsured_runoff_pct", "survival_month", "lowest", "lowest_month"]] + [
            [s["name"], s["runoff_multiplier"], 100 * s["haircut_add"], 100 * s["contingent_available"], s["months"],
             100 * s["uninsured_runoff"], s["survival"], s["lowest"], s["lowest_month"]] for s in L["scenarios"]]
    col = L.get("collateral")
    if col and col["lines"]:
        out["Collateral"] = ([["product", "balance", "lendable_share_pct", "lendable_value"]]
                             + [[p, b, 100 * sh, v] for p, b, sh, v in col["lines"]]
                             + [[], ["secured line", "unused line", "collateral left", "counted"]]
                             + [[x["name"], x["line"], x["collateral_headroom"], x["usable"]] for x in col["secured"]])
    conc = L.get("concentration")
    if conc:
        out["Concentration"] = [["measure", "value"]] + [[k2, v] for k2, v in conc.items()]
    d = r.get("deposits")
    if d and d["products"]:
        out["Deposit study"] = [["product", "months", "beta_assumed_pct", "beta_estimated_pct", "lag", "r2",
                                 "up_beta_pct", "down_beta_pct", "decay_assumed_pct", "decay_estimated_pct",
                                 "runoff_per_100bp_assumed_pct", "runoff_per_100bp_estimated_pct", "sensitivity_r2",
                                 "core_pct", "flags"]] + [
            [x["product"], x["months"], _pct((x["assumed"] or {}).get("beta")), _pct(x["beta"]["beta"]), x["beta"]["lag"],
             x["beta"]["r2"], _pct(x["beta"]["up_beta"]), _pct(x["beta"]["down_beta"]),
             _pct((x["assumed"] or {}).get("runoff")), _pct(x["decay"]["decay"]) if x["decay"] else None,
             _pct((x["assumed"] or {}).get("runoff_per_100bp")),
             _pct(x["sensitivity"]["runoff_per_100bp"]) if x["sensitivity"] else None,
             x["sensitivity"]["r2"] if x["sensitivity"] else None, _pct(x["core"]), "; ".join(x["flags"])]
            for x in d["products"]]
    t = r.get("sensitivity")
    if t:
        out["Assumption tests"] = [["family", "variant", "nii_y1", "nii_decline_300_pct", "nev_decline_300_pct",
                                    "nev_ratio_min_pct", "limit_flips"]] + [
            ["as assumed", "", t["baseline"]["nii_y1"], t["baseline"]["nii_decline_300"],
             t["baseline"]["nev_decline_300"], t["baseline"]["nev_ratio_min"], ""]] + [
            [x["family"], x["variant"], x["values"]["nii_y1"], x["values"]["nii_decline_300"],
             x["values"]["nev_decline_300"], x["values"]["nev_ratio_min"], ", ".join(x["flips"])] for x in t["rows"]]
    h = r.get("history")
    if h:
        keys = ("as_of", "assets", "nii_y1", "nii_decline_300", "nev_ratio_min", "nev_decline_300", "supervisory_ratio",
                "net_worth_ratio", "capital_to_rwa", "breaches")
        out["Trend"] = [list(keys)] + [[x.get(key) for key in keys] for x in h["trend"]]
        if h["changes"]:
            out["Assumption changes"] = [["assumption", "was", "now"]] + [
                [w, "" if o is None else o, "" if n is None else n] for w, o, n in h["changes"]]
        if h["backtest"]:
            out["Back-test"] = [["product", "side", "forecast", "actual", "error", "forecast_rate_pct",
                                 "actual_rate_pct"]] + [
                [x["product"], x["side"], x["forecast"], x["actual"], x["error"], _pct(x["forecast_rate"]),
                 _pct(x["actual_rate"])] for x in h["backtest"]["products"]]
    out["Checks"] = [["check", "passed", "detail"]] + [[c.name, c.passed, c.detail] for c in r["checks"]]
    months = r["base_run"]
    out["Monthly base"] = [["month", "cash", "overnight", "interest_income", "interest_expense", "fee_income",
                            "operating_expense", "credit_losses", "net_income", "assets", "liabilities", "equity"]] + [
        [m.month, m.cash, m.overnight, m.interest_income, m.interest_expense, m.fee_income, m.operating_expense,
         m.credit_losses, m.net_income, m.assets, m.liabilities, m.equity] for m in months]
    out["Curve"] = [["tenor_months", "rate_pct"]] + [[t, v] for t, v in zip(a.curve.tenors, a.curve.rates)]
    return out


def write_workbook(r, path):
    xlsx.write_workbook(path, sheets(r))
    return path
