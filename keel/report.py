"""One HTML report, readable offline, and CSVs of the monthly projection.

Sections in the order an ALCO packet runs: summary, interest-rate risk (NII
and NEV, with the NCUA test), the plan (FP&A), liquidity, the reconciliation
that ties them, and every assumption the run used.
"""

import csv
import datetime
import html
import os

from keel import engine, measures
from keel.engine import CASH


def _m(value):
    """Dollars in thousands, the way ALCO packets read."""
    return "{:,.0f}".format(value / 1000.0)


def _p(value, places=2):
    return ("{:,.%df}%%" % places).format(100.0 * value)


def _table(head, rows, numeric_from=1):
    cells = ["<table><thead><tr>"]
    cells += ['<th%s>%s</th>' % (' class="num"' if i >= numeric_from else "", html.escape(h))
              for i, h in enumerate(head)]
    cells.append("</tr></thead><tbody>")
    for row in rows:
        cells.append("<tr>" + "".join('<td%s>%s</td>' % (' class="num"' if i >= numeric_from else "", c)
                                      for i, c in enumerate(row)) + "</tr>")
    cells.append("</tbody></table>")
    return '<div class="wrap">' + "".join(cells) + "</div>"


STYLE = """
:root{--paper:#fcfcfa;--ink:#1b1d1c;--muted:#5d625f;--rule:#dcdfdb;--accent:#1f5f7a;--good:#2d6a3e;--bad:#9b2c2c;}
@media (prefers-color-scheme:dark){:root{--paper:#131515;--ink:#e7e9e6;--muted:#9ba09c;--rule:#2b2f2d;--accent:#7fb7d0;--good:#7cc08a;--bad:#e08a8a;}}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 -apple-system,'Segoe UI',Inter,sans-serif;}
main{max-width:72rem;margin:0 auto;padding:2rem 1.25rem 4rem;}
h1{font-size:1.9rem;margin:0 0 .25rem;} h2{font-size:1.35rem;margin:2.5rem 0 .75rem;padding-bottom:.4rem;border-bottom:1px solid var(--rule);}
h3{font-size:1.05rem;margin:1.5rem 0 .5rem;} p{margin:0 0 .8rem;max-width:62rem;} .muted{color:var(--muted);font-size:.9rem;}
.wrap{overflow-x:auto;margin:.5rem 0 1rem;} table{border-collapse:collapse;font-size:.92rem;min-width:28rem;}
th{font-size:.78rem;color:var(--muted);text-align:left;font-weight:600;padding:.35rem .7rem;border-bottom:1px solid var(--ink);white-space:nowrap;}
td{padding:.35rem .7rem;border-bottom:1px solid var(--rule);white-space:nowrap;} .num{text-align:right;font-variant-numeric:tabular-nums;}
.pass{color:var(--good);font-weight:600;} .fail{color:var(--bad);font-weight:600;}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:1rem;margin:1rem 0;}
.tile{border:1px solid var(--rule);padding:.8rem 1rem;} .tile b{display:block;font-size:1.5rem;font-variant-numeric:tabular-nums;}
.tile span{color:var(--muted);font-size:.85rem;}
"""


