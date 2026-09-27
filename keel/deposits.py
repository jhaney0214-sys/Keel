"""The deposit study: share betas and decay estimated from the institution's
own history, set against the assumptions the model runs on.

Non-maturity share assumptions (how far a share rate follows the market, and
how fast balances run off) drive NEV more than anything else in a credit
union's model, and examiners ask what supports them. This reads two optional
files in the folder:

    deposit_history.csv    month, product, balance, rate, market_rate
        One row per product per month (rates in percent). market_rate is the
        short market rate that month (Fed funds, or the 1-month Treasury).
    deposit_accounts.csv   month, account_id, product, balance
        Account-level balances by month. Optional; it is what decay needs.

and estimates, per product:

* **Beta and lag.** The share rate regressed on the market rate lagged 0 to
  6 months; the lag with the best fit is reported with its beta and R².
* **Up and down betas.** The change in the share rate over the market's
  rising phase (trough to peak) and its falling phase (peak to the end),
  each over the change in the market rate: pricing is often asymmetric.
* **Rate sensitivity of balances.** Monthly balance growth regressed on the
  spread of the market rate over the share rate: how much a 100bp wider
  spread pulls money out, the counterpart of `runoff_per_100bp`.
* **Core balance.** The lowest trailing-12-month balance over the average:
  the part of the balance that has not moved with rates.
* **Decay.** From accounts: the balance still held, month by month, by the
  accounts open in the first month, fitted to (1 - d)^(t/12). Aggregate
  balances cannot give decay, because new money hides runoff; the study says
  so rather than guessing.

It recommends values; it changes nothing. The assumptions stay the ones in the
settings until someone decides to adopt the study's.
"""

import math
import os

from keel import tables
from keel.model import InputError

MAX_LAG = 6


def _num(value, where):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        raise InputError("%s: %r is not a number" % (where, value))


def read(folder):
    """(history {product: [(month, balance, rate, market)]}, accounts or None), or (None, None)."""
    path = tables.find(folder, "deposit_history", required=False)
    if path is None:
        return None, None
    history = {}
    for n, row in enumerate(tables.read_table(path), 2):
        where = "%s line %d" % (os.path.basename(path), n)
        month = (row.get("month") or "").strip()[:7]
        product = (row.get("product") or "").strip()
        if not month or not product:
            continue
        history.setdefault(product, []).append((month, _num(row.get("balance"), where),
                                                _num(row.get("rate"), where) / 100.0,
                                                _num(row.get("market_rate"), where) / 100.0))
    for product in history:
        history[product].sort()
    accounts = None
    apath = tables.find(folder, "deposit_accounts", required=False)
    if apath is not None:
        accounts = {}
        for n, row in enumerate(tables.read_table(apath), 2):
            product = (row.get("product") or "").strip()
            month = (row.get("month") or "").strip()[:7]
            if not product or not month:
                continue
            accounts.setdefault(product, {}).setdefault(month, {})[row.get("account_id", "").strip()] = \
                _num(row.get("balance"), "%s line %d" % (os.path.basename(apath), n))
    return history, accounts


