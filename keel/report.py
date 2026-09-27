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
        return "12+ months"
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
    parts = ["<section id='plan'><h2>The plan</h2><div class='charts'>%s%s</div>" % (income, worth)]
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
        parts.append("<p class='muted'>From <code>actuals.csv</code>, $000. A product the actuals leave out counts as "
                     "on budget. Volume is the balance difference at the budget's yield; rate is the rest.</p>")
    else:
        parts.append("<p class='muted'>Add <code>actuals.csv</code> (month, line, average_balance, amount) to the "
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
        scen.append([esc(s.name), esc(move + (", over %d months" % s.ramp_months if s.ramp_months else ""))])
    parts.append("<div><h3>Scenarios</h3>%s</div></div>" % table(["Scenario", "Move"], scen, numeric_from=99))
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
    nav = [("summary", "Summary"), ("limits", "Limits"), ("rate-risk", "Rate risk"), ("plan", "Plan"),
           ("liquidity", "Liquidity")]
    if r["securities"] or r["imported"] is not None:
        nav.append(("portfolios", "Portfolios"))
    nav += [("profitability", "Profitability"), ("budget", "Budget")]
    if r["queries"]:
        nav.append(("adhoc", "Ad hoc"))
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
        summary(r), rate_risk(r), plan(r), liquidity(r), portfolios(r), profitability_section(r), budget_section(r),
        adhoc_section(r), reconciliation(r), assumptions_section(r),
        "</main><footer>Generated %s by Keel. Every figure is computed from the input files; none is typed. Keel "
        "runs on this computer and sends nothing anywhere.</footer></body></html>" % datetime.date.today().isoformat(),
    ]), a)


def build(positions, assumptions, out_dir, name="Credit union", imported=None, folder=None):
    r = results_module.compute(positions, assumptions, name, imported, folder)
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
