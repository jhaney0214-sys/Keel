"""Investment purchases and swaps: what a trade earns, what it costs up
front, and how long it takes to earn that back.

    python -m keel swap <folder> <trade.json>

A trade sells securities the institution holds, buys new ones, or both:

    {
      "name": "Sell the 2021 callables and munis, buy current-coupon MBS",
      "sell": ["SYN00014", {"id": "SYN00034", "share": 0.5, "price": 78.25}],
      "buy": [{"name": "FNMA 30-year 5.5%", "product": "agency_mbs", "amount": "proceeds",
               "yield": 5.10, "term_months": 360, "amortization": "level"}]
    }

A sale is the whole position unless `share` says less. It is priced at
Keel's own market value (the position's cash flows discounted on today's
curve, as the NEV and portfolio pages price it) unless `price` gives it, in
percent of book. The difference from book is realized on the analysis date
and comes out of net worth at once. A purchase's `amount` is dollars, or
"proceeds" for whatever the sales raise; its yield is `yield` (percent) or
`spread` over the curve at its term. Everything settles through cash, so a
purchase with no sale is funded from cash, and proceeds not reinvested stay
in cash at the short rate.

The analysis runs the book before and after through the same plan, in the
base case and at +/-300bp, and reports:

  - the trade: each security sold and bought, with book, market, the gain or
    loss, yield, average life and duration;
  - the yield pickup: income on what is bought less income given up;
  - the earn-back: the month the plan's cumulative extra net interest income
    first covers the realized loss, in each scenario;
  - the effect on the whole institution: NII, NEV and its sensitivity,
    liquidity, net worth and every policy limit, side by side.

The plan reinvests what the trade leaves in cash and what runs off, so the
NII difference includes that: a swap that shortens the book shows its
reinvestment at forward rates, not only its coupon.
"""

import html

from keel import engine, measures, model, whatif
from keel.engine import CASH
from keel.model import InputError, Position

SCENARIOS = ("base", "+300", "-300")


def _sales(spec):
    for item in spec.get("sell", []):
        if isinstance(item, str):
            yield {"id": item, "share": 1.0, "price": None}
        else:
            if "id" not in item:
                raise InputError("swap: each sale needs an id")
            share = float(item.get("share", 1.0))
            if not 0 < share <= 1:
                raise InputError("swap: the share sold of %s must be above 0 and at most 1" % item["id"])
            yield {"id": item["id"], "share": share,
                   "price": None if item.get("price") is None else float(item["price"]) / 100.0}


def build(positions, a, spec):
    """(changed positions, trade dict). The trade lists what was sold and
    bought, the realized gain or loss and the cash moved."""
    book = [p.copy() for p in positions]
    by_id = {p.id: p for p in book}
    cash = next((p for p in book if p.product == CASH), None)
    if cash is None:
        raise InputError("swap: the balance sheet has no cash position to settle through")
    base = a.scenarios[0]
    sold, proceeds_total, realized_total = [], 0.0, 0.0
    for sale in _sales(spec):
        p = by_id.get(sale["id"])
        if p is None:
            raise InputError("swap: %r is not a position id (securities keep their security_id)" % sale["id"])
        if p.side != "asset" or p.product == CASH:
            raise InputError("swap: %s is not an asset that can be sold" % sale["id"])
        stats = _analytics(p, a)
        amount = p.balance * sale["share"]
        market = stats["market"] * sale["share"]
        proceeds = amount * sale["price"] if sale["price"] is not None else market
        sold.append({"id": p.id, "name": p.name, "product": p.product, "book": amount, "market": market,
                     "proceeds": proceeds, "gain": proceeds - amount, "yield": p.rate, "wal": stats["wal"],
                     "duration": stats["duration"], "priced": "given" if sale["price"] is not None else "model"})
        p.balance -= amount
        cash.balance += proceeds
        proceeds_total += proceeds
        realized_total += proceeds - amount
    bought, spent = [], 0.0
    for n, item in enumerate(spec.get("buy", []), 1):
        product = item.get("product")
        if product not in a.products:
            raise InputError("swap: purchase %d: %r is not a product in the settings" % (n, product))
        amount = item.get("amount", "proceeds")
        amount = proceeds_total - spent if amount == "proceeds" else float(amount)
        if amount <= 0:
            raise InputError("swap: purchase %d has nothing to spend (amount %s)" % (n, "{:,.0f}".format(amount)))
        term = int(item.get("term_months", a.products[product].new_term or 12))
        if "yield" in item:
            rate = float(item["yield"]) / 100.0
        elif "spread" in item:
            rate = (a.curve.rate(term) + float(item["spread"])) / 100.0
        else:
            raise InputError("swap: purchase %d needs a yield or a spread" % n)
        amortization = item.get("amortization", "bullet")
        p = Position(id="buy%02d" % n, name=item.get("name", "purchase %d" % n), product=product, side="asset",
                     balance=amount, rate=rate, rate_type="fixed", term_months=term, amortization=amortization,
                     call_months=int(item.get("call_months", 0)), amort_months=int(item.get("amort_months", 0)))
        book.append(p)
        cash.balance -= amount
        spent += amount
        stats = _analytics(p, a)
        bought.append({"id": p.id, "name": p.name, "product": product, "book": amount, "market": stats["market"],
                       "yield": rate, "wal": stats["wal"], "duration": stats["duration"], "term_months": term})
    if not sold and not bought:
        raise InputError("swap: the trade sells and buys nothing")
    book = [p for p in book if p.balance > 0.005 or p.product == CASH or p.id not in {s["id"] for s in sold}]
    model.check(book, a)
    return book, {"sold": sold, "bought": bought, "proceeds": proceeds_total, "spent": spent,
                  "realized": realized_total, "cash_change": proceeds_total - spent,
                  "cash_after": cash.balance}


