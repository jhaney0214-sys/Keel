"""New-product spread analysis: what a proposed product earns, and what it
does to the institution.

    python -m keel newproduct <folder> <proposal.json>

A proposal describes the product and its launch:

    {
      "name": "72-month green auto loan",
      "product": "green_auto",          the new product's key
      "like": "new_auto",               copy behaviour from this product; override below
      "side": "asset",
      "rate": 5.49,                     percent; or "spread" over the curve at the term
      "term_months": 72,
      "amortization": "level",
      "launch_balance": 10000000,       originated on the analysis date, funded through cash
      "growth": 40,                     percent a year after launch
      "average_size": 32000,
      "upfront_fee": 0,                 percent of the amount
      "behaviour": {"cpr": 16, "charge_off": 0.45, "servicing_cost": 0.55,
                    "origination_cost": 1.0, "risk_weight": 75}
    }

It answers three questions. **Does one loan pay?** The pricing calculator's
life economics at the proposed rate, with the hurdle and break-even rates.
**What does the book earn on it?** The product's own path through the plan,
year by year: balance, interest, FTP, spread and contribution after its
costs. **What does it do to the institution?** The whole book run with and
without it through the same measures: NII and NEV in every scenario, the
liquidity stress and every policy limit, side by side.
"""

import copy
import html

from keel import measures, model, pricing, whatif
from keel.model import InputError


def _pct(v):
    return None if v is None else float(v) / 100.0


def build(positions, raw, proposal):
    """(changed positions, changed assumptions, notes, deal) with the product launched."""
    key = proposal["product"]
    raw = copy.deepcopy(raw)
    if key in raw["products"]:
        raise InputError("new product: %r is already a product; give the proposal a new key" % key)
    like = proposal.get("like")
    if like and like not in raw["products"]:
        raise InputError("new product: like %r is not a product" % like)
    spec = dict(raw["products"].get(like, {})) if like else {}
    spec.update(proposal.get("behaviour", {}))
    a = model.parse_assumptions(raw)
    term = int(proposal.get("term_months", 0))
    if "rate" in proposal:
        rate = float(proposal["rate"])
        spread = rate - a.curve.rate(term or 1)
    elif "spread" in proposal:
        spread = float(proposal["spread"])
        rate = a.curve.rate(term or 1) + spread
    else:
        raise InputError("new product: give a rate or a spread")
    amortization = proposal.get("amortization", "level" if term else "nonmaturity")
    # New business after launch is priced at the same spread to the curve.
    spec.update({"new_term": term, "new_amortization": amortization if amortization != "nonmaturity" else "",
                 "spread": spread, "growth": float(proposal.get("growth", 0.0))})
    raw["products"][key] = spec
    side = proposal.get("side", "asset")
    action = {"add": {"id": key + "_launch", "name": proposal.get("name", key), "product": key, "side": side,
                      "balance": float(proposal.get("launch_balance", 0)), "rate": rate,
                      "rate_type": "administered" if amortization == "nonmaturity" else "fixed",
                      "term_months": term, "amortization": amortization,
                      "amort_months": int(proposal.get("amort_months", 0))}}
    if action["add"]["balance"] <= 0:
        raise InputError("new product: launch_balance must be positive")
    changed, changed_a, notes = whatif.apply(positions, raw, {"actions": [action]})
    notes.insert(0, "new product %s: %s at %.2f%% (%+.2f%% over the %d-month curve), growing %s%% a year" % (
        key, side, rate, spread, term or 1, proposal.get("growth", 0)))
    deal = pricing.Deal(product=key, amount=float(proposal.get("average_size", 10000)), term_months=term,
                        rate=rate / 100.0, side=side, amortization=amortization,
                        amort_months=int(proposal.get("amort_months", 0)),
                        upfront_fee=_pct(proposal.get("upfront_fee", 0)))
    return changed, changed_a, notes, deal


def path(run, product, a, ftp_rate, spec, years, opening, average_life, side="asset"):
    """The product's own year-by-year economics in the plan. Origination
    cost is spread over the deal's average life, as the unit economics do."""
    out = []
    previous = opening
    for y in range(1, years + 1):
        months = measures.year(run, y)
        ends = [m.balances.get(product, 0.0) for m in months]
        starts = [previous] + ends[:-1]
        average = sum((s + e) / 2.0 for s, e in zip(starts, ends)) / 12.0
        previous = ends[-1]
        interest = sum(m.interest.get(product, 0.0) for m in months)
        ftp = average * ftp_rate
        spread = interest - ftp if side == "asset" else ftp - interest
        costs = average * (spec.servicing_cost + (spec.charge_off if side == "asset" else 0.0) - spec.fee_yield
                           + (spec.origination_cost / average_life if average_life else 0.0))
        capital = (average * (spec.risk_weight if spec.risk_weight is not None else 1.0) * a.target_capital
                   if side == "asset" else 0.0)
        pre_tax = spread + capital * ftp_rate - costs
        net = pre_tax * (1.0 - a.tax_rate)
        out.append({"year": y, "average": average, "end": ends[-1], "interest": interest, "ftp": ftp,
                    "spread": spread, "costs": costs, "capital": capital, "net": net,
                    "roa": net / average if average else 0.0,
                    "raroc": net / capital if capital > 0 else None})
    return out