def build(positions, assumptions, out_dir, name="Credit union", imported=None):
    a = assumptions
    runs = {s.name: _going(positions, a, s) for s in a.scenarios}
    base_run = runs["base"]
    nevs = {s.name: measures.nev(positions, a, s) for s in a.scenarios if s.instantaneous}
    supervisory = {name: measures.nev(positions, a, s, supervisory=True)
                   for s in a.scenarios for name in [s.name] if s.parallel and s.shock_bp in (0, 300)}
    test = measures.ncua_test(supervisory["base"], supervisory["+300"])
    stressed = _going(positions, a, a.scenarios[0], stress=True)
    survival = measures.survival(stressed)
    checks = measures.reconcile(positions, a, runs)
    if imported is not None:
        for tie in imported.ties:
            checks.append(measures.Check("Detail ties to the general ledger: %s" % tie.line, tie.ties,
                                         "detail $%s, ledger $%s, difference $%.2f" % (
                                             "{:,.2f}".format(tie.detail), "{:,.2f}".format(tie.ledger),
                                             tie.difference)))
    gap = measures.contractual_gap(positions, a, a.scenarios[0], 12)
    ratios = measures.ratios(positions, a)
    total_assets = sum(p.balance for p in positions if p.side == "asset")
    base_y1 = measures.income_statement(measures.year(base_run, 1))["net_interest_income"]

    parts = ["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width, initial-scale=1'>",
             "<title>%s: ALM, plan and liquidity, %s</title>" % (html.escape(name), a.as_of),
             "<style>%s</style></head><body><main>" % STYLE,
             "<h1>%s</h1><p class='muted'>ALM, plan and liquidity from one projection, as of %s. "
             "Dollars in thousands.</p>" % (html.escape(name), a.as_of)]
    if a.notes.get("about"):
        parts.append("<p class='muted'>%s</p>" % html.escape(a.notes["about"]))

    passed = sum(c.passed for c in checks)
    parts.append("<div class='tiles'>")
    for value, label in (
            (_m(base_y1), "Year-one NII, base plan"),
            (_p(test["post_shock_ratio"]), "NEV ratio after +300bp: %s" % test["ratio_rating"]),
            (_p(-test["sensitivity_value_decline"], 1), "NEV change at +300bp: %s" % test["sensitivity_rating"]),
            ("12 months+" if survival is None else "month %d" % survival,
             "Survival under the %d-month stress" % a.stress_months),
            ("%d of %d" % (passed, len(checks)), "Reconciliation checks passed")):
        parts.append("<div class='tile'><b>%s</b><span>%s</span></div>" % (value, html.escape(label)))
    parts.append("</div>")

    # ---- ALM
    parts.append("<h2>Interest-rate risk</h2><h3>Net interest income by scenario</h3>")
    rows = []
    for s in a.scenarios:
        y1 = measures.income_statement(measures.year(runs[s.name], 1))["net_interest_income"]
        y2 = measures.income_statement(measures.year(runs[s.name], 2))["net_interest_income"]
        rows.append([html.escape(s.name), _m(y1), _p(y1 / base_y1 - 1, 1), _m(y2)])
    parts.append(_table(["Scenario", "Year 1 NII", "vs base", "Year 2 NII"], rows))
    parts.append("<p class='muted'>Going concern: balances follow the plan's growth, and maturing or "
                 "repaid balances are replaced at the rates each scenario offers. Parallel scenarios move "
                 "the whole curve on the analysis date and hold it; ramps reach their move over the stated "
                 "months. Rates are floored at %.2f%%.</p>" % a.rate_floor)

    parts.append("<h3>Net economic value</h3>")
    base_nev = nevs["base"]
    rows = []
    for key, n in nevs.items():
        rows.append([html.escape(key), _m(n.pv_assets), _m(n.pv_liabilities), _m(n.nev), _p(n.ratio),
                     _p(n.nev / base_nev.nev - 1, 1)])
    parts.append(_table(["Scenario", "PV assets", "PV liabilities", "NEV", "NEV ratio", "NEV vs base"], rows))
    parts.append("<p class='muted'>This table uses the credit union's own share assumptions. NEV need not "
                 "move in a straight line: floors on share rates stop liability costs falling in the down "
                 "shocks while their present value keeps rising.</p>")
    parts.append("<h3>NCUA NEV Supervisory Test</h3>")
    parts.append(_table(["Supervisory basis", "PV assets", "PV liabilities", "NEV", "NEV ratio"], [
        [key, _m(n.pv_assets), _m(n.pv_liabilities), _m(n.nev), _p(n.ratio)] for key, n in supervisory.items()]))
    parts.append(
        "<p>At +300bp: post-shock NEV ratio %s, <strong>%s</strong>; NEV change %s, <strong>%s</strong>. "
        "Non-maturity shares are priced at NCUA's standardized 99.00 in the base case and 95.04 at +300bp; "
        "every other position keeps its modelled value. Thresholds from Letter SL 22-01: post-shock ratio "
        "above 7%% low, 4-7%% moderate, below 4%% high; NEV decline below 40%% low, 40-65%% moderate, above "
        "65%% high. (The decline in the ratio itself, not rated: %s.)</p>" % (
            _p(test["post_shock_ratio"]), test["ratio_rating"], _p(-test["sensitivity_value_decline"], 1),
            test["sensitivity_rating"], _p(test["sensitivity_ratio_decline"], 1)))

    gap_rows, insensitive = measures.repricing_gap(positions, a)
    parts.append("<h3>Repricing gap</h3>")
    parts.append(_table(["Band", "Assets repricing", "Liabilities repricing", "Gap", "Cumulative gap",
                         "Cumulative gap / assets"],
                        [[g["band"], _m(g["assets"]), _m(g["liabilities"]), _m(g["gap"]), _m(g["cumulative"]),
                          _p(g["cumulative_to_assets"], 1)] for g in gap_rows]))
    parts.append("<p class='muted'>Base scenario. Variable-rate positions count in full at their next reset; "
                 "everything else by its principal cash flows, including prepayment and share decay. Not "
                 "rate-sensitive: assets %s, liabilities %s.</p>" % (_m(insensitive["asset"]),
                                                                      _m(insensitive["liability"])))

    # ---- FP&A
    parts.append("<h2>The plan</h2><h3>Income statement, base scenario</h3>")
    years = a.horizon_months // 12
    statements = [measures.income_statement(measures.year(base_run, y)) for y in range(1, years + 1)]
    lines = (("Interest income", "interest_income"), ("Interest expense", "interest_expense"),
             ("Net interest income", "net_interest_income"), ("Fee and other income", "fee_income"),
             ("Operating expense", "operating_expense"), ("Credit losses", "credit_losses"),
             ("Net income", "net_income"))
    rows = [[label] + [_m(s[key]) for s in statements] for label, key in lines]
    parts.append(_table(["Line"] + ["Year %d" % y for y in range(1, years + 1)], rows))
    parts.append("<h3>Balance sheet at each year end, base scenario</h3>")
    ends = [base_run[12 * y - 1] for y in range(1, years + 1)]
    products = sorted({p.product for p in positions} - {CASH}, key=lambda k: (
        [p.side for p in positions if p.product == k][0] != "asset", k))
    rows = [["Cash"] + [_m(m.cash) for m in ends]]
    for product in products:
        rows.append([html.escape(product.replace("_", " "))] + [_m(m.balances.get(product, 0.0)) for m in ends])
    rows += [["Overnight borrowing"] + [_m(m.overnight) for m in ends],
             ["Total assets"] + [_m(m.assets) for m in ends],
             ["Total liabilities"] + [_m(m.liabilities) for m in ends],
             ["Net worth"] + [_m(m.equity) for m in ends],
             ["Net worth ratio"] + [_p(m.equity / m.assets) for m in ends]]
    parts.append(_table(["Line"] + ["Year %d" % y for y in range(1, years + 1)], rows))
    rows = []
    for s in a.scenarios:
        run = runs[s.name]
        rows.append([html.escape(s.name), _m(measures.income_statement(measures.year(run, 1))["net_income"]),
                     _p(run[11].equity / run[11].assets), _m(max(m.overnight for m in run))])
    parts.append("<h3>The plan under each scenario</h3>")
    parts.append(_table(["Scenario", "Year 1 net income", "Net worth ratio, month 12",
                         "Peak overnight borrowing"], rows))

    # ---- Liquidity
    parts.append("<h2>Liquidity</h2>")
    parts.append("<p><strong>Regulatory tier:</strong> %s (total assets %s).</p>" % (
        html.escape(measures.cfp_tier(total_assets)), _m(total_assets)))
    parts.append(_table(["Ratio", "Today"], [["Loans to shares", _p(ratios["loans_to_shares"], 1)],
                                             ["Cash and liquid investments to shares", _p(ratios["liquid_to_shares"], 1)],
                                             ["Borrowings to assets", _p(ratios["borrowings_to_assets"], 1)]]))
    parts.append("<h3>Stress: %d months of share runoff while loans fund to plan</h3>" % a.stress_months)
    rows = [[str(m.month), _m(m.cash), _m(m.liquid_assets), _m(m.overnight), _m(m.available_liquidity)]
            for m in stressed[:12]]
    parts.append(_table(["Month", "Cash", "Liquid investments after haircut", "Overnight borrowing",
                         "Available liquidity"], rows))
    parts.append("<p>%s Available liquidity is cash above the minimum, liquid investments after haircut, "
                 "and contingent sources (%s), less borrowing already drawn. The horizon is the first "
                 "twelve months; beyond that the stress's frozen share balances describe a different plan, "
                 "not a stress.</p>" % (
                     "Available liquidity stays positive through the first twelve months."
                     if survival is None else "<strong>Available liquidity runs out in month %d.</strong>" % survival,
                     html.escape(", ".join("%s %s" % (n, _m(c)) for n, c in a.contingent))))
    peak, when = measures.funding_gap(base_run)
    parts.append("<p><strong>The plan's own funding need:</strong> %s</p>" % (
        "the base plan never borrows overnight." if peak <= 0 else
        "the base plan borrows up to %s overnight, in month %d, because loans grow faster than shares. "
        "That is a funding decision the plan has to make, whatever the stress shows." % (_m(peak), when)))
    parts.append("<h3>Contractual gap, today's positions only</h3>")
    parts.append(_table(["Month", "Net inflow", "Cumulative"], [[str(k), _m(n), _m(c)] for k, n, c in gap]))

    # ---- Portfolios
    investment_products = {k for k, v in a.products.items() if v.liquid} | {
        "invest_cds", "fhlb_stock", "cuso"}
    securities = measures.security_analytics(positions, a, investment_products)
    if securities:
        parts.append("<h2>Portfolios</h2><h3>Investments by type</h3>")
        groups = measures.by_product(securities)
        totals = {"book": sum(g["book"] for g in groups), "market": sum(g["market"] for g in groups)}
        rows = [[html.escape(g["product"].replace("_", " ")), str(g["count"]), _m(g["book"]), _m(g["market"]),
                 _m(g["gain"]), _p(g["yield"]), "%.1f" % g["wal"], "%.2f" % g["duration"]] for g in groups]
        rows.append(["<strong>Total</strong>", str(len(securities)), _m(totals["book"]), _m(totals["market"]),
                     _m(totals["market"] - totals["book"]), "", "", ""])
        parts.append(_table(["Type", "Holdings", "Book", "Market value", "Unrealized gain (loss)", "Book yield",
                             "WAL (years)", "Effective duration"], rows))
        parts.append("<p class='muted'>Market value is each holding's cash flows discounted on the base curve "
                     "plus its product's discount spread; effective duration is from +/-100bp. FHLB stock and "
                     "other stakes with no maturity count at book. Callables are called when their coupon beats "
                     "the market by the product's threshold.</p>")
        if len(securities) <= 250:
            holdings = sorted(securities, key=lambda x: -x["book"])[:25]
            parts.append("<h3>Largest holdings</h3>")
            parts.append(_table(["Security", "Description", "Book", "Market value", "Gain (loss)", "Yield",
                                 "Duration"],
                                [[html.escape(h["id"]), html.escape(h["name"]), _m(h["book"]), _m(h["market"]),
                                  _m(h["gain"]), _p(h["yield"]), "%.2f" % h["duration"]] for h in holdings],
                                numeric_from=2))
    if imported is not None:
        loans = imported.summaries["loans"]
        parts.append("<h3>Loans</h3>")
        parts.append(_table(["Product", "Loans", "Balance", "Weighted rate", "Weighted remaining term (months)",
                             "60+ days delinquent"],
                            [[html.escape(l["product"].replace("_", " ")), "{:,}".format(l["count"]),
                              _m(l["balance"]), "%.2f%%" % l["rate"], "%.0f" % l["term"],
                              _p(l["delinquent"] / l["balance"], 2)] for l in loans]))
        parts.append("<h3>Certificate maturities</h3>")
        parts.append(_table(["Maturing in", "Certificates", "Balance", "Weighted rate"],
                            [[c["band"], "{:,}".format(c["count"]), _m(c["balance"]), "%.2f%%" % c["rate"]]
                             for c in imported.summaries["certificates"]]))
        parts.append("<p class='muted'>From the core files: %s. Rows that behave alike are pooled into %d "
                     "positions for the projection; <code>positions_imported.csv</code> lists them.</p>" % (
                         ", ".join("%s %s" % ("{:,}".format(n), k) for k, n in imported.rows.items()),
                         len(positions)))

    # ---- Reconciliation
    parts.append("<h2>Reconciliation</h2><p>The claim this report makes is that interest-rate risk, the "
                 "plan and liquidity are one model. These checks test it on this run.</p>")
    parts.append(_table(["Check", "Result", "Detail"], [
        [html.escape(c.name), "<span class='%s'>%s</span>" % ("pass" if c.passed else "fail",
                                                                "Pass" if c.passed else "FAIL"),
         html.escape(c.detail)] for c in checks], numeric_from=99))

    # ---- Assumptions
    parts.append("<h2>Every assumption this run used</h2>")
    parts.append(_table(["Tenor (months)", "Rate"], [[str(int(t)), "%.2f%%" % r] for t, r in
                                                    zip(a.curve.tenors, a.curve.rates)]))
    fields = ("cpr", "cpr_per_100bp", "runoff", "runoff_per_100bp", "beta", "rate_floor", "new_term",
              "spread", "discount_spread", "growth", "charge_off", "haircut", "stress_runoff")
    rows = []
    for product in sorted(a.products):
        spec = a.products[product]
        rows.append([html.escape(product)] + [
            (str(getattr(spec, f)) if f == "new_term" else "%.2f%%" % (100 * getattr(spec, f)))
            for f in fields])
    parts.append(_table(["Product"] + [f.replace("_", " ") for f in fields], rows))
    parts.append("<p class='muted'>Generated %s by Keel. Every figure is computed from the two input files; "
                 "none is typed. Runs on this machine and sends nothing anywhere.</p>" %
                 datetime.date.today().isoformat())
    parts.append("</main></body></html>")

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(parts))
    _write_months(os.path.join(out_dir, "projection_base.csv"), base_run, products)
    return {"checks": checks, "test": test, "survival": survival, "nii_year1": base_y1}


def _going(positions, a, scenario, stress=False):
    return engine.going_concern(positions, a, scenario, stress=stress)


def _write_months(path, months, products):
    fields = ["month", "cash", "overnight", "interest_income", "interest_expense", "fee_income",
              "operating_expense", "credit_losses", "net_income", "assets", "liabilities", "equity"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(fields + ["balance_" + p for p in products])
        for m in months:
            w.writerow([m.month] + ["%.2f" % getattr(m, f) for f in fields[1:]]
                       + ["%.2f" % m.balances.get(p, 0.0) for p in products])