def _ols(xs, ys):
    """(slope, intercept, r²) of y on x."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    resid = sum((y - intercept - slope * x) ** 2 for x, y in zip(xs, ys))
    return slope, intercept, (1.0 - resid / syy) if syy else 0.0


def betas(rows):
    """Best-fitting lag, its beta and R², and the up and down betas."""
    rates = [r for _, _, r, _ in rows]
    market = [m for _, _, _, m in rows]
    best = None
    for lag in range(0, MAX_LAG + 1):
        if len(rows) - lag < 12:
            break
        slope, _, r2 = _ols(market[:len(market) - lag], rates[lag:])
        if best is None or r2 > best[2]:
            best = (lag, slope, r2)
    lag = best[0] if best else 0
    # Phases of the market: trough before its peak, then the peak to the end.
    peak = max(range(len(market)), key=lambda i: market[i])
    trough = min(range(0, peak + 1), key=lambda i: market[i])
    up = down = None
    if market[peak] - market[trough] > 0.005 and peak + lag < len(rows):
        up = (rates[min(peak + lag, len(rates) - 1)] - rates[min(trough + lag, len(rates) - 1)]) / \
             (market[peak] - market[trough])
    if market[peak] - market[-1] > 0.005:
        end = len(rows) - 1
        start = min(peak + lag, end)
        span = market[peak] - market[max(end - lag, peak)]
        if span > 0.005 and end > start:
            down = (rates[start] - rates[end]) / span
    return {"lag": lag, "beta": best[1] if best else None, "r2": best[2] if best else None,
            "up_beta": up, "down_beta": down}


def balance_sensitivity(rows):
    """Annual balance runoff per 100bp of market-over-share spread, and core share."""
    growth, spread = [], []
    for (_, b0, _, _), (_, b1, r1, m1) in zip(rows, rows[1:]):
        if b0 > 0 and b1 > 0:
            growth.append(math.log(b1 / b0))
            spread.append((m1 - r1) * 100.0)          # in percentage points
    if len(growth) < 12:
        return None, None
    slope, _, r2 = _ols(spread, growth)
    per_100bp = -slope * 12.0                          # annual runoff per 100bp wider spread
    balances = [b for _, b, _, _ in rows]
    trailing = [min(balances[max(0, i - 11):i + 1]) for i in range(len(balances))]
    core = min(trailing[11:]) / (sum(balances) / len(balances)) if len(balances) >= 12 else None
    return {"runoff_per_100bp": per_100bp, "r2": r2}, core


def decay(months):
    """Annual decay of the first month's accounts, from account balances, and account attrition."""
    labels = sorted(months)
    if len(labels) < 6:
        return None
    cohort = months[labels[0]]
    start = sum(v for v in cohort.values() if v > 0)
    if start <= 0:
        return None
    xs, ys, counts = [], [], []
    for t, label in enumerate(labels[1:], 1):
        held = months[label]
        kept = sum(max(held.get(a, 0.0), 0.0) for a in cohort)
        if kept <= 0:
            break
        xs.append(t)
        ys.append(math.log(kept / start))
        counts.append(sum(1 for a in cohort if held.get(a, 0.0) > 0) / float(len(cohort)))
    if not xs:
        return None
    slope = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)   # through the origin
    annual = 1.0 - math.exp(12.0 * slope)
    attrition = 1.0 - counts[-1] ** (12.0 / xs[-1]) if counts[-1] > 0 else None
    return {"decay": annual, "account_attrition": attrition, "months": xs[-1], "accounts": len(cohort),
            "average_life_years": (1.0 / annual) if annual > 0 else None}


def study(folder, a):
    """The study for every product with history, or None when there is none."""
    history, accounts = read(folder)
    if not history:
        return None
    out = []
    for product in sorted(history):
        rows = history[product]
        if len(rows) < 13:
            continue
        spec = a.products.get(product)
        b = betas(rows)
        sens, core = balance_sensitivity(rows)
        d = decay(accounts[product]) if accounts and product in accounts else None
        row = {"product": product, "months": len(rows), "from": rows[0][0], "to": rows[-1][0],
               "beta": b, "sensitivity": sens, "core": core, "decay": d,
               "assumed": None if spec is None else {"beta": spec.beta, "runoff": spec.runoff,
                                                      "runoff_per_100bp": spec.runoff_per_100bp},
               "flags": []}
        if spec is not None:
            if b["beta"] is not None and abs(b["beta"] - spec.beta) >= 0.10:
                row["flags"].append("beta assumed %.0f%%, estimated %.0f%%" % (100 * spec.beta, 100 * b["beta"]))
            if d and abs(d["decay"] - spec.runoff) >= 0.03:
                row["flags"].append("decay assumed %.0f%%, estimated %.0f%%" % (100 * spec.runoff, 100 * d["decay"]))
            if sens and abs(sens["runoff_per_100bp"] - spec.runoff_per_100bp) >= 0.02 and sens["r2"] >= 0.2:
                row["flags"].append("runoff per 100bp assumed %.1f%%, estimated %.1f%%" % (
                    100 * spec.runoff_per_100bp, 100 * sens["runoff_per_100bp"]))
        out.append(row)
    return {"products": out, "has_accounts": accounts is not None}


def recommended(result):
    """The Products-sheet values the study supports, in percent, for products it could estimate."""
    rec = {}
    for row in result["products"]:
        values = {}
        if row["beta"]["beta"] is not None and (row["beta"]["r2"] or 0) >= 0.5:
            values["beta"] = round(100 * row["beta"]["beta"], 1)
        if row["decay"]:
            values["runoff"] = round(100 * row["decay"]["decay"], 1)
        if row["sensitivity"] and row["sensitivity"]["r2"] >= 0.2:
            values["runoff_per_100bp"] = round(100 * max(row["sensitivity"]["runoff_per_100bp"], 0.0), 1)
        if values:
            rec[row["product"]] = values
    return rec