def analyse(positions, a, raw, proposal):
    from keel import engine
    changed, changed_a, notes, deal = build(positions, raw, proposal)
    quote = pricing.quote(deal, changed_a)
    before = whatif.key_measures(positions, a)
    after = whatif.key_measures(changed, changed_a)
    run = engine.going_concern(changed, changed_a, changed_a.scenarios[0])
    spec = changed_a.products[proposal["product"]]
    opening = sum(x.balance for x in changed if x.product == proposal["product"])
    lives = path(run, proposal["product"], changed_a, quote["at"]["ftp"], spec, changed_a.horizon_months // 12,
                 opening, quote["at"]["average_life"], proposal.get("side", "asset"))
    return {"proposal": proposal, "notes": notes, "quote": quote, "before": before, "after": after, "path": lives}


def page(result, style):
    from keel import report
    p, q = result["proposal"], result["quote"]
    e = q["at"]
    k, pct = report.k, report.pct
    rows = [[html.escape(name), pct(e[key])] for name, key, _ in pricing.LINES]
    rows.append(["RAROC", "n/a" if e["raroc"] is None else pct(e["raroc"], 1)])
    status = ""
    if e["raroc"] is not None:
        status = report.chip("within" if e["raroc"] >= q["hurdle"] else "near" if e["raroc"] >= 0 else "breach",
                             "Clears the %.0f%% hurdle" % (100 * q["hurdle"]) if e["raroc"] >= q["hurdle"]
                             else "Below the hurdle")
    if e["side"] == "asset":
        unit = ("<p>At %s the product earns a spread of <b>%s</b> over its funding and a RAROC of <b>%s</b> %s. "
                "It needs <b>%s</b> to earn the hurdle, and breaks even at <b>%s</b>. Average life %.1f years.</p>" % (
                    pct(e["rate"]), pct(e["spread"]), "n/a" if e["raroc"] is None else pct(e["raroc"], 1), status,
                    "n/a" if q["hurdle_rate"] is None else pct(q["hurdle_rate"]),
                    "n/a" if q["breakeven_rate"] is None else pct(q["breakeven_rate"]), e["average_life"]))
    else:
        covers = e["pre_tax"] >= 0
        unit = ("<p>At %s the product's funding is worth <b>%s</b> a year over what it pays, and after its costs "
                "it %s <b>%s</b> %s. The highest rate that still covers its costs is <b>%s</b>. Average life %.1f "
                "years. Money raised above that rate costs more than wholesale funding of the same term.</p>" % (
                    pct(e["rate"]), pct(e["spread"]), "earns" if covers else "loses", pct(abs(e["pre_tax"])),
                    report.chip("within" if covers else "breach", "Covers its costs" if covers else "Costs more "
                                "than it earns"),
                    "n/a" if q["breakeven_rate"] is None else pct(q["breakeven_rate"]), e["average_life"]))
    life = report.table(["Year", "Average balance", "Interest", "FTP", "Spread", "Costs net of fees", "Net income",
                         "ROA", "RAROC"],
                        [[str(r["year"]), k(r["average"]), k(r["interest"]), k(r["ftp"]), k(r["spread"]),
                          k(r["costs"]), k(r["net"]), pct(r["roa"]), "n/a" if r["raroc"] is None else pct(r["raroc"], 1)]
                         for r in result["path"]])
    return """<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'><title>New product: %s</title>
<style>%s</style></head><body><header class='top'><div class='inner'><h1>New product: %s</h1>
<p class='sub'>Spread analysis on the institution's own curve, behaviour and costs, and the whole book run with and
without it.</p></div></header><main>
<h2>One %s</h2>%s<div class='charts'><div>%s</div><div>%s</div></div>
<h2>In the plan ($000)</h2>%s
<p class='muted'>The product's balance and interest are read from the plan; its funding is charged at the launch
deal's FTP, and costs, losses and capital at its own rates, with origination cost spread over the average life. The institution runs below carry its interest only:
servicing and origination costs are in this table, not in the plan's operating expense.</p>
<h2>The institution, with and without it</h2><ul>%s</ul>%s</main></body></html>""" % (
        html.escape(p.get("name", p["product"])), style, html.escape(p.get("name", p["product"])),
        "loan" if e["side"] == "asset" else "account", unit,
        report.table(["Per dollar of average balance", "Annual"], rows),
        report.table(["Rate", "Spread", "RAROC"], [[pct(b["rate"]), pct(b["spread"]),
                                                   "n/a" if b["raroc"] is None else pct(b["raroc"], 1)]
                                                  for b in q["band"]], numeric_from=0),
        life, "".join("<li>%s</li>" % html.escape(n) for n in result["notes"]),
        whatif.tables(result["before"], result["after"]))
