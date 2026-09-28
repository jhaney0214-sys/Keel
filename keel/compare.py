"""A second opinion: Keel's answers beside another ALM model's, on the same book.

    python -m keel compare <folder> --template other_model.csv     a blank template to fill in
    python -m keel compare <folder> other_model.csv                 the comparison, as a page

The template takes what any ALM report states, one row per figure:

    measure,scenario,value
    nii_year1,base,21400000          net interest income, year one, dollars
    nii_year1,+300,20800000
    nii_year2,base,22600000
    nev,base,118000000               net economic value (EVE for a bank), dollars
    nev_ratio,+300,4.9               percent of the present value of assets
    total_assets,,560000000          the opening balance sheet the model ran on
    loans,,404000000
    shares,,481000000

Scenarios are Keel's names (base, +100 ... -300, ramp +200 and so on);
leave any figure out. For each one the page shows both values, the
difference, and whether it is within a tolerance a reviewer would accept.
Then, for the kind of difference it is, where to look first: two models on
the same book should differ for reasons that can be named. A difference in
the opening balances means the two are not modelling the same book, and
nothing else is worth comparing until it is fixed.
"""

import csv
import html
import os

from keel import engine, tables
from keel.model import InputError

#: measure -> (label, kind, tolerance): kind "money" compares in percent of
#: the other model's value, "pct" in percentage points.
MEASURES = {
    "total_assets": ("Total assets", "money", 0.1),
    "loans": ("Loans", "money", 0.1),
    "shares": ("Shares and deposits", "money", 0.1),
    "nii_year1": ("Net interest income, year one", "money", 3.0),
    "nii_year2": ("Net interest income, year two", "money", 5.0),
    "nii_change_year1": ("Change in year-one NII from base", "pct", 2.0),
    "nev": ("Net economic value", "money", 10.0),
    "nev_ratio": ("NEV ratio", "pct", 1.5),
    "nev_change": ("Change in NEV from base", "pct", 5.0),
}
BALANCES = ("total_assets", "loans", "shares")

#: Where to look, by the kind of difference.
WHERE = {
    "balances": "The two models are not running the same book. Tie each model's opening balances to the "
                "general ledger (Keel's Reconciliation section does this for Keel) before comparing anything else.",
    "nii_base": "Base-case NII differs: compare the opening yields and rates (Keel calibrates to the book's own "
                "rates), fee and expense lines are not in NII, then the base-case rate path (flat, forward or a "
                "forecast), new-business pricing and planned growth.",
    "nii_shock": "The base agrees but the shocked NII does not: compare deposit betas and repricing lags (the "
                 "largest lever), rate floors on shares, prepayment speeds' response to rates, and whether "
                 "shocks are instantaneous or ramped.",
    "nev_base": "Base NEV differs: compare discount rates (Keel discounts at the curve plus each product's "
                "discount_spread), non-maturity share decay (the life of the deposits), and how stock, fixed "
                "assets and other non-earning items are valued.",
    "nev_shock": "The base NEV agrees but the shocked one does not: compare deposit decay and its response to "
                 "rates, prepayment speeds under the shock, and option features (calls, caps and floors).",
}


def template_rows(r):
    """Keel's own figures, as the template's rows."""
    rows = [("total_assets", "", r["opening"]["assets"]), ("loans", "", r["opening"]["loans"]),
            ("shares", "", r["opening"]["shares"])]
    for x in r["nii"]:
        rows.append(("nii_year1", x["scenario"], x["y1"]))
        rows.append(("nii_year2", x["scenario"], x["y2"]))
        if x["scenario"] != "base":
            rows.append(("nii_change_year1", x["scenario"], 100 * x["y1_change"]))
    for x in r["nev"]:
        rows.append(("nev", x["scenario"], x["nev"]))
        rows.append(("nev_ratio", x["scenario"], 100 * x["ratio"]))
        if x["scenario"] != "base":
            rows.append(("nev_change", x["scenario"], 100 * x["change"]))
    return rows


def keel_figures(positions, a, r):
    loans = sum(p.balance for p in positions if p.side == "asset" and p.balance > 0
                and a.products[p.product].charge_off > 0)
    shares = sum(p.balance for p in positions if p.side == "liability" and p.product not in
                 ("borrowings", "other_liabilities"))
    _, assets, _, _ = engine.opening(positions)
    r = dict(r, opening={"assets": assets, "loans": loans, "shares": shares})
    return {(m, s): v for m, s, v in template_rows(r)}


def write_template(path, positions, a, r):
    """A template listing every figure Keel can compare, with the value column blank."""
    figures = keel_figures(positions, a, r)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(["measure", "scenario", "value", "note"])
        for (m, s) in figures:
            w.writerow([m, s, "", MEASURES[m][0] + (" (percent)" if MEASURES[m][1] == "pct" else " (dollars)")])
    return len(figures)


