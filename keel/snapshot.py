"""A two-page rate-risk snapshot: the full report cut down to what a first look needs.

    python -m keel snapshot examples/cu-68225 --contact "A. Analyst, analyst@example.com"

It is written for a credit union or bank that has never run Keel. Built from
the public call report (`keel callreport`), every balance and total is the
institution's own but its behaviour is Keel's defaults, so the page says
which is which and ends with what a run on its own files would change. It
shows only the policy limits the institution has set (in its settings, or
with --limit): a call report does not carry the board's, and a typical
limit shown as if it were theirs would be a verdict nobody set.

Every figure comes from the same `results.compute` output as the full
report, so the two cannot disagree.
"""

import datetime
import os

from keel import charts, report, results as results_module, terms
from keel.report import chip, esc, k, pct, signed, signed_points, table

RATING = report.RATING
#: Who a snapshot's closing offer asks the reader to reply to.
CONTACT = "Jordan Haney, CFA, jhaney0214@gmail.com"

EXTRA_STYLE = """
.snap .tiles{grid-template-columns:repeat(3,1fr)}
.calibration{font-size:.8rem}
@media (max-width:40rem){.snap .tiles{grid-template-columns:repeat(2,1fr)}}
.basis{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:.8rem;margin:.6rem 0 1rem}
.basis div{border:1px solid var(--rule);border-radius:4px;padding:.7rem .9rem;font-size:.88rem}
.basis h4{margin:0 0 .3rem;font-size:.86rem}
.offer{background:var(--panel);border-radius:4px;padding:1rem 1.1rem;margin:1.6rem 0 0}
.offer h2{border:0;margin:0 0 .5rem;padding:0}
.note{border-left:3px solid var(--warning);padding:.5rem .8rem;background:var(--panel);font-size:.88rem;margin:.8rem 0}
@media print{html{font-size:11px} body.snap{font-size:11px} .snap h2{break-before:auto;break-after:avoid;margin-top:1rem}
.snap .charts{grid-template-columns:1fr 1fr;gap:0 1.2rem} .snap svg.chart{max-width:100%}
.snap #rate-risk>svg.chart{max-width:34rem} .snap .tile{padding:.45rem .6rem} .snap .findings li{padding:.35rem .6rem}
.snap td{padding:.15rem .5rem} .snap th{padding:.2rem .5rem} .snap .offer{break-inside:avoid}
.snap header.top .inner{padding-top:0}}
"""

# The report's findings a first look keeps; plan, profitability, limits and the
# lean too heavily on defaults (or on files a call report lacks) to lead with.
KEEP = ("Earnings", "Economic value", "Liquidity", "Capital")


def indicative(r):
    """Built from the call report: its balances, but Keel's default behaviour."""
    return bool(r["notes"].get("defaults"))


def finding(r, head, text):
    """The report's own finding, reworded where it would claim the institution's assumptions."""
    if head == "Economic value" and indicative(r):
        text = text.split(" NCUA's supervisory test")[0].replace(
            "On the credit union's own assumptions", "On Keel's default behaviour")
        text += (" NCUA's supervisory test, with its standardized share values, comes to %.1f%% after +300bp. That "
                 "depends on remaining terms and prepayment the call report does not give, so it is not rated here."
                 % (100 * r["test"]["post_shock_ratio"]))
    return text


def tiles(r):
    a, t, L = r["assumptions"], r["test"], r["liquidity"]
    parallel = [x for x in r["nii"] if x["shock_bp"] in (300, -300) and x["ramp"] == 0]
    worst = min(parallel, key=lambda x: x["y1_change"])
    out = [("$%.1fM" % (r["nii_base"]["y1"] / 1e6), "Year-one net interest income, base plan", ""),
           (signed(worst["y1_change"]), "Year-one NII in the %sbp shock" % worst["scenario"], "")]
    if terms.is_bank(a):
        nev = min((x for x in r["nev"] if x["scenario"] in ("+300", "-300")), key=lambda x: x["ratio"])
        out += [(pct(nev["ratio"], 1), "NEV ratio after the %sbp shock" % nev["scenario"], ""),
                (signed(nev["change"], 0), "NEV change in the %sbp shock" % nev["scenario"], "")]
    elif indicative(r):
        # From a call report, the supervisory test's standardized share values
        # meet default terms and prepayment, and the result is not rated (the
        # Economic value text says why and gives the figure). Its change is a
        # percentage of a base that can be near zero: "-269%" led one page.
        # The tiles show the NEV Keel actually models on its defaults.
        nev = next(x for x in r["nev"] if x["scenario"] == "+300")
        out += [(pct(nev["ratio"], 1), "NEV ratio after +300bp, Keel defaults", ""),
                (signed(nev["change"], 0), "NEV change at +300bp, Keel defaults", "")]
    else:
        out += [(pct(t["post_shock_ratio"], 1), "NCUA supervisory NEV ratio after +300bp",
                 chip(RATING[t["ratio_rating"]], t["ratio_rating"] + " risk")),
                (signed(-t["sensitivity_value_decline"], 0), "NCUA supervisory NEV change at +300bp",
                 chip(RATING[t["sensitivity_rating"]], t["sensitivity_rating"] + " risk"))]
    out += [("12+ months" if L["survival"] is None else "Month %d" % L["survival"],
             "Liquidity lasts, %d-month stress" % a.stress_months, ""),
            # Book equity, after unrealized securities losses: not the regulatory net worth ratio the peers show.
            (pct(r["opening"]["equity"] / r["opening"]["assets"], 1), "Book equity to assets today", "")]
    return "<div class='tiles'>%s</div>" % "".join(
        "<div class='tile'><span class='v'>%s</span><span class='l'>%s</span>%s</div>" % (esc(v), esc(l), s)
        for v, l, s in out)


