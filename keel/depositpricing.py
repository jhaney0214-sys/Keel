"""Deposit pricing: what a rate move or a certificate special really costs.

Raising a share rate pays the higher rate on every dollar already there to
attract the few that are not. The number that matters is the **marginal
cost of new money**: the extra interest a year divided by the extra balance
it brings, set against what the same money costs wholesale (an FHLB advance
or brokered deposit of the same term).

**Rate moves on non-maturity shares.** A product's balance sensitivity is its
annual runoff per 100bp of market-over-share spread: the deposit study's
estimate where its fit is usable (R² 0.2 or better), else the settings'
`runoff_per_100bp`. A move of d basis points narrows the spread by d and,
over a year, brings

    new balance = B x sensitivity x d / 100

at a cost of B x d (every existing dollar reprices) plus the new balance at
the new rate, so

    marginal cost = (r + d) + B x d / new balance = (r + d) + 1 / (100 x sensitivity)

which does not depend on B at all: a product whose balances barely move
with rates buys each new dollar very dear. Cuts run the same arithmetic the
other way, as the saving per dollar lost.

**A certificate special** is offered at `rate` for `term_months` over a
promotion `window_months`, and is expected to raise `volume`. Some of that
volume is money already here:

  - certificates maturing within the window that would have renewed anyway
    (`maturing_renewal` percent of them, from the positions), now at the
    special rather than the standard renewal rate (the curve at the term plus
    the certificate product's spread, the rate the plan would pay);
  - the rest of the volume, split by `sources` (percent), between
    `new_money` and existing share products, each leaving its own rate.

The incremental interest a year over the new money alone is the special's
marginal cost. It is compared with wholesale funding of the same term (the
curve at the term plus `wholesale_spread`, default 0.15%), and the
break-even new-money share (where the two are equal) says how much of the
volume must be new for the special to beat borrowing.
"""

import html

from keel.model import InputError

MOVES = (-50, -25, 25, 50, 100)
SHARE_TYPES = ("administered",)


def sensitivity(product, a, study=None):
    """(annual runoff per 100bp as a decimal, where it came from)."""
    if study:
        for row in study["products"]:
            s = row["sensitivity"]
            if row["product"] == product and s and s["r2"] >= 0.2 and s["runoff_per_100bp"] > 0:
                return s["runoff_per_100bp"], "study (R² %.2f)" % s["r2"]
    return a.products[product].runoff_per_100bp, "settings"


def _wholesale(a, term, spread):
    return (a.curve.rate(max(1, term)) + spread) / 100.0


def rate_moves(positions, a, study=None, moves=MOVES, wholesale_spread=0.15):
    """For each non-maturity share product: its balance, rate, sensitivity and
    the marginal cost of new money (or saving per dollar lost) at each move."""
    books = {}
    for p in positions:
        if p.side == "liability" and p.rate_type in SHARE_TYPES:
            b = books.setdefault(p.product, [0.0, 0.0])
            b[0] += p.balance
            b[1] += p.balance * p.rate
    out = []
    for product, (balance, interest) in sorted(books.items(), key=lambda x: -x[1][0]):
        if balance <= 0:
            continue
        rate = interest / balance
        sens, source = sensitivity(product, a, study)
        floor = a.products[product].rate_floor
        cells = []
        for d in moves:
            # A cut cannot take the rate below the product's floor: it moves
            # only as far as the floor, and not at all when already there.
            applied = max(d, int(round((floor - rate) * 10000))) if d < 0 else d
            change = balance * sens * applied / 100.0
            extra = balance * applied / 10000.0 + change * (rate + applied / 10000.0)
            cells.append({"move": d, "applied": applied, "balance_change": change, "cost_change": extra,
                          "marginal": extra / change if change else None})
        out.append({"product": product, "balance": balance, "rate": rate, "sensitivity": sens, "source": source,
                    "moves": cells})
    return {"products": out, "wholesale_12": _wholesale(a, 12, wholesale_spread),
            "wholesale_spread": wholesale_spread / 100.0}


def _pct(spec, key, default):
    value = spec.get(key, default)
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        raise InputError("special: %s must be a percent, not %r" % (key, value))