def _analytics(p, a):
    one = p.copy()
    one.balance = one.balance or 1.0
    rows = measures.security_analytics([one], a, {one.product})
    if not rows:
        return {"market": p.balance, "wal": 0.0, "duration": 0.0}
    r = rows[0]
    scale = p.balance / one.balance if one.balance else 0.0
    return {"market": r["market"] * scale, "wal": r["wal"], "duration": r["duration"]}


def _scenario(a, name):
    return next(s for s in a.scenarios if s.name == name)


def earn_back(before, after, loss):
    """The first month the cumulative extra NII covers `loss` (a positive
    number), with the cumulative path; None when it never does."""
    running, path, month = 0.0, [], None
    for b, x in zip(before, after):
        running += x.nii - b.nii
        path.append(running)
        if month is None and loss > 0 and running >= loss:
            month = b.month
    if loss <= 0:
        month = 0
    return month, path


def analyse(positions, a, spec):
    changed, trade = build(positions, a, spec)
    loss = -trade["realized"]
    runs = {}
    for name in SCENARIOS:
        s = _scenario(a, name)
        before = engine.going_concern(positions, a, s)
        after = engine.going_concern(changed, a, s)
        month, path = earn_back(before, after, loss)
        years = []
        for y in range(1, len(before) // 12 + 1):
            b = measures.income_statement(measures.year(before, y))["net_interest_income"]
            x = measures.income_statement(measures.year(after, y))["net_interest_income"]
            years.append({"year": y, "before": b, "after": x, "change": x - b})
        runs[name] = {"earn_back": month, "cumulative": path, "years": years,
                      "net_worth_before": before[-1].equity / before[-1].assets,
                      "net_worth_after": after[-1].equity / after[-1].assets}
    sold_income = sum(x["book"] * x["yield"] for x in trade["sold"])
    bought_income = sum(x["book"] * x["yield"] for x in trade["bought"])
    short = a.curve.rate(a.short_tenor) / 100.0
    cash_income = trade["cash_change"] * short
    _, assets, _, equity = engine.opening(positions)
    _, assets_after, _, equity_after = engine.opening(changed)
    avg = lambda rows, f: (sum(r["book"] * r[f] for r in rows) / sum(r["book"] for r in rows)  # noqa: E731
                           if rows and sum(r["book"] for r in rows) else None)
    summary = {"sold_book": sum(x["book"] for x in trade["sold"]), "sold_yield": avg(trade["sold"], "yield"),
               "sold_duration": avg(trade["sold"], "duration"), "sold_wal": avg(trade["sold"], "wal"),
               "bought_yield": avg(trade["bought"], "yield"), "bought_duration": avg(trade["bought"], "duration"),
               "bought_wal": avg(trade["bought"], "wal"),
               "pickup": bought_income + cash_income - sold_income,
               "income_given_up": sold_income, "income_bought": bought_income, "cash_income": cash_income,
               "net_worth_before": equity / assets, "net_worth_after": equity_after / assets_after}
    return {"spec": spec, "trade": trade, "summary": summary, "runs": runs,
            "before": whatif.key_measures(positions, a), "after": whatif.key_measures(changed, a)}


def _months(m):
    if m is None:
        return "not within the plan"
    if m == 0:
        return "nothing to earn back"
    return "month %d (%.1f years)" % (m, m / 12.0)


def _by(m):
    return ("earns the loss back by <b>month %d</b> (%.1f years)" % (m, m / 12.0) if m
            else "does not earn it back within the plan")


def page(result, style):
    from keel import charts, report
    k, pct, esc = report.k, report.pct, html.escape
    t, s, runs = result["trade"], result["summary"], result["runs"]
    name = result["spec"].get("name", "Investment trade")
    parts = []
    loss = -t["realized"]
    base = runs["base"]
    y1 = base["years"][0]["change"] if base["years"] else 0.0
    headline = ("<p>The trade %s <b>$%sK</b> on the analysis date%s, and picks up <b>$%sK</b> a year of income on "
                "today's yields.</p>" % (
                    "realizes a loss of" if loss > 0 else "realizes a gain of", k(abs(t["realized"])),
                    ", taking the net worth ratio from %s to %s" % (pct(s["net_worth_before"]),
                                                                   pct(s["net_worth_after"])) if loss > 0 else "",
                    k(s["pickup"])))
    if loss > 0:
        headline += ("<p>In the base plan the extra net interest income %s; at +300bp it %s, and at -300bp it %s. "
                     "Counting the loss, year-one net income changes by <b>$%sK</b>: the plan below books the "
                     "loss against net worth on the analysis date, so its year-one net income leaves it out.</p>" % (
                         _by(base["earn_back"]), _by(runs["+300"]["earn_back"]), _by(runs["-300"]["earn_back"]),
                         k(y1 - loss)))
    if not t["sold"]:
        headline = ("<p>A purchase of <b>$%sK</b> funded from cash, picking up <b>$%sK</b> a year over the short "
                    "rate on today's yields.</p>" % (k(t["spent"]), k(s["pickup"])))
    parts.append(headline)
    if t["sold"]:
        parts.append("<h2>Sold ($000)</h2>")
        parts.append(report.table(["Security", "Product", "Book", "Market", "Proceeds", "Gain (loss)", "Book yield",
                                   "Average life", "Duration"],
                                  [[esc(x["name"]) + " <span class='muted'>%s</span>" % esc(x["id"]),
                                    report.label(x["product"]), k(x["book"]), k(x["market"]), k(x["proceeds"]),
                                    k(x["gain"]), pct(x["yield"]), "%.1f" % x["wal"], "%.1f" % x["duration"]]
                                   for x in t["sold"]], numeric_from=2))
    if t["bought"]:
        parts.append("<h2>Bought ($000)</h2>")
        parts.append(report.table(["Security", "Product", "Cost", "Market today", "Yield", "Average life",
                                   "Duration"],
                                  [[esc(x["name"]), report.label(x["product"]), k(x["book"]), k(x["market"]),
                                    pct(x["yield"]), "%.1f" % x["wal"], "%.1f" % x["duration"]]
                                   for x in t["bought"]], numeric_from=2))
        parts.append("<p class='muted'>Market today is the purchase priced as Keel prices the portfolio: its cash "
                     "flows on today's curve plus the product's discount spread. Above cost, it yields more than "
                     "that; below, less.</p>")
    rows = [["Income given up on what is sold", k(-s["income_given_up"])],
            ["Income on what is bought", k(s["income_bought"])]]
    if abs(t["cash_change"]) >= 0.5:
        rows.append(["Cash %s, at the short rate" % ("left over" if t["cash_change"] > 0 else "spent"),
                     k(s["cash_income"])])
    rows.append(["Yield pickup, a year", k(s["pickup"])])
    parts.append("<h2>Yield pickup ($000, on today's balances and yields)</h2>")
    parts.append(report.table(["", "Amount"], rows, total_last=True))
    if s["sold_duration"] is not None and s["bought_duration"] is not None:
        parts.append("<p>Duration goes from %.1f years on what is sold to %.1f on what is bought; average life from "
                     "%.1f to %.1f years.</p>" % (s["sold_duration"], s["bought_duration"], s["sold_wal"],
                                                  s["bought_wal"]))
    parts.append("<h2>%s</h2>" % ("Earning it back" if loss > 0 else "In the plan"))
    if loss > 0:
        path = base["cumulative"]
        step = 1 if len(path) <= 120 else 12
        parts.append(charts.line([(m, path[m - 1] / 1000.0, "month %d: cumulative extra NII $%sK" % (m, k(path[m - 1])))
                                  for m in range(step, len(path) + 1, step)],
                                 lambda v: "%.0fK" % v, "Cumulative extra NII, base plan, against the $%sK loss "
                                 "(dashed), $000" % k(loss), reference=loss / 1000.0, reference_label="",
                                 x_ticks=set(range(12, len(path) + 1, 12 if len(path) <= 120 else 60))))
    parts.append(report.table(
        ["Scenario", "Earn-back", "Year 1 NII change", "Year 2", "Year 3", "Whole plan", "Net worth ratio, end of "
         "plan, before", "After"],
        [[esc(n), _months(r["earn_back"])] + [k(y["change"]) for y in r["years"][:3]] +
         [k(r["cumulative"][-1] if r["cumulative"] else 0.0), pct(r["net_worth_before"]), pct(r["net_worth_after"])]
         for n, r in runs.items()], numeric_from=2))
    parts.append("<p class='muted'>Each scenario runs the book with and without the trade through the plan, "
                 "which reinvests cash and runoff at that scenario's rates; the difference in net interest "
                 "income, added up month by month, is set against the loss realized on day one. A sale is "
                 "priced at Keel's market value (its cash flows on today's curve) unless the trade gives a price. "
                 "Regulatory capital takes the realized loss at once; an unrealized loss held to maturity would "
                 "not, though the Capital section's market lens shows it either way.</p>")
    parts.append("<h2>The institution, with and without it</h2>")
    parts.append(whatif.tables(result["before"], result["after"]))
    return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>Trade: %s</title>
<style>%s</style></head><body><header class='top'><div class='inner'><h1>Trade: %s</h1>
<p class='sub'>An investment purchase or swap on the institution's own curve and plan.</p></div></header>
<main>%s</main></body></html>""" % (esc(name), style, esc(name), "".join(parts))