def read_other(path):
    out = {}
    for n, row in enumerate(tables.read_table(path), 2):
        m, s, v = (row.get("measure") or "").strip(), (row.get("scenario") or "").strip(), row.get("value")
        if not m and not v:
            continue
        if m not in MEASURES:
            raise InputError("%s line %d: unknown measure %r (one of %s)" % (os.path.basename(path), n, m,
                                                                           ", ".join(MEASURES)))
        if v in (None, ""):
            continue
        try:
            out[(m, s)] = float(str(v).replace(",", "").replace("%", ""))
        except ValueError:
            raise InputError("%s line %d: value %r is not a number" % (os.path.basename(path), n, v))
    if not out:
        raise InputError("%s has no figures filled in" % path)
    return out


def compare(positions, a, r, other):
    """[{measure, scenario, label, keel, other, difference, within, kind}] and the notes on where to look."""
    mine = keel_figures(positions, a, r)
    rows = []
    for (m, s), theirs in other.items():
        label, kind, tolerance = MEASURES[m]
        if (m, s) not in mine:
            rows.append({"measure": m, "scenario": s, "label": label, "keel": None, "other": theirs,
                         "difference": None, "within": None, "kind": kind, "tolerance": tolerance})
            continue
        ours = mine[(m, s)]
        if kind == "money":
            diff = 100.0 * (ours - theirs) / abs(theirs) if theirs else None
        else:
            diff = ours - theirs
        rows.append({"measure": m, "scenario": s, "label": label, "keel": ours, "other": theirs,
                     "difference": diff, "within": None if diff is None else abs(diff) <= tolerance,
                     "kind": kind, "tolerance": tolerance})
    order = list(MEASURES)
    rows.sort(key=lambda x: (order.index(x["measure"]), _scenario_order(x["scenario"])))
    return rows, where(rows)


def _scenario_order(name):
    try:
        return (0, float(name.replace("bp", "")))
    except ValueError:
        return (1 if name else -1, name)


def where(rows):
    """The kinds of difference present, in the order to investigate them."""
    off = [x for x in rows if x["within"] is False]
    found = []
    if any(x["measure"] in BALANCES for x in off):
        found.append("balances")
    if any(x["measure"].startswith("nii") and x["scenario"] == "base" for x in off):
        found.append("nii_base")
    if any(x["measure"].startswith("nii") and x["scenario"] not in ("base", "") for x in off):
        found.append("nii_shock")
    if any(x["measure"].startswith("nev") and x["scenario"] == "base" for x in off):
        found.append("nev_base")
    if any(x["measure"].startswith("nev") and x["scenario"] not in ("base", "") for x in off):
        found.append("nev_shock")
    return [(k, WHERE[k]) for k in found]


def page(name, rows, notes, style, other_name="The other model"):
    from keel import report
    k, esc = report.k, html.escape
    agree = sum(1 for x in rows if x["within"])
    judged = sum(1 for x in rows if x["within"] is not None)
    body = []
    for x in rows:
        fmt = (lambda v: "" if v is None else "%.2f%%" % v) if x["kind"] == "pct" else \
            (lambda v: "" if v is None else k(v))
        diff = "" if x["difference"] is None else ("%+.1f%%" % x["difference"] if x["kind"] == "money"
                                                   else "%+.2f pts" % x["difference"])
        status = ("" if x["within"] is None else
                  report.chip("within", "Within %g%s" % (x["tolerance"], "%" if x["kind"] == "money" else " pts"))
                  if x["within"] else report.chip("breach", "Outside %g%s" % (
                      x["tolerance"], "%" if x["kind"] == "money" else " pts")))
        body.append([esc(x["label"]), esc(x["scenario"] or "opening"), fmt(x["other"]), fmt(x["keel"]), diff,
                     status if x["keel"] is not None else "Keel has no such scenario"])
    advice = "".join("<li>%s</li>" % esc(text) for _, text in notes) or \
        "<li>Every figure compared is within tolerance.</li>"
    return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>Second opinion: %s</title>
<style>%s</style></head><body><header class='top'><div class='inner'><h1>Second opinion: %s</h1>
<p class='sub'>Keel beside %s, on the same book. Money in $000.</p></div></header><main>
<p><b>%d of %d</b> figures agree within tolerance.</p>
<h2>Where to look first</h2><ol>%s</ol>
<h2>Figure by figure</h2>%s
<p class='muted'>Tolerances are what a reviewer would accept between two sound models: 0.1%% on opening
balances, 3%% on year-one NII, 5%% on year two, 10%% on NEV, 1.5 points on the NEV ratio and 2 to 5 points on
the changes under shock. Differences outside them are not errors in either model until traced to an
assumption; the list above says where to start.</p></main></body></html>""" % (
        esc(name), style, esc(name), esc(other_name), agree, judged, advice,
        report.table(["Figure", "Scenario", esc(other_name), "Keel", "Difference", ""], body, numeric_from=2))