def special(positions, a, spec, grid=True):
    """The certificate special's volume by source, its incremental cost and
    marginal cost of new money, against wholesale."""
    try:
        rate = float(spec["rate"]) / 100.0
        term = int(spec["term_months"])
        volume = float(spec["volume"])
    except (KeyError, TypeError, ValueError):
        raise InputError("special: give rate (percent), term_months and volume")
    window = int(spec.get("window_months", 3))
    product = spec.get("product", "certificates")
    if product not in a.products:
        raise InputError("special: %r is not a product" % product)
    renewal = _pct(spec, "maturing_renewal", 70)
    wholesale_spread = float(spec.get("wholesale_spread", 0.15))
    standard = (a.curve.rate(term) / 100.0) + a.products[product].spread
    maturing = [p for p in positions if p.product == product and p.side == "liability" and 0 < p.term_months <= window]
    maturing_balance = sum(p.balance for p in maturing)
    rollover = min(volume, maturing_balance * renewal)
    sources = {k: float(v) / 100.0 for k, v in (spec.get("sources") or {"new_money": 100}).items()}
    total = sum(sources.values())
    if abs(total - 1.0) > 1e-6:
        raise InputError("special: sources must add to 100 (they add to %g)" % (100 * total))
    rates = {}
    for p in positions:
        if p.side == "liability":
            r = rates.setdefault(p.product, [0.0, 0.0])
            r[0] += p.balance
            r[1] += p.balance * p.rate
    rest = volume - rollover
    rows = [{"source": "maturing certificates renewing", "balance": rollover, "old_rate": standard,
             "note": "%s of $%s maturing within %d months, at the %.2f%% standard renewal rate instead" % (
                 "%g%%" % (100 * renewal), "{:,.0f}".format(maturing_balance), window, 100 * standard)}]
    new_money = 0.0
    for source, share in sources.items():
        amount = rest * share
        if source == "new_money":
            new_money = amount
            rows.append({"source": "new money", "balance": amount, "old_rate": None, "note": ""})
            continue
        if source not in rates or rates[source][0] <= 0:
            raise InputError("special: source %r is not a liability product with a balance" % source)
        if amount > rates[source][0]:
            raise InputError("special: %s cannot give $%s; it holds $%s" % (
                source, "{:,.0f}".format(amount), "{:,.0f}".format(rates[source][0])))
        rows.append({"source": source.replace("_", " "), "balance": amount,
                     "old_rate": rates[source][1] / rates[source][0], "note": "leaves at its average rate"})
    cost = volume * rate - sum(x["balance"] * x["old_rate"] for x in rows if x["old_rate"] is not None)
    wholesale = _wholesale(a, term, wholesale_spread)
    migrated_cost = sum(x["balance"] * (rate - x["old_rate"]) for x in rows if x["old_rate"] is not None)
    # Break-even: the new-money share n of the volume beyond renewals at which
    # the marginal cost equals wholesale w, the other sources keeping their
    # mix (old rate m). With R rolled over at standard rate q and V the rest:
    #   R(s - q) + V(1 - n)(s - m) + V n s = V n w  =>  n = [R(s - q) + V(s - m)] / [V(w - m)]
    migrating = [x for x in rows[1:] if x["old_rate"] is not None]
    moved = sum(x["balance"] for x in migrating)
    breakeven = None
    if rest > 0 and moved > 0:
        m = sum(x["balance"] * x["old_rate"] for x in migrating) / moved
        if wholesale > m:
            breakeven = (rollover * (rate - standard) + rest * (rate - m)) / (rest * (wholesale - m))
    return {"name": spec.get("name", "Certificate special"), "rate": rate, "term_months": term, "window": window,
            "volume": volume, "standard_rate": standard, "maturing_balance": maturing_balance, "rows": rows,
            "new_money": new_money, "incremental_cost": cost, "migrated_cost": migrated_cost,
            "marginal": cost / new_money if new_money else None, "wholesale": wholesale,
            "wholesale_cost": new_money * wholesale,
            "breakeven_new_share": breakeven, "grid": _grid(positions, a, spec) if grid else []}


def _grid(positions, a, spec):
    """Marginal cost at the special's rate +/-25bp and new-money shares of 20% to 80%."""
    out = []
    base = dict(spec)
    others = {k: v for k, v in (spec.get("sources") or {}).items() if k != "new_money"}
    total_other = sum(float(v) for v in others.values())
    if not total_other:
        return []               # every dollar is new money: there is no share to vary
    for bump in (-25, 0, 25):
        row = {"rate": float(spec["rate"]) + bump / 100.0, "cells": []}
        for share in (20, 40, 60, 80):
            trial = dict(base, rate=row["rate"])
            trial["sources"] = dict({k: float(v) * (100 - share) / total_other for k, v in others.items()},
                                    new_money=share)
            result = special(positions, a, trial, grid=False)
            row["cells"].append({"new_share": share, "marginal": result["marginal"]})
        out.append(row)
    return out