def limits(r):
    """The board's own limits, each with its status; the ones it has not set are left out, not defaulted."""
    board = [x for x in r["limits"] if not x.default]
    how = ("Set them in the Limits sheet of the settings workbook, as \"limits\" in assumptions.json, or for this "
           "page alone with <code>keel snapshot --limit nii_decline_300=15</code>.")
    if not board:
        return ("<section id='limits'><h2>Policy limits</h2><p>No policy limits are set, so none are shown: a "
                "typical limit is not this %s's. %s</p></section>" % (
                    "bank" if terms.is_bank(r["assumptions"]) else "credit union", how))
    rows = [[esc(x.label.replace("own assumptions", "Keel's default behaviour") if indicative(r) else x.label),
             report.limit_text(x), report.limit_value(x), chip(x.status)] for x in board]
    note = "Near means within %g%% of the limit." % r["assumptions"].warning_band
    if indicative(r):
        note = ("The limits are the board's; the rate-risk measures beside them rest on Keel's default behaviour, so "
                "read those statuses as indicative. " + note)
    unset = len(r["limits"]) - len(board)
    if unset:
        note += " %d more %s not set. %s" % (unset, "is" if unset == 1 else "are", how)
    return "<section id='limits'><h2>Policy limits</h2>%s<p class='muted'>%s</p></section>" % (
        table(["Measure", "Limit", "Today", "Status"], rows, numeric_from=2), note)


def rate_risk(r):
    shocked_nii = [x for x in r["nii"] if x["scenario"] != "base"]
    shocked_nev = [x for x in r["nev"] if x["scenario"] != "base"]
    nii_chart = charts.diverging_bars(
        [(x["scenario"], 100 * x["y1_change"], "%s: year-one NII $%sK, %s vs base" % (
            x["scenario"], k(x["y1"]), signed(x["y1_change"]))) for x in shocked_nii],
        signed_points(1), "Year-one NII vs base")
    nev_chart = charts.diverging_bars(
        [(x["scenario"], 100 * x["change"], "%s: NEV $%sK, ratio %s, %s vs base" % (
            x["scenario"], k(x["nev"]), pct(x["ratio"]), signed(x["change"]))) for x in shocked_nev],
        signed_points(0), "NEV vs base")
    nev_by = {x["scenario"]: x for x in r["nev"]}
    rows = [[esc(x["scenario"]), k(x["y1"]), signed(x["y1_change"]),
             pct(nev_by[x["scenario"]]["ratio"]) if x["scenario"] in nev_by else "",
             signed(nev_by[x["scenario"]]["change"]) if x["scenario"] in nev_by else ""] for x in r["nii"]]
    gap = r["gap"]
    gap_chart = charts.columns(
        [(report._short_band(g["band"]), g["gap"] / 1e6, "%s: gap $%sK; cumulative %s of assets" % (
            g["band"], k(g["gap"]), pct(g["cumulative_to_assets"], 1))) for g in gap],
        report._millions, "Repricing gap by band ($ millions)", signed=True)
    one_year = next((g for g in gap if g["band"] == "3-12 months"), None)
    return "".join([
        "<section id='rate-risk'><h2>Interest-rate risk</h2><div class='charts'>%s%s</div>" % (nii_chart, nev_chart),
        table(["Scenario", "Year 1 NII ($000)", "Year 1 vs base", "NEV ratio", "NEV vs base"], rows),
        "<p class='muted'>Going concern: balances follow the plan and what matures is replaced at each scenario's "
        "rates. NEV here is on the model's share values; the NCUA test above uses the standardized ones.</p>",
        gap_chart,
        "<p class='muted'>Base scenario. Variable-rate positions count at their next reset, everything else by its "
        "cash flows after prepayment and share decay%s.</p>" % (
            "; cumulative gap through one year is %s of assets" % pct(one_year["cumulative_to_assets"], 1)
            if one_year else ""),
        "</section>"])


