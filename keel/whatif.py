"""What-if: change an assumption or the balance sheet, and see every measure move.

    python -m keel whatif <credit union folder> <whatif.json>

A what-if file:

    {
      "name": "Fund auto growth with an FHLB advance",
      "assumptions": {"products.new_auto.growth": 10, "products.money_market.beta": 70},
      "actions": [
        {"add": {"id": "fhlb_new", "name": "New FHLB advance", "product": "borrowings",
                 "side": "liability", "balance": 50000000, "rate": 4.10, "rate_type": "fixed",
                 "term_months": 36, "amortization": "bullet",
                 "draws_on": "FHLB unused borrowing capacity"}},
        {"scale": {"product": "treasuries", "factor": 0.5}}
      ]
    }

`assumptions` sets values by dotted path, in the same units as
assumptions.json (percent). `actions` change the balance sheet on the
analysis date: `add` a position, or `scale` every position of a product.
Each action settles through cash, as the real transaction would, so the
balance sheet still balances: borrowing adds cash, buying spends it, selling
at book returns it. The comparison runs the base and the what-if through the
same measures and shows both, and the change.
"""

import copy
import dataclasses
import html
import json
import os

from keel import engine, measures, model
from keel.curve import Scenario
from keel.engine import CASH
from keel.model import InputError, Position


def set_path(raw, path, value):
    keys = path.split(".")
    node = raw
    for key in keys[:-1]:
        if key not in node:
            raise InputError("what-if: %r is not in assumptions.json" % path)
        node = node[key]
    if keys[-1] not in node and not isinstance(node, dict):
        raise InputError("what-if: %r is not in assumptions.json" % path)
    node[keys[-1]] = value


def apply(positions, raw_assumptions, spec):
    """(positions, assumptions, notes) after the what-if's changes."""
    raw = copy.deepcopy(raw_assumptions)
    notes = []
    for path, value in spec.get("assumptions", {}).items():
        set_path(raw, path, value)
        notes.append("%s set to %s" % (path, value))
    assumptions = model.parse_assumptions(raw)
    book = [p.copy() for p in positions]
    cash = [p for p in book if p.product == CASH]
    if not cash:
        raise InputError("what-if: the balance sheet has no cash position to settle through")
    cash = cash[0]
    for action in spec.get("actions", []):
        if "add" in action:
            a = dict(action["add"])
            p = Position(id=a["id"], name=a.get("name", a["id"]), product=a["product"], side=a["side"],
                         balance=float(a["balance"]), rate=float(a.get("rate", 0)) / 100.0,
                         rate_type=a.get("rate_type", "fixed"), index=a.get("index", ""),
                         margin=float(a.get("margin", 0)) / 100.0, reset_months=int(a.get("reset_months", 0)),
                         term_months=int(a.get("term_months", 0)), amortization=a.get("amortization", "bullet"),
                         amort_months=int(a.get("amort_months", 0)), call_months=int(a.get("call_months", 0)))
            if any(x.id == p.id for x in book):
                raise InputError("what-if: id %r already exists" % p.id)
            book.append(p)
            cash.balance += p.balance if p.side == "liability" else -p.balance
            notes.append("added %s: %s %s at %.2f%%" % (p.name, p.side, "{:,.0f}".format(p.balance), 100 * p.rate))
            source = a.get("draws_on")
            if source:
                # Borrowing from a contingent source uses up that much of it:
                # the liquidity stress must not count the same capacity twice.
                # Found when a $50M FHLB advance *raised* stress liquidity $41M.
                matched = [i for i, (n, _) in enumerate(assumptions.contingent) if n == source]
                if not matched:
                    raise InputError("what-if: draws_on %r is not a contingent source in assumptions.json"
                                     % source)
                i = matched[0]
                name, capacity = assumptions.contingent[i]
                assumptions.contingent[i] = (name, capacity - p.balance)
                notes.append("%s reduced by %s" % (name, "{:,.0f}".format(p.balance)))
            elif p.side == "liability" and p.product == "borrowings":
                notes.append("NOTE: this borrowing names no draws_on source, so no contingent capacity was "
                             "reduced; if it comes from a counted source, liquidity is overstated.")
        elif "scale" in action:
            # Selling assets is priced at market by default: proceeds are the
            # sold share of each position's base-scenario value, and the
            # difference from book is a realized gain or loss that lands in
            # equity on the analysis date. Found when a what-if sold
            # underwater MBS at book and showed no loss.
            product, factor = action["scale"]["product"], float(action["scale"]["factor"])
            price = action["scale"].get("price", "market")
            moved, realized = 0.0, 0.0
            base = assumptions.scenarios[0]
            for p in book:
                if p.product == product and p is not cash:
                    change = p.balance * (factor - 1.0)
                    if p.side == "asset" and change < 0 and price == "market":
                        value = measures.nev([p], assumptions, base).pv_assets
                        proceeds = -change * (value / p.balance if p.balance else 1.0)
                        realized += proceeds - (-change)
                        p.balance += change
                        moved -= proceeds
                    else:
                        p.balance += change
                        moved += change if p.side == "asset" else -change
            if moved == 0.0:
                raise InputError("what-if: no positions of product %r to scale" % product)
            cash.balance -= moved
            notes.append("scaled %s by %s at %s (cash %s %s)" % (product, factor, price, "+" if moved < 0 else "-",
                                                                 "{:,.0f}".format(abs(moved))))
            if realized:
                notes.append("realized %s of %s, charged to net worth on the analysis date" % (
                    "gain" if realized > 0 else "loss", "{:,.0f}".format(abs(realized))))
        else:
            raise InputError("what-if: unknown action %r" % action)
    if cash.balance < 0:
        notes.append("WARNING: the actions leave cash negative (%s); the projection borrows overnight to "
                     "cover it from month 1." % "{:,.0f}".format(cash.balance))
    model.check(book, assumptions)
    return book, assumptions, notes