def page(result, style):
    from keel import report
    k, pct, esc = report.k, report.pct, html.escape
    parts = []
    s = result.get("special")
    if s:
        better = s["marginal"] is not None and s["marginal"] <= s["wholesale"]
        parts.append("<h2>%s</h2>" % esc(s["name"]))
        if s["new_money"] <= 0:
            # Renewals take the whole volume: there is no new money to price,
            # only a higher rate on money that would have stayed anyway.
            parts.append("<p>A certificate special at <b>%s</b> for %d months, offered for %d months, raising "
                         "<b>$%sK</b>, all of it from certificates maturing in the window that would have renewed "
                         "anyway. It raises <b>no new money</b>: it pays <b>$%sK</b> a year more to keep money "
                         "already here. %s</p>" % (
                             pct(s["rate"]), s["term_months"], s["window"], k(s["volume"]),
                             k(s["incremental_cost"]),
                             report.chip("breach", "a cost with nothing raised")))
        else:
            parts.append("<p>A certificate special at <b>%s</b> for %d months, offered for %d months, raising "
                         "<b>$%sK</b>. Only <b>$%sK</b> of it is new money; the rest was already here and now "
                         "costs more. Each new dollar costs <b>%s</b> a year, against <b>%s</b> for wholesale money "
                         "of the same term: %s</p>" % (
                             pct(s["rate"]), s["term_months"], s["window"], k(s["volume"]), k(s["new_money"]),
                             pct(s["marginal"]), pct(s["wholesale"]),
                             report.chip("within" if better else "breach",
                                         "cheaper than borrowing" if better else "dearer than borrowing")))
        parts.append(report.table(["Where the money comes from", "Balance ($000)", "Rate it paid before", "Note"],
                                  [[esc(x["source"]), k(x["balance"]),
                                    "" if x["old_rate"] is None else pct(x["old_rate"]), esc(x["note"])]
                                   for x in s["rows"]]))
        parts.append(report.table(["", "Annual ($000)"], [
            ["Interest on the special", k(s["volume"] * s["rate"])],
            ["Less what the money already here was paid", k(-(s["volume"] * s["rate"] - s["incremental_cost"]))],
            ["Incremental interest", k(s["incremental_cost"])],
            ["of which repricing money already here", k(s["migrated_cost"])],
            ["The new money borrowed wholesale instead", k(s["wholesale_cost"])]]))
        n = s["breakeven_new_share"]
        if n is not None and n <= 1:
            parts.append("<p>The special beats borrowing only if more than <b>%s</b> of the volume beyond renewals "
                         "is new money.</p>" % pct(n, 0))
        elif n is not None:
            parts.append("<p>No share of new money makes it cheaper than borrowing: repricing the renewals and the "
                         "balances that move over costs more than wholesale money would, even if every other dollar "
                         "were new.</p>")
        grid = s["grid"]
        if grid and s["new_money"] > 0:          # no new money: nothing for the grid to price
            parts.append("<h3>Marginal cost of new money by rate and new-money share</h3>")
            parts.append(report.table(["Special rate"] + ["%d%% new" % c["new_share"] for c in grid[0]["cells"]],
                                      [[pct(row["rate"] / 100.0)] + [
                                          "n/a" if c["marginal"] is None else pct(c["marginal"]) + (
                                              "" if c["marginal"] <= s["wholesale"] else " " + report.chip(
                                                  "breach", "over wholesale")) for c in row["cells"]]
                                       for row in grid]))
    m = result.get("moves")
    if m and m["products"]:
        parts.append(moves_table(m))
    return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>Deposit pricing</title>
<style>%s</style></head><body><header class='top'><div class='inner'><h1>Deposit pricing</h1>
<p class='sub'>The marginal cost of new money, against wholesale funding.</p></div></header><main>%s
<p class='muted'>Money already here is priced at what it paid before; new money at the offered rate. Wholesale is
the curve at the same term plus a %s spread.</p></main></body></html>""" % (
        style, "".join(parts), pct((result.get("moves") or {}).get("wholesale_spread", 0.0015)))


def moves_table(m):
    from keel import report
    k, pct = report.k, report.pct
    head = ["Product", "Balance ($000)", "Rate", "Balance per 100bp", "From"] + [
        "%+dbp" % c["move"] for c in m["products"][0]["moves"]]
    rows = []
    for p in m["products"]:
        cells = []
        for c in p["moves"]:
            if c["move"] < 0 and c["applied"] >= 0:
                cells.append("at its floor")
            elif c["marginal"] is None:
                cells.append("no money moves")
            elif c["move"] > 0:
                cells.append(pct(c["marginal"]))
            else:
                cells.append("saves " + pct(c["marginal"]) + (
                    "" if c["applied"] == c["move"] else " (%+dbp, to the floor)" % c["applied"]))
        rows.append([report.label(p["product"]), k(p["balance"]), pct(p["rate"]), pct(p["sensitivity"], 1),
                     p["source"]] + cells)
    return ("<h3 id='deposit-pricing'>Deposit pricing: marginal cost of new money</h3>" + report.table(head, rows)
            + "<p class='muted'>A raise pays the new rate on every dollar already in the product; the cell is the "
              "extra interest a year over the extra balance a year brings, set by the balance sensitivity. A cut "
              "is the saving per dollar that leaves. Twelve-month wholesale money costs %s. Where a raise costs "
              "more than that, borrowing is cheaper than paying up.</p>" % pct(m["wholesale_12"]))
