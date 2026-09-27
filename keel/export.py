"""Every table in the report, as an Excel workbook.

ALCO packets get assembled in Excel, so the numbers go there too: one sheet
per table, raw values (dollars, percents as percents) rather than the report's
rounded thousands, so a formula built on them adds up to the cent. It reads
the same `results.compute` output the HTML does.
"""

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