def key_measures(positions, a):
    """The numbers an ALCO compares, from one full pass of the model."""
    base = a.scenarios[0]
    up = next(s for s in a.scenarios if s.name == "+300")
    down = next(s for s in a.scenarios if s.name == "-300")
    run = engine.going_concern(positions, a, base)
    nii = lambda r, y: measures.income_statement(measures.year(r, y))["net_interest_income"]  # noqa: E731
    up_run, down_run = engine.going_concern(positions, a, up), engine.going_concern(positions, a, down)
    mgmt0, mgmt3 = measures.nev(positions, a, base), measures.nev(positions, a, up)
    sup0 = measures.nev(positions, a, Scenario("base", 0, floor=a.rate_floor), supervisory=True)
    sup3 = measures.nev(positions, a, Scenario("+300", 300, floor=a.rate_floor), supervisory=True)
    test = measures.ncua_test(sup0, sup3)
    stressed = engine.going_concern(positions, a, base, stress=True)
    peak, _ = measures.funding_gap(run)
    y1 = measures.income_statement(measures.year(run, 1))
    limits = _limits(positions, a, run, {"+300": up_run, "-300": down_run}, {"+300": mgmt3}, mgmt0, stressed)
    return [
        ("Year-one NII, base", nii(run, 1), "money"),
        ("Year-two NII, base", nii(run, 2), "money"),
        ("Year-one NII change at +300bp", nii(up_run, 1) / nii(run, 1) - 1, "pct"),
        ("Year-one NII change at -300bp", nii(down_run, 1) / nii(run, 1) - 1, "pct"),
        ("Year-one net income", y1["net_income"], "money"),
        ("Net worth ratio, month 12", run[11].equity / run[11].assets, "pct"),
        ("Net worth ratio, month 60", run[-1].equity / run[-1].assets, "pct"),
        ("NEV ratio, own assumptions, base", mgmt0.ratio, "pct"),
        ("NEV ratio, own assumptions, +300bp", mgmt3.ratio, "pct"),
        ("Supervisory NEV ratio after +300bp", test["post_shock_ratio"], "pct"),
        ("Supervisory NEV change at +300bp", -test["sensitivity_value_decline"], "pct"),
        ("Supervisory rating (ratio / change)", "%s / %s" % (test["ratio_rating"], test["sensitivity_rating"]), "text"),
        ("Lowest available liquidity in the stress year", min(m.available_liquidity for m in stressed[:12]), "money"),
        ("Peak overnight borrowing, base plan", peak, "money"),
    ] + [(x.label, x, "limit") for x in limits]