def peers(r):
    p = r.get("peers")
    if not p:
        return ""
    rows = []
    for x in p["ratios"]:
        tag = ""
        if x["better"] and (x["percentile"] <= 10 or x["percentile"] >= 90):
            good = x["percentile"] >= 50 if x["better"] == "higher" else x["percentile"] <= 50
            tag = chip("within" if good else "breach", "Top tenth" if good else "Bottom tenth")
        rows.append([esc(x["label"]), "%.2f%%" % x["value"], "%.2f%%" % x["median"], "%.0f" % x["percentile"], tag])
    return ("<section id='peers'><h2>Against peers</h2><p class='muted'>The %s credit unions in NCUA's %s peer "
            "group, %s call report, ratios as filed. Percentile is the share of peers below.</p>%s</section>" % (
                "{:,}".format(p["count"]), esc(p["peer_group"]), esc(p["cycle"]),
                table(["Ratio", "This credit union", "Peer median", "Percentile", ""], rows)))


def basis(r):
    notes = r["notes"]
    if indicative(r):
        return ("<section id='basis'><h2>What this is built from</h2><div class='basis'>"
                "<div><h4>Its own, from the call report</h4>Every balance by loan, share, investment and borrowing "
                "type; total assets, liabilities and net worth; loan interest, investment income and dividends, "
                "which the rates are calibrated to earn and cost exactly.</div>"
                "<div><h4>Keel's defaults, not its own</h4>Prepayment speeds, share decay and rate betas, remaining "
                "terms, and share rates by product. These drive the NEV and the shock results most, and they are "
                "the first thing its own files would replace.</div></div>"
                "<p class='muted'>How each figure was calibrated is in the notes at the end.</p></section>")
    return ("<section id='basis'><h2>What this is built from</h2><p>The institution's own detail files and "
            "assumptions, as of %s.</p></section>" % esc(r["as_of"]))


def calibration(r):
    """The call-report build's own notes: the provenance of every calibrated number, as fine print after the offer."""
    notes = r["notes"]
    if not indicative(r):
        return ""
    return "<section id='notes' class='muted calibration'><h3>Notes</h3><p>%s %s</p></section>" % (
        esc(notes.get("source", "")), esc(notes.get("calibration", "")))


def offer(r, contact):
    own = indicative(r)
    return ("<section class='offer' id='offer'><h2>%s</h2><p>%s</p><p>Keel runs on one computer and sends nothing "
            "anywhere; it can run on your own machine with your staff at the keyboard. The result is a full ALCO "
            "report tied to your general ledger, and a line-by-line comparison with your current model if you have "
            "one.</p><p><b>%s</b></p></section>" % (
                "What your own files would change" if own else "A second opinion",
                "This page uses public data and default behaviour. Run on your loan, share, investment and "
                "borrowing detail, with your own decay, betas and prepayment, the NEV and shock results above would "
                "be your own, not a typical credit union's." if own else
                "These results are from your own files. A side-by-side with your current ALM model shows where the "
                "two agree and, where they do not, which assumption to look at first.",
                esc(contact)))


def page(r, contact):
    a = r["assumptions"]
    source = "public call report data" if indicative(r) else "the institution's own files"
    warn = ("<p class='note'>A first look, not an ALM report: behaviour is Keel's defaults. Read the rate-risk "
            "results as what a typical credit union with this balance sheet would show.</p>"
            if indicative(r) else "")
    kept = [(h, text) for h, text in r["findings"] if h in KEEP]
    return terms.translate("\n".join([
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>%s: rate-risk snapshot</title><style>%s%s</style></head><body class='snap'>" % (
            esc(r["name"]), report.STYLE, EXTRA_STYLE),
        "<header class='top'><div class='inner'><h1>%s</h1><p class='sub'>Rate-risk snapshot as of %s, from %s.</p>"
        "</div></header><main>" % (esc(r["name"]), esc(a.as_of), source),
        "<section id='summary'><h2>At a glance</h2>%s%s<ul class='findings'>%s</ul></section>" % (
            warn, tiles(r), "".join("<li><b>%s</b><span>%s</span></li>" % (esc(h), esc(finding(r, h, t))) for h, t in kept)),
        limits(r), rate_risk(r), peers(r), basis(r), offer(r, contact), calibration(r),
        "</main><footer>Generated %s by Keel. Every figure is computed, none typed.</footer></body></html>" % (
            datetime.date.today().isoformat()),
    ]), a)


def build(positions, assumptions, out_path, name, folder=None, contact=CONTACT, imported=None):
    r = results_module.compute(positions, assumptions, name, imported, folder, assumption_tests=False)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page(r, contact))
    return r
