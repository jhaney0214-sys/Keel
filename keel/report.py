"""The ALCO report: one HTML page, readable offline and printable as a board packet.

It opens with what a meeting needs first (plain-language findings, the
headline numbers and every policy limit with its status), then the detail in
the order an ALCO packet runs: interest-rate risk, the plan, liquidity,
portfolios, the reconciliation that ties them together, and every assumption
the run used. Each chart sits above the table holding its exact numbers.

Everything shown comes from `results.compute`, as does the Excel workbook
written beside it (`results.xlsx`), so the two cannot disagree.
"""

import csv
import datetime
import html
import os

from keel import charts, export, importer, query, results as results_module, terms

STATUS = {"within": ("good", "&#10003;", "Within"), "near": ("warning", "&#9650;", "Near"),
          "breach": ("critical", "&#10005;", "Breach")}
RATING = {"Low": "within", "Moderate": "near", "High": "breach"}

# Chart colours are the dataviz reference palette's blue and its diverging
# red, validated (light and dark) against these surfaces; status colours are
# its reserved set and always travel with an icon and a word.
STYLE = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#f3f3f0;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#77766f;
--rule:#e2e1dc;--accent:#2a78d6;--series-1:#2a78d6;--neg:#e34948;--good:#0ca30c;--warning:#fab219;--critical:#d03b3b;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--surface:#1a1a19;--panel:#252523;
--ink:#f4f4f1;--ink-2:#c3c2b7;--ink-3:#9a998f;--rule:#3a3a37;--accent:#3987e5;--series-1:#3987e5;--neg:#e66767;}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#252523;--ink:#f4f4f1;--ink-2:#c3c2b7;
--ink-3:#9a998f;--rule:#3a3a37;--accent:#3987e5;--series-1:#3987e5;--neg:#e66767;}
*{box-sizing:border-box} html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Inter,Roboto,sans-serif}
header.top .inner,main,nav .inner,footer{max-width:76rem;margin:0 auto;padding-left:1rem;padding-right:1rem}
header.top{border-bottom:1px solid var(--rule)} header.top .inner{padding-top:1.6rem;padding-bottom:1rem}
h1{font-size:1.75rem;line-height:1.2;margin:0 0 .3rem;letter-spacing:-.01em}
.sub{color:var(--ink-2);margin:0 0 .3rem;font-size:.92rem}
nav{position:sticky;top:0;z-index:5;background:var(--surface);border-bottom:1px solid var(--rule)}
nav .inner{display:flex;gap:1.1rem;overflow-x:auto;white-space:nowrap;padding-top:.55rem;padding-bottom:.55rem;font-size:.88rem}
nav a{color:var(--ink-2);text-decoration:none} nav a:hover{color:var(--accent)}
main{padding-top:.4rem;padding-bottom:3rem} section,h3[id]{scroll-margin-top:3.2rem}
h2{font-size:1.3rem;margin:2.4rem 0 .9rem;padding-bottom:.45rem;border-bottom:1px solid var(--rule)}
h3{font-size:1rem;margin:1.8rem 0 .5rem}
p{margin:0 0 .75rem;max-width:64rem} .muted{color:var(--ink-2);font-size:.86rem} a{color:var(--accent)}
.findings{display:grid;gap:.5rem;margin:.4rem 0 1.2rem;padding:0;list-style:none}
.findings li{display:grid;grid-template-columns:8.5rem 1fr;gap:1rem;padding:.55rem .85rem;background:var(--panel);border-radius:4px}
.findings b{color:var(--ink-2);font-weight:600;font-size:.86rem;padding-top:.1rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr));gap:.8rem;margin:1rem 0 1.2rem}
.tile{border:1px solid var(--rule);border-radius:4px;padding:.8rem .95rem}
.tile .v{display:block;font-size:1.55rem;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.tile .l{display:block;color:var(--ink-2);font-size:.83rem;margin:.15rem 0 .4rem}
.chip{display:inline-flex;align-items:center;gap:.35rem;font-size:.78rem;font-weight:600;padding:.08rem .5rem .08rem .2rem;
border-radius:999px;border:1px solid var(--rule);color:var(--ink);white-space:nowrap}
.chip i{font-style:normal;width:1rem;height:1rem;border-radius:50%;display:inline-grid;place-items:center;font-size:.62rem;color:#fff}
.chip.good i{background:var(--good)} .chip.warning i{background:var(--warning);color:#0b0b0b} .chip.critical i{background:var(--critical)}
.default{color:var(--ink-3);font-size:.78rem}
.wrap{overflow-x:auto;margin:.4rem 0 1rem}
table{border-collapse:collapse;font-size:.87rem}
th{font-size:.74rem;color:var(--ink-2);text-align:left;font-weight:600;padding:.35rem .65rem;border-bottom:1px solid var(--ink);white-space:nowrap}
td{padding:.32rem .65rem;border-bottom:1px solid var(--rule);white-space:nowrap}
tr.total td{font-weight:600;border-top:1px solid var(--ink-2)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.pass{color:var(--good);font-weight:600} .fail{color:var(--critical);font-weight:600}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,26rem),1fr));gap:1rem 2.2rem;margin:.6rem 0 .8rem}
svg.chart{width:100%;height:auto;max-width:46rem;display:block;overflow:visible;margin:.4rem 0}
.c-title{font-size:13px;font-weight:600;fill:var(--ink)}
.c-label{font-size:11.5px;fill:var(--ink-2)} .c-value{font-size:11.5px;fill:var(--ink)}
.c-tick{font-size:10.5px;fill:var(--ink-3)}
.c-grid{stroke:var(--rule);stroke-width:1} .c-axis{stroke:var(--ink-2);stroke-width:1}
.c-limit{stroke:var(--critical);stroke-width:1.5;stroke-dasharray:4 3}
.c-pos{fill:var(--series-1)} .c-neg{fill:var(--neg)}
.c-line{fill:none;stroke:var(--series-1);stroke-width:2}
.c-dot{fill:var(--series-1);stroke:var(--surface);stroke-width:2}
.c-hit{fill:transparent} .c-mark:hover .c-hit{fill:var(--panel)}
.downloads{display:flex;flex-wrap:wrap;gap:.4rem 1.2rem;font-size:.88rem;margin:.5rem 0 0}
footer{padding-top:1.2rem;padding-bottom:3rem;color:var(--ink-3);font-size:.8rem;border-top:1px solid var(--rule)}
@media (max-width:40rem){.findings li{grid-template-columns:1fr;gap:.1rem} h1{font-size:1.4rem}}
@media print{nav,.downloads{display:none} body{font-size:10pt;background:#fff;color:#000}
h2{break-before:page;margin-top:0} #summary h2{break-before:auto}
table,svg.chart,.tile,.findings li{break-inside:avoid} *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.wrap{overflow:visible} a{color:inherit;text-decoration:none}}
"""


# --------------------------------------------------------------- formatting

def k(value):
    """Dollars in thousands, the way ALCO packets read (never "-0")."""
    return "{:,.0f}".format(value / 1000.0 + 0.0).replace("-0", "0") if abs(value) < 500 else         "{:,.0f}".format(value / 1000.0)


def pct(value, places=2):
    return ("{:,.%df}%%" % places).format(100.0 * value)


def signed(value, places=1):
    """+1.5%; a value that rounds to zero reads 0.0%, never -0.0%."""
    text = ("{:+,.%df}%%" % places).format(100.0 * value)
    return text[1:] if float(text[1:-1].replace(",", "")) == 0 else text


def signed_points(places):
    """A chart formatter for values already in percent."""
    return lambda v: signed(v / 100.0, places)


def esc(text):
    return html.escape(str(text))


def chip(status, text=None):
    cls, icon, word = STATUS[status]
    return "<span class='chip %s'><i aria-hidden='true'>%s</i>%s</span>" % (cls, icon, esc(text or word))


def table(head, rows, numeric_from=1, total_last=False):
    out = ["<div class='wrap'><table><thead><tr>"]
    out += ["<th%s>%s</th>" % (" class='num'" if i >= numeric_from else "", esc(h)) for i, h in enumerate(head)]
    out.append("</tr></thead><tbody>")
    for n, row in enumerate(rows):
        cls = " class='total'" if total_last and n == len(rows) - 1 else ""
        out.append("<tr%s>%s</tr>" % (cls, "".join(
            "<td%s>%s</td>" % (" class='num'" if i >= numeric_from else "", c) for i, c in enumerate(row))))
    out.append("</tbody></table></div>")
    return "".join(out)


def label(product):
    return esc(product.replace("_", " "))


def limit_text(x):
    unit = " months" if x.unit == "months" else "%"
    return ("at most " if x.kind == "max" else "at least ") + "%g%s" % (x.limit, unit)


def limit_value(x):
    if x.value is None:
        return "12+ months" if x.unit == "months" else "not measured"
    return "%.1f%s" % (x.value, " months" if x.unit == "months" else "%")


# --------------------------------------------------------------- sections

def summary(r):
    a, t, L = r["assumptions"], r["test"], r["liquidity"]
    limits = {x.key: x for x in r["limits"]}
    parts = ["<section id='summary'><h2>Summary</h2><ul class='findings'>"]
    parts += ["<li><b>%s</b><span>%s</span></li>" % (esc(h), esc(text)) for h, text in r["findings"]]
    parts.append("</ul><div class='tiles'>")
    worst = limits["nii_decline_300"]
    passed = sum(c.passed for c in r["checks"])
    tiles = [
        ("$" + k(r["nii_base"]["y1"]) + "K", "Year-one net interest income, base plan", ""),
        ("%+.1f%%" % -worst.value, "Year-one NII, worst of ±300bp", chip(worst.status, worst.status.title()
                                                                          if worst.status != "within" else "Within limit")),
    ]
    if terms.is_bank(a):
        ratio, decline = limits["nev_ratio_min"], limits["nev_decline_300"]
        tiles += [
            ("%.1f%%" % ratio.value, "NEV ratio after the worst ±300bp", chip(ratio.status)),
            ("%+.0f%%" % -decline.value, "NEV change, worst of ±300bp", chip(decline.status))]
    else:
        tiles += [
            (pct(t["post_shock_ratio"]), "NCUA supervisory NEV ratio after +300bp",
             chip(RATING[t["ratio_rating"]], t["ratio_rating"] + " risk")),
            (signed(-t["sensitivity_value_decline"], 0), "NCUA supervisory NEV change at +300bp",
             chip(RATING[t["sensitivity_rating"]], t["sensitivity_rating"] + " risk"))]
    tiles += [
        ("12+ months" if L["survival"] is None else "Month %d" % L["survival"],
         "Liquidity lasts, %d-month stress" % a.stress_months, chip(limits["survival_months_min"].status)),
        ("%d of %d" % (passed, len(r["checks"])), "Reconciliation checks pass",
         chip("within" if passed == len(r["checks"]) else "breach", "Tied" if passed == len(r["checks"]) else "Fails")),
    ]
    for value, text, status in tiles:
        parts.append("<div class='tile'><span class='v'>%s</span><span class='l'>%s</span>%s</div>" % (
            esc(value), esc(text), status))
    parts.append("</div><h3 id='limits'>Policy limits</h3>")
    rows = [[esc(x.label), limit_text(x) + (" <span class='default'>default</span>" if x.default else ""),
             limit_value(x), chip(x.status)] for x in r["limits"]]
    parts.append(table(["Measure", "Limit", "Today", "Status"], rows, numeric_from=2))
    defaults = sum(1 for x in r["limits"] if x.default)
    parts.append("<p class='muted'>%sNear means within %g%% of the limit.</p>" % (
        "Limits marked default are Keel's typical values, not the board's; set the board's own in the Limits sheet "
        "of the settings workbook (or \"limits\" in assumptions.json). " if defaults else
        "Every limit here is the board's, from the settings. ", a.warning_band))
    parts.append("</section>")
    return "".join(parts)


def rate_risk(r):
    a = r["assumptions"]
    limits = {x.key: x for x in r["limits"]}
    shocked_nii = [x for x in r["nii"] if x["scenario"] != "base"]
    shocked_nev = [x for x in r["nev"] if x["scenario"] != "base"]
    nii_chart = charts.diverging_bars(
        [(x["scenario"], 100 * x["y1_change"], "%s: year-one NII $%sK, %s vs base" % (
            x["scenario"], k(x["y1"]), signed(x["y1_change"]))) for x in shocked_nii],
        signed_points(1), "Year-one NII vs base", limit=limits["nii_decline_300"].limit,
        limit_label="±300bp limit", both_sides=False)
    nev_chart = charts.diverging_bars(
        [(x["scenario"], 100 * x["change"], "%s: NEV $%sK, ratio %s, %s vs base" % (
            x["scenario"], k(x["nev"]), pct(x["ratio"]), signed(x["change"]))) for x in shocked_nev],
        signed_points(0), "NEV vs base, own assumptions", limit=limits["nev_decline_300"].limit,
        limit_label="±300bp limit", both_sides=False)
    parts = ["<section id='rate-risk'><h2>Interest-rate risk</h2><div class='charts'>%s%s</div>" % (
        nii_chart, nev_chart)]
    parts.append("<h3>Net interest income and earnings at risk ($000)</h3>")
    parts.append(table(["Scenario", "Year 1 NII", "Year 1 vs base", "Year 2 NII", "24 months vs base"],
                       [[esc(x["scenario"]), k(x["y1"]), signed(x["y1_change"]), k(x["y2"]), signed(x["m24_change"])]
                        for x in r["nii"]]))
    parts.append("<p class='muted'>Going concern: balances follow the plan, and what matures or repays is replaced "
                 "at each scenario's rates. Parallel shocks move the whole curve on the analysis date and hold it; "
                 "ramps reach their move over the stated months; shaped scenarios move each point of the curve by "
                 "its own amount. The 24-month column catches the repricing a one-year view misses. Rates are "
                 "floored at %.2f%%.</p>" % a.rate_floor)
    parts.append("<h3>Net economic value, own assumptions ($000)</h3>")
    parts.append(table(["Scenario", "PV assets", "PV liabilities", "NEV", "NEV ratio", "NEV vs base"],
                       [[esc(x["scenario"]), k(x["pv_assets"]), k(x["pv_liabilities"]), k(x["nev"]), pct(x["ratio"]),
                         signed(x["change"])] for x in r["nev"]]))
    parts.append("<p class='muted'>NEV need not move in a straight line: floors on share rates stop liability costs "
                 "falling in the down shocks while their present value keeps rising.</p>")
    if not terms.is_bank(a):
        t, s = r["test"], r["supervisory"]
        parts.append("<h3>NCUA NEV Supervisory Test ($000)</h3>")
        parts.append(table(["Supervisory basis", "PV assets", "PV liabilities", "NEV", "NEV ratio"],
                           [[esc(key), k(v["pv_assets"]), k(v["pv_liabilities"]), k(v["nev"]), pct(v["ratio"])]
                            for key, v in s.items()]))
        parts.append("<p>At +300bp: post-shock NEV ratio <b>%s</b> %s &nbsp; NEV change <b>%s</b> %s</p>" % (
            pct(t["post_shock_ratio"]), chip(RATING[t["ratio_rating"]], t["ratio_rating"]),
            signed(-t["sensitivity_value_decline"]), chip(RATING[t["sensitivity_rating"]], t["sensitivity_rating"])))
        parts.append("<p class='muted'>Non-maturity shares at NCUA's standardized 99.00 (base) and 95.04 (+300bp); every "
                     "other position at its modelled value. Thresholds from Letter SL 22-01: ratio above 7%% low, 4-7%% "
                     "moderate, below 4%% high; NEV decline below 40%% low, 40-65%% moderate, above 65%% high. The fall "
                     "in the ratio itself, not rated: %s.</p>" % pct(t["sensitivity_ratio_decline"], 1))
    gap = r["gap"]
    parts.append("<h3>Repricing gap</h3>")
    parts.append(charts.columns(
        [(_short_band(g["band"]), g["gap"] / 1e6, "%s: assets $%sK, liabilities $%sK, gap $%sK; cumulative %s of "
          "assets" % (g["band"], k(g["assets"]), k(g["liabilities"]), k(g["gap"]), pct(g["cumulative_to_assets"], 1)))
         for g in gap], _millions, "Gap by repricing band ($ millions)", signed=True))
    parts.append(table(["Band", "Assets repricing", "Liabilities repricing", "Gap", "Cumulative gap",
                        "Cumulative / assets"],
                       [[esc(g["band"]), k(g["assets"]), k(g["liabilities"]), k(g["gap"]), k(g["cumulative"]),
                         pct(g["cumulative_to_assets"], 1)] for g in gap]))
    parts.append("<p class='muted'>Base scenario, $000. Variable-rate positions count in full at their next reset; "
                 "everything else by its principal cash flows, including prepayment and share decay. Not "
                 "rate-sensitive: assets %s, liabilities %s.</p>" % (k(r["insensitive"]["asset"]),
                                                                      k(r["insensitive"]["liability"])))
    parts.append(assumptions_test_section(r))
    parts.append("</section>")
    return "".join(parts)


def _short_band(band):
    return (band.replace(" months", "m").replace(" month", "m").replace(" years", "y").replace(" year", "y")
            .replace("over ", ">").replace(" to ", "-").replace("-", "–"))


def _millions(v):
    return ("%+.0fM" if abs(v) >= 10 else "%+.1fM") % v


def plan(r):
    p = r["plan"]
    years = ["Year %d" % y for y in range(1, p["years"] + 1)]
    nw_limit = next(x.limit for x in r["limits"] if x.key == "net_worth_min")
    income = charts.columns([(y, s["net_income"] / 1e6, "%s: net income $%sK" % (y, k(s["net_income"])))
                             for y, s in zip(years, p["statements"])],
                            lambda v: "$%.1fM" % v, "Net income by year, base plan ($ millions)",
                            signed=any(s["net_income"] < 0 for s in p["statements"]))
    path = p["net_worth_path"]
    worth = charts.line([(m, 100 * v, "Month %d: net worth ratio %.2f%%" % (m, 100 * v))
                         for m, v in enumerate(path, 1) if m % 6 == 0 or m == 1],
                        lambda v: "%.1f%%" % v, "Net worth ratio, base plan", reference=nw_limit,
                        reference_label="limit %g%%" % nw_limit, zero=False)
    case = {"flat": "today's curve, held for the whole plan", "forward": "the curve's implied forward rates",
            "forecast": "the rate forecast in the settings (Forecast sheet)"}[r["assumptions"].base_case]
    parts = ["<section id='plan'><h2>The plan</h2><p class='muted'>Base case: %s. Every scenario is a shock on "
             "top of it; NEV and market values stay on today's curve.</p><div class='charts'>%s%s</div>" % (
                 case, income, worth)]
    lines = (("Interest income", "interest_income"), ("Interest expense", "interest_expense"),
             ("Net interest income", "net_interest_income"), ("Fee and other income", "fee_income"),
             ("Operating expense", "operating_expense"), ("Credit losses", "credit_losses"))
    if any(x["income_tax"] for x in p["statements"]):
        lines += (("Income tax", "income_tax"),)
    lines += (("Net income", "net_income"),)
    parts.append("<h3>Income statement, base scenario ($000)</h3>")
    parts.append(table(["Line"] + years, [[n] + [k(s[key]) for s in p["statements"]] for n, key in lines],
                       total_last=True))
    ratio_lines = (("Yield on average assets", "yield_on_assets"), ("Cost of funds", "cost_of_funds"),
                   ("Net interest margin", "nim"), ("Return on average assets", "roa"),
                   ("Efficiency ratio", "efficiency"), ("Net worth ratio, year end", "net_worth_ratio"))
    parts.append("<h3>Ratios</h3>")
    parts.append(table(["Ratio"] + years, [[n] + [pct(x[key]) for x in p["ratios"]] for n, key in ratio_lines]))
    parts.append("<p class='muted'>On average balances (the opening and each month end). Efficiency is operating "
                 "expense over net interest income plus fees: lower is better.</p>")
    parts.append("<h3>Balance sheet at each year end ($000)</h3>")
    rows = [[label(b["line"])] + [k(v) for v in b["values"]] for b in p["balance_sheet"]]
    rows += [["Total assets"] + [k(x["assets"]) for x in p["totals"]],
             ["Total liabilities"] + [k(x["liabilities"]) for x in p["totals"]],
             ["Net worth"] + [k(x["equity"]) for x in p["totals"]]]
    parts.append(table(["Line"] + years, rows, total_last=True))
    parts.append(credit_section(r))
    parts.append("<h3>The plan under each scenario ($000)</h3>")
    parts.append(table(["Scenario", "Year 1 net income", "Net worth ratio, month 12", "Peak overnight borrowing"],
                       [[esc(x["scenario"]), k(x["net_income_y1"]), pct(x["net_worth_m12"]), k(x["peak_overnight"])]
                        for x in p["by_scenario"]]))
    parts.append("</section>")
    return "".join(parts)


def liquidity(r):
    L = r["liquidity"]
    limits = {x.key: x for x in r["limits"]}
    parts = ["<section id='liquidity'><h2>Liquidity</h2>"]
    if not terms.is_bank(r["assumptions"]):
        parts.append("<p><b>Regulatory tier (12 CFR 741.12):</b> %s. Total assets $%sK.</p>" % (
            esc(L["tier"]), k(r["opening"]["assets"])))
    rows = []
    for key in ("liquid_to_shares_min", "loans_to_shares_max", "borrowings_to_assets_max"):
        x = limits[key]
        rows.append([esc(x.label), limit_value(x), limit_text(x), chip(x.status)])
    parts.append(table(["Ratio", "Today", "Limit", "Status"], rows))
    parts.append(charts.line(
        [(m["month"], m["available"] / 1e6, "Month %d: available $%sK (cash $%sK, liquid $%sK, overnight $%sK)" % (
            m["month"], k(m["available"]), k(m["cash"]), k(m["liquid"]), k(m["overnight"]))) for m in L["stress"]],
        lambda v: "$0" if not v else "$%.0fM" % v if abs(v) >= 10 else "$%.1fM" % v,
        "Available liquidity through the stress ($ millions)", reference=0.0, reference_label="none left"))
    parts.append(table(["Month", "Cash", "Liquid investments after haircut", "Overnight borrowing", "Available"],
                       [[str(m["month"]), k(m["cash"]), k(m["liquid"]), k(m["overnight"]), k(m["available"])]
                        for m in L["stress"]]))
    parts.append("<p class='muted'>$000. %d months of share runoff (each product's stress runoff on top of its decay) "
                 "while loans keep funding to plan. Available liquidity is cash above the minimum, liquid investments "
                 "after haircut and contingent sources (%s), less borrowing already drawn; measured over the first "
                 "twelve months.</p>" % (L["stress_months"], esc(", ".join(
                     "%s $%sK" % (n, k(c)) for n, c in L["contingent"])) or "none"))
    parts.append("<p><b>The plan's own funding need:</b> %s</p>" % (
        "the base plan never borrows overnight." if L["funding_peak"] <= 0 else
        "the base plan borrows up to $%sK overnight, in month %d, because loans grow faster than shares. That is a "
        "funding decision the plan has to make, whatever the stress shows." % (k(L["funding_peak"]),
                                                                              L["funding_peak_month"])))
    parts.append("<h3>Contractual gap, today's positions only ($000)</h3>")
    parts.append(table(["Month", "Net inflow", "Cumulative"], [[str(m), k(n), k(c)] for m, n, c in L["contractual"]]))
    parts.append(liquidity_scenarios(r))
    parts.append(liquidity_collateral(r))
    parts.append(liquidity_concentration(r))
    parts.append("</section>")
    return "".join(parts)


def portfolios(r):
    groups, securities, imported = r["security_groups"], r["securities"], r["imported"]
    if not securities and imported is None:
        return ""
    parts = ["<section id='portfolios'><h2>Portfolios</h2>"]
    if securities:
        parts.append("<h3>Investments by type ($000)</h3>")
        book = sum(g["book"] for g in groups)
        market = sum(g["market"] for g in groups)
        rows = [[label(g["product"]), str(g["count"]), k(g["book"]), k(g["market"]), k(g["gain"]),
                 pct(g["gain"] / g["book"] if g["book"] else 0.0, 1), pct(g["yield"]), "%.1f" % g["wal"],
                 "%.2f" % g["duration"]] for g in groups]
        rows.append(["Total", str(len(securities)), k(book), k(market), k(market - book),
                     pct((market - book) / book if book else 0.0, 1), "", "", ""])
        parts.append(table(["Type", "Holdings", "Book", "Market value", "Gain (loss)", "Gain / book", "Book yield",
                            "WAL (years)", "Eff. duration"], rows, total_last=True))
        parts.append("<p class='muted'>Market value is each holding's cash flows discounted on the base curve plus "
                     "its product's discount spread; effective duration is from ±100bp. FHLB stock and other stakes "
                     "with no maturity count at book. Callables are called when their coupon beats the market by "
                     "the product's threshold. Every holding is in <code>results.xlsx</code>.</p>")
        holdings = sorted(securities, key=lambda x: -x["book"])[:20]
        parts.append("<h3>Largest holdings ($000)</h3>")
        parts.append(table(["Security", "Description", "Book", "Market value", "Gain (loss)", "Yield", "Duration"],
                           [[esc(h["id"]), esc(h["name"]), k(h["book"]), k(h["market"]), k(h["gain"]), pct(h["yield"]),
                             "%.2f" % h["duration"]] for h in holdings], numeric_from=2))
    if imported is not None:
        parts.append("<h3>Loans ($000)</h3>")
        parts.append(table(["Product", "Loans", "Balance", "Contract rate", "Remaining term (months)",
                            "60+ days delinquent", "Non-accrual (90+)"],
                           [[label(x["product"]), "{:,}".format(x["count"]), k(x["balance"]), "%.2f%%" % x["rate"],
                             "%.0f" % x["term"], pct(x["delinquent"] / x["balance"]) if x["balance"] else "",
                             k(x["nonaccrual"])] for x in imported.summaries["loans"]]))
        parts.append("<p class='muted'>Loans %d or more days past due are on non-accrual: they pool apart at a zero "
                     "rate, so projected interest income excludes them.</p>" % importer.NONACCRUAL_DAYS)
        parts.append("<h3>Certificate maturities ($000)</h3>")
        parts.append(table(["Maturing in", "Certificates", "Balance", "Rate"],
                           [[esc(x["band"]), "{:,}".format(x["count"]), k(x["balance"]), "%.2f%%" % x["rate"]]
                            for x in imported.summaries["certificates"]]))
        parts.append("<p class='muted'>From the core files: %s. Rows that behave alike are pooled into %d positions "
                     "for the projection; <code>positions_imported.csv</code> lists them.</p>" % (
                         esc(", ".join("%s %s" % ("{:,}".format(n), key) for key, n in imported.rows.items())),
                         r["positions"]))
    parts.append("</section>")
    return "".join(parts)


def profitability_section(r):
    a = r["assumptions"]
    P = r["profitability"]
    lines, tot = P["lines"], P["totals"]
    loans = [x for x in lines if x.side == "asset" and x.capital > 0 and x.balance > 0 and x.interest > 0]
    parts = ["<section id='profitability'><h2>Profitability, FTP and capital</h2>"]
    parts.append("<p>On today's balances, annualized. Each product is charged (assets) or credited (liabilities) "
                 "for its funding at a funds transfer price matched to its own cash flows; what it earns over that "
                 "is its spread, and treasury keeps the rest: <b>$%sK</b> of the <b>$%sK</b> run-rate net interest "
                 "income is the balance sheet's rate mismatch. Capital is allocated at %g%% of risk-weighted assets; "
                 "RAROC is net income over that capital, against a %g%% hurdle.</p>" % (
                     k(tot["treasury"]), k(tot["nii"]), round(100 * a.target_capital, 2), round(100 * a.hurdle_rate, 2)))
    if loans:
        parts.append(charts.diverging_bars(
            [(label_text(x.product), 100 * x.raroc, "%s: RAROC %s, spread %s, net $%sK on capital $%sK" % (
                x.product, pct(x.raroc, 1), pct(x.rate(x.spread)), k(x.net), k(x.capital))) for x in loans],
            lambda v: "%.0f%%" % v, "RAROC by product", reference=100 * a.hurdle_rate,
            reference_label="hurdle %s" % pct(a.hurdle_rate, 0)))
        rows = []
        for x in loans:
            status = "within" if x.raroc >= a.hurdle_rate else "near" if x.raroc >= 0 else "breach"
            rows.append([label(x.product), k(x.balance), pct(x.rate(x.interest)), pct(x.rate(x.ftp)),
                         pct(x.rate(x.spread)), pct(x.rate(x.fees - x.servicing)), pct(x.rate(x.expected_loss)),
                         pct(x.rate(x.net)), k(x.capital), pct(x.raroc, 1) + " " + chip(
                             status, {"within": "Clears", "near": "Below", "breach": "Loses"}[status])])
        parts.append("<h3>Loans and investments ($000)</h3>")
        parts.append(table(["Product", "Balance", "Yield", "FTP", "Spread", "Fees less servicing", "Expected loss",
                            "ROA", "Capital", "RAROC"], rows))
    deposits = [x for x in lines if x.side == "liability" and x.balance > 0 and (x.interest or x.ftp)]
    parts.append("<h3>Deposits and borrowings ($000)</h3>")
    parts.append(table(["Product", "Balance", "Rate paid", "FTP credit", "Spread", "Fees less servicing",
                        "Contribution", "Contribution / balance"],
                       [[label(x.product), k(x.balance), pct(x.rate(x.interest)), pct(x.rate(x.ftp)),
                         pct(x.rate(x.spread)), pct(x.rate(x.fees - x.servicing)), k(x.net), pct(x.rate(x.net))]
                        for x in deposits]))
    parts.append("<h3>From products to the institution ($000, annual run-rate)</h3>")
    rows = [["Product spreads over FTP", k(tot["product_spread"])], ["Capital credit", k(tot["capital_credit"])],
            ["Treasury margin (rate mismatch)", k(tot["treasury"])], ["= Net interest income", k(tot["nii"])],
            ["Fees carried by products", k(tot["fees"])], ["Fees not allocated", k(tot["unallocated_fees"])],
            ["Servicing carried by products", k(-tot["servicing"])],
            ["Operating expense not allocated", k(-tot["unallocated_expense"])],
            ["Expected loss", k(-tot["expected_loss"])]]
    if tot["tax"]:
        rows.append(["Income tax", k(-tot["tax"])])
    rows.append(["Run-rate net income", k(tot["net"])])
    parts.append(table(["Line", "Amount"], rows, total_last=True))
    ratio = P["capital_ratio"]
    parts.append("<p class='muted'>Risk-weighted assets $%sK; net worth to risk-weighted assets %s. Weights are each "
                 "product's <code>risk_weight</code>, or 100%% for loans and other assets, 20%% for liquid investments "
                 "and 0%% for cash where none is set: a simplified risk-based measure, not a regulatory filing. The "
                 "run-rate is today's book held for a year at today's rates; the plan above grows and reprices it. "
                 "Price a single deal with <code>python -m keel price</code> or the Pricing page of <code>keel "
                 "serve</code>.</p>" % (k(P["rwa"]), "n/a" if ratio is None else pct(ratio)))
    parts.append("</section>")
    return "".join(parts)


def accounts_section(r):
    acc = r.get("accounts")
    if not acc:
        return ""
    t = acc["totals"]
    parts = ["<section id='accounts'><h2>Accounts, members and branches</h2>"]
    parts.append("<p>Every loan and certificate%s priced as its product is: spread over the funds transfer price, "
                 "capital credit, fees, servicing, expected loss and a fixed cost per account (each product's "
                 "<code>account_cost</code>). %s accounts, contribution <b>$%sK</b> a year before the rest of "
                 "overhead.</p>" % (
                     " and each member's share balance" if any(p["kind"] == "share" for p in acc["products"]) else "",
                     "{:,}".format(t["accounts"]), k(t["net"])))
    rows = []
    for p in acc["products"]:
        rows.append([label(p["product"]), "{:,}".format(p["accounts"]), k(p["balance"]),
                     "{:,.0f}".format(p["balance"] / p["accounts"]), k(p["account_cost"]), k(p["net"]),
                     "{:,.0f}".format(p["average_net"]), pct(p["losing_share"], 0),
                     "" if p["breakeven_balance"] is None else "{:,.0f}".format(p["breakeven_balance"])])
    parts.append("<h3>By product ($000 unless shown in dollars)</h3>")
    parts.append(table(["Product", "Accounts", "Balance", "Average balance ($)", "Account costs", "Contribution",
                        "Per account ($)", "Accounts losing money", "Break-even balance ($)"], rows))
    if acc["unallocated_expense"]:
        parts.append("<p class='muted'>Account costs take $%sK of the $%sK of operating expense the products' "
                     "servicing does not carry. The break-even balance is where an account's margin at its "
                     "product's average rates covers its account cost.</p>" % (
                         k(t["account_cost"]), k(acc["unallocated_expense"])))
    if acc["members"]:
        losing = t["losing_members"]
        parts.append("<h3>Members</h3><p>%s members; <b>%s</b> (%s) contribute less than they cost.%s</p>" % (
            "{:,}".format(t["members"]), "{:,}".format(losing), pct(losing / float(t["members"]), 0),
            " %s accounts carry no member number and are left out of the member view." % "{:,}".format(t["unassigned"])
            if t["unassigned"] else ""))
        if acc["whale"]:
            parts.append(charts.line(
                [(int(round(100 * x)), 100 * y, "the top %.0f%% of members: %.0f%% of the contribution" % (100 * x, 100 * y))
                 for x, y in acc["whale"][::5]], lambda v: "%.0f%%" % v,
                "Cumulative contribution, members ranked most profitable first", x_label="share of members",
                reference=100.0, reference_label="", x_ticks={0, 20, 40, 60, 80, 100},
                x_fmt=lambda v: "%.0f%%" % v))
            parts.append("<p class='muted'>The whale curve: the dashed line is the whole contribution. It rises above it while the profitable members add "
                         "to it, and falls back as the members who lose money take their share away.</p>")
        parts.append(table(["Decile", "Members", "Loans", "Deposits", "Contribution", "Per member ($)",
                            "Products per member"],
                           [[str(d["decile"]), "{:,}".format(d["members"]), k(d["loans"]), k(d["deposits"]),
                             k(d["net"]), "{:,.0f}".format(d["average_net"]), "%.1f" % d["products"]]
                            for d in acc["deciles"]]))
        parts.append("<h3>By relationship</h3>")
        parts.append(table(["Relationship", "Members", "Loans", "Deposits", "Contribution", "Per member ($)",
                            "Losing money"],
                           [[esc(g["relationship"]), "{:,}".format(g["members"]), k(g["loans"]), k(g["deposits"]),
                             k(g["net"]), "{:,.0f}".format(g["average_net"]), pct(g["losing_share"], 0)]
                            for g in acc["relationships"]]))
        if any(g["branch"] != "(none)" for g in acc["branches"]):
            parts.append("<h3>By branch</h3>")
            parts.append(table(["Branch", "Members", "Loans", "Deposits", "Contribution", "Per member ($)",
                                "Losing money"],
                               [[esc(g["branch"]), "{:,}".format(g["members"]), k(g["loans"]), k(g["deposits"]),
                                 k(g["net"]), "{:,.0f}".format(g["average_net"]), pct(g["losing_share"], 0)]
                                for g in acc["branches"]]))
            parts.append("<p class='muted'>A member belongs to the branch that holds most of their balance.</p>")
    else:
        parts.append("<p class='muted'>Add <code>member_id</code> (and <code>branch</code>) columns to loans.csv and "
                     "certificates.csv, and <code>data/member_shares.csv</code> (member_id, product_code, balance), "
                     "to read this by member and branch.</p>")
    parts.append("<p class='muted'>Annual run-rate on today's balances, as the product view. Query every account "
                 "or member with <code>keel query --table accounts</code> or <code>--table members</code>, or the "
                 "Explore page.</p></section>")
    return "".join(parts)


def label_text(product):
    return product.replace("_", " ")


def budget_section(r):
    b, v = r["budget"], r["variance"]
    short = [m[2:] for m in b["labels"]]
    parts = ["<section id='budget'><h2>Budget</h2>"]
    parts.append("<p>The first year of the base plan, month by month: the same projection the rate-risk and "
                 "liquidity numbers come from. Every product's monthly balance, interest and yield is in "
                 "<code>results.xlsx</code>.</p>")
    keys = (("Interest income", "interest_income"), ("Interest expense", "interest_expense"),
            ("Net interest income", "nii"), ("Fee and other income", "fee_income"),
            ("Operating expense", "operating_expense"), ("Credit losses", "credit_losses"))
    if any(m["income_tax"] for m in b["income"]):
        keys += (("Income tax", "income_tax"),)
    keys += (("Net income", "net_income"),)
    parts.append("<h3>Income statement by month ($000)</h3>")
    parts.append(table(["Line"] + short + ["Year"], [[n] + [k(m[key]) for m in b["income"]] + [
        k(sum(m[key] for m in b["income"]))] for n, key in keys], total_last=True))
    rows = []
    for p in b["products"]:
        avg = sum(p["average"]) / len(p["average"])
        interest = sum(p["interest"])
        if avg <= 0 and interest == 0:
            continue
        rows.append([label(p["product"]), k(p["end"][-1]), k(avg), k(interest), pct(interest / avg if avg else 0.0)])
    if b["lines"]:
        parts.append("<h3>Non-interest income and expense by month ($000)</h3>")
        extra_rows = []
        for kind, title in (("income", "Income"), ("expense", "Expense")):
            chosen = [x for x in b["lines"] if x["kind"] == kind]
            extra_rows += [[esc(x["line"])] + [k(v) for v in x["months"]] + [k(sum(x["months"]))] for x in chosen]
            if chosen:
                months = [sum(x["months"][t] for x in chosen) for t in range(len(short))]
                extra_rows.append(["<b>Total %s</b>" % kind] + ["<b>%s</b>" % k(v) for v in months] +
                            ["<b>%s</b>" % k(sum(months))])
        parts.append(table(["Line"] + short + ["Year"], extra_rows))
    if b["drivers"]:
        parts.append("<h3>Budget drivers</h3>")
        grid = {}
        for d in b["drivers"]:
            for kind in ("volume", "balance", "rate"):
                if d[kind] is not None:
                    grid.setdefault((d["product"], kind), {})[d["plan_month"]] = d[kind]
        extra_rows = []
        names = {"volume": "new volume ($000)", "balance": "month-end balance ($000)", "rate": "offering rate"}
        for (product, kind), by_month in sorted(grid.items()):
            cells = []
            for m in range(1, len(short) + 1):
                amount = by_month.get(m)
                cells.append("" if amount is None else pct(amount) if kind == "rate" else k(amount))
            later = [m for m in by_month if m > len(short)]
            extra_rows.append(["%s: %s" % (label(product), names[kind])] + cells + ["+%d later" % len(later) if later else ""])
        parts.append(table(["Driver"] + short + [""], extra_rows))
        parts.append("<p class='muted'>A volume is new business that month; a balance is where the product should "
                     "end the month; an offering rate prices new business (and, for a share product, the whole "
                     "balance) from that month on, and a scenario still moves it by its shift or beta. Between "
                     "and after drivers a product grows at its planned rate from where the last driver left it.</p>")
    parts.append("<h3>By product, budget year ($000)</h3>")
    parts.append(table(["Product", "Year-end balance", "Average balance", "Interest", "Yield"], rows))
    if v:
        parts.append("<h3 id='variance'>Actual against budget, through %s</h3>" % esc(v["through"]))
        net = next(x for x in v["statement"] if x["line"] == "Net income")
        parts.append("<p>Net income is <b>$%sK</b> %s budget over %d month%s. Each product's effect on net interest "
                     "income splits into volume (its balance differed) and rate (its yield differed).</p>" % (
                         k(abs(net["variance"])), "ahead of" if net["variance"] >= 0 else "behind", v["months"],
                         "s" if v["months"] > 1 else ""))
        moved = sorted(v["products"], key=lambda x: -abs(x["nii_variance"]))[:12]
        if moved:
            parts.append(charts.diverging_bars(
                [(label_text(x["product"]), x["nii_variance"] / 1000.0, "%s: volume $%sK, rate $%sK" % (
                    x["product"], k(x["volume"]), k(x["rate"]))) for x in moved],
                lambda value: "%+.0fK" % value, "Effect on net interest income, year to date ($000)"))
        parts.append(table(["Product", "Budget balance", "Actual balance", "Budget yield", "Actual yield", "Volume",
                            "Rate", "Effect on NII"],
                           [[label(x["product"]), k(x["budget_balance"]), k(x["actual_balance"]),
                             pct(x["budget_yield"]), pct(x["actual_yield"]), k(x["volume"]), k(x["rate"]),
                             k(x["nii_variance"])] for x in v["products"]]))
        parts.append(table(["Line", "Budget", "Actual", "Better (worse)"],
                           [[esc(x["line"]), k(x["budget"]), k(x["actual"]), k(x["variance"])] for x in v["statement"]],
                           total_last=True))
        source = ("the general ledger's trial balance (<code>trial_balance</code>, mapped by <code>gl_map</code>)"
                  if v.get("source") == "trial_balance" else "<code>actuals.csv</code>")
        parts.append("<p class='muted'>From " + source + ", $000. A product the actuals leave out counts as "
                     "on budget. Volume is the balance difference at the budget's yield; rate is the rest.</p>")
    else:
        parts.append("<p class='muted'>Add the general ledger's monthly <code>trial_balance.csv</code> with a "
                     "<code>gl_map.csv</code>, or <code>actuals.csv</code> (month, line, average_balance, amount), to the "
                     "folder to compare actual results with this budget, product by product.</p>")
    parts.append("</section>")
    return "".join(parts)


def adhoc_section(r):
    if not r["queries"]:
        return ""
    parts = ["<section id='adhoc'><h2>Ad hoc reports</h2><p class='muted'>Each saved query in the folder's "
             "<code>queries/</code> runs on every report. Write one, or build one on the Explore page of "
             "<code>keel serve</code> and save it there.</p>"]
    for q in r["queries"]:
        parts.append("<h3>%s</h3>" % esc(q["name"]))
        rows = [[esc(query.fmt(c)) for c in row] for row in q["rows"][:60]]
        if q["total"]:
            rows.append([esc(query.fmt(c)) for c in q["total"]])
        by = len(q["columns"]) - len([c for c in q["columns"] if " " in c or c == "count"])
        parts.append(table(q["columns"], rows, numeric_from=max(by, 1), total_last=bool(q["total"])))
        parts.append("<p class='muted'>From %s: %s of %s rows matched%s.</p>" % (
            esc(q["table"]), "{:,}".format(q["matched"]), "{:,}".format(q["of"]),
            "; first 60 groups shown" if len(q["rows"]) > 60 else ""))
    parts.append("</section>")
    return "".join(parts)


def assumptions_test_section(r):
    t = r.get("sensitivity")
    if not t:
        return ""
    base = t["baseline"]
    limits = {x.key: x for x in r["limits"]}
    parts = ["<h3 id='assumption-tests'>Key assumptions: what the answer depends on</h3>"]
    parts.append("<p class='muted'>Each assumption moved on its own, everything else held, and the rate-risk measures "
                 "run again. An assumption whose plausible range changes a limit's status is one the institution "
                 "needs its own evidence for: a deposit study for betas and decay, prepayment history for speeds.</p>")
    parts.append(charts.diverging_bars(
        [("deposit study" if x["family"] == "Deposit study" else "%s %s" % (x["family"].lower(),
                                                                        x["variant"].split(" ")[0]),
          x["values"]["nev_decline_300"] - base["nev_decline_300"],
          "%s %s: NEV decline %.1f%% (base %.1f%%), NEV ratio %.1f%%, NII decline %.1f%%" % (
              x["family"], x["variant"], x["values"]["nev_decline_300"], base["nev_decline_300"],
              x["values"]["nev_ratio_min"], x["values"]["nii_decline_300"])) for x in t["rows"]],
        lambda v: "%+.1f pts" % v, "Change in the worst NEV decline (±300bp), points"))
    rows = [["<b>As assumed</b>", "", k(base["nii_y1"]), "%.1f%%" % base["nii_decline_300"],
             "%.1f%%" % base["nev_decline_300"], "%.1f%%" % base["nev_ratio_min"],
             " ".join(chip(t["status"][key], limits[key].label.split(",")[0]) for key in ("nii_decline_300", "nev_decline_300",
                                                                                          "nev_ratio_min")
                      if t["status"][key] != "within") or chip("within", "All within")]]
    for x in t["rows"]:
        flips = " ".join(chip(x["status"][key], "%s now %s" % (limits[key].label.split(",")[0],
                                                               STATUS[x["status"][key]][2].lower()))
                         for key in x["flips"])
        rows.append([esc(x["family"]), esc(x["variant"]), k(x["values"]["nii_y1"]),
                     "%.1f%%" % x["values"]["nii_decline_300"], "%.1f%%" % x["values"]["nev_decline_300"],
                     "%.1f%%" % x["values"]["nev_ratio_min"], flips or "no change"])
    parts.append(table(["Assumption", "Variant", "Year-1 NII ($000)", "NII decline, worst ±300", "NEV decline, worst ±300",
                        "NEV ratio, worst ±300", "Limit status"], rows, numeric_from=2))
    parts.append("<p class='muted'>Betas, decay, speeds and spreads are scaled across every product that has them. "
                 "Decay changes NEV, not the plan's NII: the plan holds share balances to their growth path.</p>")
    return "".join(parts)


def history_section(r):
    h = r.get("history")
    if h is None:
        return ""
    parts = ["<section id='history'><h2>History and back-test</h2>"]
    trend = h["trend"]
    if len(trend) < 2:
        parts.append("<p>This is the first run saved for this institution. From the next analysis date on, this "
                     "section shows the trend quarter by quarter, every assumption changed since, and how this "
                     "run's forecast compares with what actually happened. Each run is saved in "
                     "<code>history/</code>.</p></section>")
        return "".join(parts)
    parts.append("<h3>Trend</h3>")
    parts.append(table(["Analysis date", "Assets ($000)", "Year-1 NII ($000)", "NII decline, worst ±300", "NEV ratio, worst ±300",
                        "NEV decline, worst ±300", "Supervisory NEV ratio", "Net worth ratio", "Capital to RWA",
                        "Limits breached"],
                       [[esc(x["as_of"]), k(x["assets"]), k(x["nii_y1"]), "%.1f%%" % x["nii_decline_300"],
                         "%.1f%%" % x["nev_ratio_min"], "%.1f%%" % x["nev_decline_300"], "%.2f%%" % x["supervisory_ratio"],
                         "%.2f%%" % x["net_worth_ratio"],
                         "" if x.get("capital_to_rwa") is None else "%.1f%%" % x["capital_to_rwa"],
                         str(x["breaches"])] for x in trend]))
    parts.append("<h3>Assumptions changed since %s</h3>" % esc(h["prior"]))
    if h["changes"]:
        parts.append(table(["Assumption", "Was", "Now"],
                           [[esc(w), esc(_plain(o)), esc(_plain(n))] for w, o, n in h["changes"]], numeric_from=1))
        parts.append("<p class='muted'>Record why each changed: the change log is part of model governance.</p>")
    else:
        parts.append("<p>None: every assumption is as it was.</p>")
    b = h["backtest"]
    if b:
        parts.append("<h3>Back-test: what the %s run forecast for today</h3>" % esc(b["from"]))
        moved = b["short_actual"] - b["short_forecast"]
        parts.append("<p>%d month%s on. That run assumed a short rate of %.2f%% by now; it is %.2f%% (%+d bp), so "
                     "part of any miss below is the rate environment, not behaviour.</p>" % (
                         b["months"], "s" if b["months"] > 1 else "", b["short_forecast"], b["short_actual"],
                         round(100 * moved)))
        rows = []
        for x in b["products"]:
            if x["product"] == "cash" or (x["forecast_rate"] in (None, 0.0) and x["actual_rate"] in (None, 0.0)):
                continue
            flag = ""
            if x["error_pct"] is not None and abs(x["error_pct"]) >= 0.05:
                flag = chip("near" if abs(x["error_pct"]) < 0.10 else "breach", "%+.0f%%" % (100 * x["error_pct"]))
            rate_gap = ("%+d bp" % round(10000 * (x["actual_rate"] - x["forecast_rate"]))
                        if x["forecast_rate"] is not None and x["actual_rate"] is not None else "")
            rows.append([label(x["product"]), k(x["forecast"]), k(x["actual"]), k(x["error"]),
                         "" if x["error_pct"] is None else "%+.1f%%" % (100 * x["error_pct"]),
                         "" if x["forecast_rate"] is None else pct(x["forecast_rate"]),
                         "" if x["actual_rate"] is None else pct(x["actual_rate"]), rate_gap, flag])
        parts.append(table(["Product", "Forecast balance", "Actual balance", "Miss ($000)", "Miss", "Forecast rate",
                            "Actual rate", "Rate miss", ""], rows))
        parts.append("<p class='muted'>A deposit balance that misses shows the decay or growth assumption at work; an "
                     "administered rate that misses shows the beta. Misses of 5% or more are marked. Cash, the account that "
                     "settles everything, and non-earning lines are left out. The forecast "
                     "rate is the plan's interest over its average balance in that month.</p>")
        known = [x for x in b["nii"] if x["actual"] is not None]
        if known:
            f = sum(x["forecast"] for x in known)
            a_ = sum(x["actual"] for x in known)
            parts.append(table(["Month", "Forecast NII ($000)", "Actual NII ($000)", "Miss"],
                               [[esc(x["month"]), k(x["forecast"]), "" if x["actual"] is None else k(x["actual"]),
                                 "" if x["actual"] is None else "%+.1f%%" % (100 * (x["actual"] / x["forecast"] - 1))]
                                for x in b["nii"]] + [["Total", k(f), k(a_), "%+.1f%%" % (100 * (a_ / f - 1))]],
                               total_last=True))
        else:
            parts.append("<p class='muted'>Add those months to <code>actuals.csv</code> or the trial balance to back-test net interest "
                         "income as well.</p>")
    parts.append("</section>")
    return "".join(parts)


def _plain(v):
    if isinstance(v, float):
        return "%g" % round(v, 6)
    return "" if v is None else str(v)


def peers_section(r):
    p = r.get("peers")
    if not p:
        return ""
    parts = ["<section id='peers'><h2>Peers</h2><p>Against the %s credit unions in NCUA's %s peer group, from "
             "the %s call report. Percentile is the share of peers below this credit union.</p>" % (
                 "{:,}".format(p["count"]), esc(p["peer_group"]), esc(p["cycle"]))]
    rows = []
    for x in p["ratios"]:
        tag = ""
        if x["better"]:
            good = x["percentile"] >= 50 if x["better"] == "higher" else x["percentile"] <= 50
            extreme = x["percentile"] <= 10 or x["percentile"] >= 90
            if extreme:
                tag = chip("within" if good else "breach", "Top tenth" if good else "Bottom tenth")
        rows.append([esc(x["label"]), "%.2f%%" % x["value"], "%.2f%%" % x["p25"], "%.2f%%" % x["median"],
                     "%.2f%%" % x["p75"], "%.0f" % x["percentile"], tag])
    parts.append(table(["Ratio", "This credit union", "Peer 25th", "Peer median", "Peer 75th", "Percentile", ""],
                       rows))
    parts.append("<p class='muted'>Ratios from the call report as filed, income annualized. Top and bottom tenth "
                 "are marked where a direction is better; for the others (loans to shares, liquidity, funding "
                 "mix) the peer range is context, not a grade.</p></section>")
    return "".join(parts)


def deposits_section(r):
    d = r.get("deposits")
    if not d or not d["products"]:
        return ""
    parts = ["<section id='deposits'><h2>Deposit study</h2><p>Share betas and balance behaviour estimated from "
             "this institution's own history (<code>deposit_history.csv</code>%s), against the assumptions the model "
             "runs on. The study recommends; it changes nothing until the settings do.</p>" % (
                 " and account balances in <code>deposit_accounts.csv</code>" if d["has_accounts"] else "")]
    rows = []
    for x in d["products"]:
        b, s, dec, a = x["beta"], x["sensitivity"], x["decay"], x["assumed"] or {}
        rows.append([
            label(x["product"]), "%d (%s to %s)" % (x["months"], x["from"], x["to"]),
            "" if a.get("beta") is None else pct(a["beta"], 0),
            "" if b["beta"] is None else "%s (lag %d, R² %.2f)" % (pct(b["beta"], 0), b["lag"], b["r2"]),
            "%s / %s" % ("n/a" if b["up_beta"] is None else pct(b["up_beta"], 0),
                         "n/a" if b["down_beta"] is None else pct(b["down_beta"], 0)),
            "" if a.get("runoff") is None else pct(a["runoff"], 0),
            "not estimable from balances" if not dec else "%s (life %.1f yrs)" % (
                pct(dec["decay"], 0), dec["average_life_years"] or 0),
            "" if a.get("runoff_per_100bp") is None else pct(a["runoff_per_100bp"], 1),
            "" if not s else "%s (R² %.2f)" % (pct(s["runoff_per_100bp"], 1), s["r2"]),
            "" if x["core"] is None else pct(x["core"], 0),
            " ".join(chip("near", f) for f in x["flags"]) or chip("within", "Supports it")])
    parts.append(table(["Product", "Months", "Beta assumed", "Beta estimated", "Up / down beta", "Decay assumed",
                        "Decay estimated", "Runoff per 100bp assumed", "Estimated", "Core balance", ""], rows))
    rec = r.get("deposit_recommended") or {}
    if rec:
        fields = sorted({k for v in rec.values() for k in v})
        parts.append("<h3>What the study supports, in the settings' units</h3>")
        parts.append(table(["Product"] + fields, [[label(p)] + ["" if f not in v else "%g" % v[f] for f in fields]
                                                  for p, v in sorted(rec.items())]))
        parts.append("<p class='muted'>To adopt, copy into the Products sheet. The key-assumption tests above include "
                     "a row that runs the model on these values, so the effect is visible before anyone decides.</p>")
    parts.append("<p class='muted'>Beta: the share rate regressed on the market rate lagged 0 to 6 months, best fit "
                 "shown. Up and down: the share rate's change over the market's rising and falling phases, each over "
                 "the market's change. Runoff per 100bp: monthly balance growth regressed on the market-over-share "
                 "spread, annualized; a low R² means balances did not move with rates and the estimate should not be "
                 "used. Core: the lowest trailing-year balance over the average. Decay: the balance still held by the "
                 "first month's accounts, fitted to (1 - d)^(t/12); aggregate balances cannot give it, because new "
                 "money hides runoff.</p></section>")
    return "".join(parts)


def liquidity_scenarios(r):
    L = r["liquidity"]
    rows = L.get("scenarios") or []
    if not rows:
        return ""
    limit = next(x for x in r["limits"] if x.key == "survival_months_min")
    out = ["<h3 id='liquidity-scenarios'>Stress scenarios</h3><p class='muted'>Each scenario scales every share "
           "product's stress runoff, adds to the haircut on liquid investments, cuts what the contingent sources "
           "will lend, and can add a run on uninsured balances, then runs the same stressed projection. Scenarios "
           "come from the Stresses sheet of the settings, or Keel's four defaults.</p>"]
    body = []
    for s in rows:
        months = None if s["survival"] is None else float(s["survival"] - 1)
        status = results_module.evaluate("survival_months_min", "min", limit.limit, months, r["assumptions"].warning_band)
        body.append([esc(s["name"]), "x%g" % s["runoff_multiplier"], "+%g pts" % round(100 * s["haircut_add"], 1),
                     pct(s["contingent_available"], 0), str(s["months"]),
                     pct(s["uninsured_runoff"], 0) if s["uninsured_runoff"] else "",
                     "the whole year" if s["survival"] is None else "runs out in month %d" % s["survival"],
                     k(s["lowest"]), str(s["lowest_month"]), chip(status)])
    out.append(table(["Scenario", "Share runoff", "Extra haircut", "Contingent available", "Months",
                      "Uninsured leaving", "Liquidity", "Low point ($000)", "Month", "Against the limit"], body))
    return "".join(out)


def liquidity_collateral(r):
    c = r["liquidity"].get("collateral")
    if not c or not c["lines"]:
        return ""
    out = ["<h3 id='collateral'>Borrowing capacity and collateral ($000)</h3>"]
    rows = [[label(p), k(b), pct(share, 0), k(v)] for p, b, share, v in c["lines"]]
    rows.append(["Total", "", "", k(c["lendable"])])
    out.append(table(["Pledgeable loans", "Balance", "Lendable share", "Lendable value"], rows, total_last=True))
    if c["secured"]:
        out.append(table(["Secured line", "Unused line", "Collateral left", "Counted in the stress"],
                         [[esc(x["name"]), k(x["line"]), k(x["collateral_headroom"]), k(x["usable"])]
                          for x in c["secured"]]))
    out.append("<p class='muted'>A secured line counts at the smaller of its unused amount and the lendable value "
               "of pledgeable loans left after what is already borrowed ($%sK). Lendable shares are each product's "
               "<code>collateral_value</code>. Securities are not counted as collateral here: they already count "
               "as liquid investments after their haircut.</p>" % k(c["borrowed"]))
    return "".join(out)


def liquidity_concentration(r):
    c = r["liquidity"].get("concentration")
    if not c:
        return ("<h3 id='concentration'>Deposit concentration</h3><p class='muted'>Add <code>depositors.csv</code> "
                "(member_id, balance: one row per member, all share accounts summed) to measure uninsured balances "
                "and large-depositor concentration.</p>")
    limit = next(x for x in r["limits"] if x.key == "uninsured_shares_max")
    out = ["<h3 id='concentration'>Deposit concentration</h3>"]
    if c["source"] == "depositors":
        out.append(table(["Measure", "Value"], [
            ["Members", "{:,}".format(c["members"])],
            ["Members over $250,000", "{:,}".format(c["over_limit"])],
            ["Balances over $250,000 (uninsured, estimated)", "$%sK" % k(c["uninsured"])],
            ["Uninsured share of shares", pct(c["uninsured_share"], 1) + " " + chip(limit.status)],
            ["Largest 10 members' share", pct(c["top10_share"], 1)],
            ["Largest 20 members' share", pct(c["top20_share"], 1)],
            ["Largest member", "$%sK" % k(c["largest"])]], numeric_from=1))
        out.append("<p class='muted'>From <code>depositors.csv</code>. Insurance covers $250,000 per member per "
                   "ownership category, and the file does not split categories, so this overstates uninsured "
                   "balances for members with joint, retirement or trust accounts: an upper bound. The Uninsured "
                   "run scenario above uses it.</p>")
    else:
        out.append("<p>Certificates over $250,000 hold <b>$%sK</b> above the insured limit, %s of shares %s. "
                   "That is a floor: share accounts are only reported in balance tiers here. Add "
                   "<code>depositors.csv</code> for the full measure.</p>" % (
                       k(c["uninsured"]), pct(c["uninsured_share"], 1), chip(limit.status)))
    return "".join(out)


def credit_section(r):
    c = r.get("credit")
    if not c:
        return ""
    nw_limit = next(x for x in r["limits"] if x.key == "net_worth_min")
    out = ["<h3 id='credit'>Credit losses under stress</h3>"]
    body = []
    for s in c["scenarios"]:
        status = results_module.evaluate("net_worth_min", "min", nw_limit.limit,
                                         100 * min(s["lowest_net_worth"], s["net_worth_after_build"]),
                                         r["assumptions"].warning_band)
        body.append([esc(s["name"]), "x%g" % s["multiplier"],
                     "" if not s["months"] else "%d, then %d back" % (s["months"], s["reversion_months"]),
                     k(s["losses_y1"]), k(s["losses_y2"]), k(s["net_income_2y"]), k(s["allowance_build"]),
                     pct(s["net_worth_after_build"]), pct(s["lowest_net_worth"]), chip(status)])
    out.append(table(["Scenario", "Charge-offs", "Months", "Losses year 1 ($000)", "Year 2", "Net income, 2 years",
                      "CECL build at once", "Net worth after the build", "Lowest net worth, plan", "Against the limit"],
                     body))
    out.append("<p class='muted'>Each scenario multiplies every loan product's charge-off rate for its stressed "
               "months and phases it back to normal over the reversion months, on the base rate path. The CECL "
               "build is the extra lifetime loss the scenario's forecast adds to the allowance on day one; it comes "
               "out of net worth before any loan defaults. Scenarios come from the settings' CreditScenarios sheet, "
               "or these three defaults.</p>")
    out.append("<h3>CECL allowance: remaining-life estimate ($000)</h3>")
    rows = [[label(x["product"]), k(x["balance"]), pct(x["rate"]), "%.1f" % x["wal_years"], k(x["lifetime"]),
             pct(x["lifetime"] / x["balance"] if x["balance"] else 0.0)] for x in c["products"]]
    total_loans = sum(x["balance"] for x in c["products"])
    rows.append(["Total", k(total_loans), "", "", k(c["estimate"]),
                 pct(c["estimate"] / total_loans if total_loans else 0.0)])
    out.append(table(["Product", "Balance", "Annual loss rate", "Remaining life (years)", "Lifetime loss",
                      "Of balance"], rows, total_last=True))
    if c["booked"]:
        gap = c["booked"] - c["estimate"]
        out.append("<p>The allowance on the books is <b>$%sK</b>, %s the estimate by $%sK (%s of it).</p>" % (
            k(c["booked"]), "above" if gap >= 0 else "below", k(abs(gap)),
            pct(c["booked"] / c["estimate"] if c["estimate"] else 0.0, 0)))
    else:
        out.append("<p class='muted'>No allowance is on the books in this data (no negative contra-asset "
                   "position), so there is nothing to compare the estimate with.</p>")
    out.append("<p class='muted'>Remaining-life method: each position's own runoff, with prepayment, and its "
               "product's charge-off rate applied month by month. A single-factor, product-level check on the "
               "booked allowance, without vintage curves, PD/LGD or a qualitative adjustment.</p>")
    return "".join(out)


def capital_section(r):
    cap = r.get("capital")
    if not cap:
        return ""
    out = ["<section id='capital'><h2>Capital</h2>"]
    if cap["category"]:
        out.append("<p>Prompt-corrective-action category: <b>%s</b>%s.</p>" % (
            esc(cap["category"]), " (a complex credit union, over $500 million)" if cap["complex"] else ""))
    rows = []
    for x in cap["rows"]:
        rows.append([esc(x["measure"]), pct(x["value"]), pct(x["well"], 1),
                     "" if x["adequate"] is None else pct(x["adequate"], 1),
                     chip(x["status"], {"within": "Well capitalized", "near": "Adequate",
                                        "breach": "Below"}[x["status"]]), esc(x["note"])])
    out.append(table(["Measure", "Today", "Well capitalized at", "Adequately at", "", "Note"], rows, numeric_from=1))
    lens = cap["lens"]
    if lens["book"]:
        out.append("<h3>With the securities at market ($000)</h3>")
        out.append(table(["", "Today", "After +300bp"], [
            ["Securities at book", k(lens["book"]), k(lens["book"])],
            ["At market", k(lens["market"]), k(lens["market_300"])],
            ["Unrealized gain (loss)", k(lens["unrealized"]), k(lens["unrealized_300"])],
            ["Net worth ratio with it", pct(lens["ratio_now"]), pct(lens["ratio_300"])]]))
        out.append("<p class='muted'>Regulatory capital ignores unrealized losses on securities until they are "
                   "sold; a stress that forces a sale does not. The shock moves the whole curve 300bp on the "
                   "analysis date, the same shock as the NEV test.</p>")
    out.append("<p class='muted'>Risk-weighted assets $%sK, from each product's <code>risk_weight</code>. Estimates "
               "on product-level weights, without deductions, off-balance-sheet exposures or past-due and "
               "concentration adjustments: not a call-report calculation.</p></section>" % k(cap["rwa"]))
    return "".join(out)


def reconciliation(r):
    rows = [[esc(c.name), "<span class='%s'>%s</span>" % (
        "pass" if c.passed else "fail", "&#10003; Pass" if c.passed else "&#10005; FAIL"), esc(c.detail)]
        for c in r["checks"]]
    return ("<section id='reconciliation'><h2>Reconciliation</h2><p>The claim this report makes is that "
            "interest-rate risk, the plan and liquidity are one model%s. These checks test it on this run.</p>%s"
            "</section>" % (" and that the detail ties to the general ledger" if r["imported"] is not None else "",
                           table(["Check", "Result", "Detail"], rows, numeric_from=99)))


def assumptions_section(r):
    a = r["assumptions"]
    parts = ["<section id='assumptions'><h2>Every assumption this run used</h2><div class='charts'>"]
    parts.append("<div><h3>Base curve</h3>%s</div>" % table(
        ["Tenor (months)", "Rate"], [[str(int(t)), "%.2f%%" % v] for t, v in zip(a.curve.tenors, a.curve.rates)]))
    scen = []
    for s in a.scenarios:
        move = ("parallel %+gbp" % s.shock_bp if s.shape is None else "shaped: " + ", ".join(
            "%gm %+gbp" % (t, v) for t, v in zip(s.shape.tenors, s.shape.rates)))
        if not s.use_path:
            move = "today's curve held, ignoring the base-case path"
        scen.append([esc(s.name), esc(move + (", over %d months" % s.ramp_months if s.ramp_months else ""))])
    parts.append("<div><h3>Scenarios</h3>%s</div></div>" % table(["Scenario", "Move"], scen, numeric_from=99))
    path = r["rate_path"]
    if path["kind"] != "flat":
        parts.append("<h3>Base-case rate path (%s)</h3>" % ("implied forwards" if path["kind"] == "forward"
                                                             else "forecast"))
        parts.append(table(["Month"] + ["%dm rate" % t for t in path["tenors"]],
                           [[str(x["month"])] + ["%.2f%%" % v for v in x["rates"]] for x in path["rows"]]))
    fields = ("cpr", "cpr_per_100bp", "runoff", "runoff_per_100bp", "beta", "rate_floor", "new_term", "spread",
              "discount_spread", "growth", "charge_off", "haircut", "stress_runoff")
    rows = [[label(product)] + [(str(getattr(a.products[product], f)) if f == "new_term"
                                 else "%.2f%%" % (100 * getattr(a.products[product], f))) for f in fields]
            for product in sorted(a.products)]
    parts.append("<h3>Products</h3>")
    parts.append(table(["Product"] + [f.replace("_", " ") for f in fields], rows))
    parts.append("<p class='muted'>Fee income $%sK a year; operating expense $%sK a year growing %.1f%%; cash minimum "
                 "$%sK; horizon %d months.</p>" % (k(a.fee_income), k(a.operating_expense), 100 * a.expense_growth,
                                                   k(a.cash_minimum), a.horizon_months))
    notes = [v for key, v in a.notes.items() if key != "about"]
    parts += ["<p class='muted'>%s</p>" % esc(n) for n in notes]
    parts.append("</section>")
    return "".join(parts)


# --------------------------------------------------------------- the page

def page(r, downloads=()):
    name, a = r["name"], r["assumptions"]
    nav = [("summary", "Summary"), ("limits", "Limits"), ("rate-risk", "Rate risk")]
    if r.get("deposits"):
        nav.append(("deposits", "Deposits"))
    nav += [("plan", "Plan"), ("liquidity", "Liquidity"), ("capital", "Capital")]
    if r["securities"] or r["imported"] is not None:
        nav.append(("portfolios", "Portfolios"))
    nav.append(("profitability", "Profitability"))
    if r.get("accounts"):
        nav.append(("accounts", "Members"))
    nav.append(("budget", "Budget"))
    if r["queries"]:
        nav.append(("adhoc", "Ad hoc"))
    if r.get("history"):
        nav.append(("history", "History"))
    if r.get("peers"):
        nav.append(("peers", "Peers"))
    nav += [("reconciliation", "Reconciliation"), ("assumptions", "Assumptions")]
    about = a.notes.get("about", "")
    return terms.translate("\n".join([
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>%s: ALCO report, %s</title><style>%s</style></head><body>" % (esc(name), esc(a.as_of), STYLE),
        "<header class='top'><div class='inner'><h1>%s</h1><p class='sub'>ALM, plan and liquidity from one "
        "projection, as of %s. Tables in thousands of dollars.</p>%s<div class='downloads'>%s</div></div></header>" % (
            esc(name), esc(a.as_of), "<p class='sub'>%s</p>" % esc(about) if about else "",
            "".join("<a href='%s'>%s</a>" % (href, esc(text)) for href, text in downloads)),
        "<nav aria-label='Sections'><div class='inner'>%s</div></nav><main>" % "".join(
            "<a href='#%s'>%s</a>" % (i, esc(t)) for i, t in nav),
        summary(r), rate_risk(r), deposits_section(r), plan(r), liquidity(r), capital_section(r), portfolios(r),
        profitability_section(r), accounts_section(r),
        budget_section(r),
        adhoc_section(r), history_section(r), peers_section(r), reconciliation(r), assumptions_section(r),
        "</main><footer>Generated %s by Keel. Every figure is computed from the input files; none is typed. Keel "
        "runs on this computer and sends nothing anywhere.</footer></body></html>" % datetime.date.today().isoformat(),
    ]), a)


def build(positions, assumptions, out_dir, name="Credit union", imported=None, folder=None, assumption_tests=None):
    r = results_module.compute(positions, assumptions, name, imported, folder, assumption_tests)
    if folder:
        from keel import history
        history.save(folder, r["snapshot"])
    os.makedirs(out_dir, exist_ok=True)
    export.write_workbook(r, os.path.join(out_dir, "results.xlsx"))
    products = [b["line"] for b in r["plan"]["balance_sheet"] if b["line"] not in ("cash", "overnight_borrowing")]
    _write_months(os.path.join(out_dir, "projection_base.csv"), r["base_run"], products)
    downloads = [("results.xlsx", "Every table, in Excel"), ("projection_base.csv", "Monthly projection (CSV)")]
    if imported is not None:
        downloads.append(("positions_imported.csv", "Pooled positions (CSV)"))
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as handle:
        handle.write(page(r, downloads))
    return {"checks": r["checks"], "test": r["test"], "survival": r["liquidity"]["survival"],
            "nii_year1": r["nii_base"]["y1"], "limits": r["limits"], "results": r}


def _write_months(path, months, products):
    fields = ["month", "cash", "overnight", "interest_income", "interest_expense", "fee_income",
              "operating_expense", "credit_losses", "net_income", "assets", "liabilities", "equity"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(fields + ["balance_" + p for p in products])
        for m in months:
            w.writerow([m.month] + ["%.2f" % getattr(m, f) for f in fields[1:]]
                       + ["%.2f" % m.balances.get(p, 0.0) for p in products])