def _limits(positions, a, run, runs, nevs, nev_base, stressed):
    """Every policy limit, measured the way the full report measures it, from
    the runs already made plus the few the limits add (+/-200bp, -300bp NEV)."""
    from keel import results
    by_name = {s.name: s for s in a.scenarios}
    for name in ("+200", "-200"):
        if name in by_name:
            runs[name] = engine.going_concern(positions, a, by_name[name])
    if "-300" in by_name:
        nevs["-300"] = measures.nev(positions, a, by_name["-300"])
    nii = lambda r: sum(m.nii for m in r[:12])  # noqa: E731
    b1 = nii(run)
    worst_nii = lambda keys: min(nii(runs[k]) / b1 - 1 for k in keys if k in runs)  # noqa: E731
    ratios = measures.ratios(positions, a)
    measured = {
        "nii_decline_300": -100 * worst_nii(("+300", "-300")),
        "nii_decline_200": -100 * worst_nii(("+200", "-200")),
        "nev_decline_300": -100 * min(n.nev / nev_base.nev - 1 for n in nevs.values()),
        "nev_ratio_min": 100 * min(n.ratio for n in nevs.values()),
        "net_worth_min": 100 * min(m.equity / m.assets for m in run),
        "liquid_to_shares_min": 100 * ratios["liquid_to_shares"],
        "loans_to_shares_max": 100 * ratios["loans_to_shares"],
        "borrowings_to_assets_max": 100 * ratios["borrowings_to_assets"],
        "survival_months_min": results.survival_value(measures.survival(stressed)),
    }
    return results.evaluate_limits(measured, a)


STATUS = {"within": ("good", "&#10003;", "Within"), "near": ("warning", "&#9650;", "Near"),
          "breach": ("critical", "&#10005;", "Breach")}


def _chip(status):
    cls, icon, word = STATUS[status]
    return "<span class='chip %s'><i aria-hidden='true'>%s</i>%s</span>" % (cls, icon, word)


def _fmt(value, kind):
    if kind == "limit":
        shown = "12+ months" if value.value is None else "%.1f%s" % (
            value.value, " months" if value.unit == "months" else "%")
        return "%s %s" % (shown, _chip(value.status))
    if kind == "money":
        return "{:,.0f}".format(value / 1000.0)
    if kind == "pct":
        return "{:,.2f}%".format(100.0 * value)
    return html.escape(str(value))


def _delta(before, after, kind):
    if kind == "limit":
        if before.value is None or after.value is None:
            return "" if before.value == after.value else "changed"
        text = "{:+,.1f}{}".format(after.value - before.value, " months" if after.unit == "months" else " pts")
        return text if before.status == after.status else "%s, now %s" % (text, STATUS[after.status][2].lower())
    if kind == "money":
        return "{:+,.0f}".format((after - before) / 1000.0)
    if kind == "pct":
        return "{:+,.2f} pts".format(100.0 * (after - before) + 0.0).replace("-0.00", "+0.00")
    return "" if before == after else "changed"


def tables(before, after=None):
    """The measures as two tables, the numbers then the policy limits; with
    `after`, each row compares the base with the what-if."""
    out = []
    for heading, limits in (("Key measures ($000)", False), ("Policy limits", True)):
        pairs = [(b, a) for b, a in zip(before, after or before) if (b[2] == "limit") == limits]
        if after is None:
            head = "<th>Measure</th><th class='num'>Base</th>"
            rows = ["<tr><td>%s</td><td class='num'>%s</td></tr>" % (html.escape(b[0]), _fmt(b[1], b[2]))
                    for b, _ in pairs]
        else:
            head = ("<th>Measure</th><th class='num'>Base</th><th class='num'>What-if</th>"
                    "<th class='num'>Change</th>")
            rows = ["<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s</td><td class='num'>%s</td></tr>" % (
                html.escape(b[0]), _fmt(b[1], b[2]), _fmt(a[1], a[2]), _delta(b[1], a[1], b[2])) for b, a in pairs]
        out.append("<h2>%s</h2><div class='wrap'><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>" % (
            heading, head, "".join(rows)))
    return "".join(out)


def comparison(title, notes, before, after):
    from keel.report import STYLE
    moved = [a[1] for b, a in zip(before, after) if a[2] == "limit" and a[1].status != b[1].status]
    verdict = ("No limit changes status." if not moved else
               "Limits that change status: %s." % "; ".join(
                   "%s, now %s" % (x.label, STATUS[x.status][2].lower()) for x in moved))
    return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>What-if: %s</title>
<style>%s</style></head><body><header class='top'><div class='inner'><h1>What-if: %s</h1>
<p class='sub'>The same model, measures and scenarios as the full report; only the changes below differ.</p>
</div></header><main><h2>What changed</h2><ul>%s</ul><p><b>%s</b></p>%s
<p class='muted'>The what-if's own full report is <a href='report.html'>report.html</a> in this folder.</p>
</main></body></html>""" % (html.escape(title), STYLE, html.escape(title),
                            "".join("<li>%s</li>" % html.escape(n) for n in notes), html.escape(verdict),
                            tables(before, after))
