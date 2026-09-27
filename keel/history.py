"""Run history: every report remembers what it said, so the next one can
check it.

Each `keel run` saves a snapshot to `<folder>/history/<analysis date>.json`:
the headline measures, the limits, the assumptions, and the base plan's
forecast month by month (each product's balance and interest, NII and net
income). A later run reads the snapshots before it and reports three things:

* **Trend**: the headline measures quarter by quarter, the way ALCO minutes
  track them.
* **Assumption changes**: every assumption that differs from the last run,
  old and new, so the change log an examiner asks for writes itself.
* **Back-test**: what the last run forecast for today against what the book
  actually is today: each product's balance, each administered rate, and,
  where `actuals.csv` has the months in between, net interest income.
  Rates themselves also moved, so the back-test shows how far the short
  rate differed from what the last run assumed: a miss that rates explain
  is not a miss in behaviour.

Snapshots are plain JSON, one per analysis date; re-running a date
replaces its snapshot.
"""

import datetime
import glob
import json
import os

from keel import budget, model, tables
from keel.engine import CASH

FIELDS = ("cpr", "cpr_per_100bp", "runoff", "runoff_per_100bp", "beta", "rate_floor", "spread", "growth",
          "charge_off", "haircut", "stress_runoff", "risk_weight", "servicing_cost", "fee_yield", "origination_cost",
          "new_term")
SETTINGS = ("fee_income", "operating_expense", "cash_minimum", "base_case", "tax_rate", "target_capital",
            "hurdle_rate", "stress_months", "fiscal_year_start")
FORECAST_MONTHS = 24


def _assumptions(a):
    products = {}
    for name, spec in a.products.items():
        products[name] = {f: (getattr(spec, f) if f == "new_term" or getattr(spec, f) is None
                              else round(100.0 * getattr(spec, f), 6)) for f in FIELDS}
    settings = {}
    for key in SETTINGS:
        v = getattr(a, key)
        settings[key] = round(100.0 * v, 6) if key in ("tax_rate", "target_capital", "hurdle_rate") else v
    return {"products": products, "settings": settings,
            "curve": {str(int(t)): r for t, r in zip(a.curve.tenors, a.curve.rates)},
            "limits": {x: v for x, v in a.limits.items()}}


def snapshot(r):
    a = r["assumptions"]
    run = r["base_run"][:FORECAST_MONTHS]
    labels = budget.month_labels(a.as_of, len(run))
    sides = {p.product: p.side for p in r["book"]}
    sides[CASH] = "asset"
    products = {}
    for product, side in sides.items():
        products[product] = {"side": side, "balance": [m.balances.get(product, 0.0) for m in run],
                             "interest": [m.interest.get(product, 0.0) for m in run]}
    opening = {}
    rates = {}
    for p in r["book"]:
        opening[p.product] = opening.get(p.product, 0.0) + p.balance
        rates[p.product] = rates.get(p.product, 0.0) + p.balance * p.rate
    worst_nii = max(x.value for x in r["limits"] if x.key == "nii_decline_300")
    by = {x.key: x for x in r["limits"]}
    return {
        "as_of": a.as_of, "name": r["name"], "saved": datetime.date.today().isoformat(),
        "short_today": a.curve.rate(a.short_tenor),
        "measures": {
            "nii_y1": r["nii_base"]["y1"], "nii_decline_300": worst_nii,
            "nev_decline_300": by["nev_decline_300"].value, "nev_ratio_min": by["nev_ratio_min"].value,
            "net_worth_ratio": 100.0 * r["opening"]["equity"] / r["opening"]["assets"],
            "capital_to_rwa": by["capital_to_rwa_min"].value, "liquid_to_shares": by["liquid_to_shares_min"].value,
            "supervisory_ratio": 100.0 * r["test"]["post_shock_ratio"], "assets": r["opening"]["assets"],
            "breaches": sum(1 for x in r["limits"] if x.status == "breach"),
            "nears": sum(1 for x in r["limits"] if x.status == "near")},
        "limits": {x.key: {"value": x.value, "limit": x.limit, "status": x.status} for x in r["limits"]},
        "book": {"balance": opening, "rate": {k: (rates[k] / opening[k] if opening[k] else 0.0) for k in opening}},
        "forecast": {"months": labels, "products": products, "nii": [m.nii for m in run],
                     "net_income": [m.net_income for m in run],
                     "short_rate": [_short(a, t) for t in range(1, len(run) + 1)]},
        "assumptions": _assumptions(a),
    }


def _short(a, month):
    move = a.path.move_bp(month, a.short_tenor) if a.path is not None else 0.0
    return a.curve.rate(a.short_tenor) + move / 100.0


def save(folder, snap):
    target = os.path.join(folder, "history")
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "%s.json" % snap["as_of"])
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(snap, handle, indent=1)
    return path


def load_all(folder):
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "history", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            try:
                out.append(json.load(handle))
            except ValueError as error:
                raise model.InputError("%s: %s" % (path, error))
    return sorted(out, key=lambda s: s["as_of"])


def months_between(earlier, later):
    a, b = datetime.date.fromisoformat(earlier), datetime.date.fromisoformat(later)
    return (b.year - a.year) * 12 + (b.month - a.month)


def changes(prior, current):
    """Every assumption that differs between two snapshots, as (where, old, new)."""
    out = []
    pa, ca = prior["assumptions"], current["assumptions"]
    for name in sorted(set(pa["products"]) | set(ca["products"])):
        old, new = pa["products"].get(name), ca["products"].get(name)
        if old is None or new is None:
            out.append(("product %s" % name, "absent" if old is None else "present",
                        "added" if old is None else "removed"))
            continue
        for f in FIELDS:
            if old.get(f) != new.get(f):
                out.append(("%s: %s" % (name, f), old.get(f), new.get(f)))
    for key in SETTINGS:
        if pa["settings"].get(key) != ca["settings"].get(key):
            out.append(("setting: %s" % key, pa["settings"].get(key), ca["settings"].get(key)))
    for key in sorted(set(pa["limits"]) | set(ca["limits"])):
        if pa["limits"].get(key) != ca["limits"].get(key):
            out.append(("limit: %s" % key, pa["limits"].get(key, "default"), ca["limits"].get(key, "default")))
    return out


def _actual_nii(folder, months, sides, fiscal_year_start=1):
    """{month: actual NII} from actuals.csv/.xlsx rows for those months, where every product is reported."""
    from keel import budget
    _, table_rows = budget.actual_rows(folder, sides, fiscal_year_start)
    by_month = {}
    for _, row in table_rows:
        month = (row.get("month") or "").strip()[:7]
        line = (row.get("line") or "").strip()
        if month in months and line in sides:
            amount = float(row.get("amount") or 0)
            by_month.setdefault(month, 0.0)
            by_month[month] += amount if sides[line] == "asset" else -amount
    return by_month


def backtest(prior, current, folder=None):
    """The prior run's forecast for today against today's book, or None when
    the prior run's forecast does not reach today."""
    k = months_between(prior["as_of"], current["as_of"])
    if k < 1 or k > len(prior["forecast"]["months"]):
        return None
    products = []
    for name, f in sorted(prior["forecast"]["products"].items(), key=lambda x: (x[1]["side"] != "asset", x[0])):
        forecast = f["balance"][k - 1]
        actual = current["book"]["balance"].get(name, 0.0)
        if forecast == 0 and actual == 0:
            continue
        row = {"product": name, "side": f["side"], "forecast": forecast, "actual": actual,
               "error": actual - forecast, "error_pct": (actual - forecast) / forecast if forecast else None,
               "forecast_rate": None, "actual_rate": None}
        # The forecast's own rate in month k: its interest over the average balance.
        previous = f["balance"][k - 2] if k > 1 else prior["book"]["balance"].get(name, 0.0)
        average = (previous + forecast) / 2.0
        if average and name != CASH:
            row["forecast_rate"] = 12.0 * f["interest"][k - 1] / average
            row["actual_rate"] = current["book"]["rate"].get(name)
        products.append(row)
    months = prior["forecast"]["months"][:k]
    sides = {n: f["side"] for n, f in prior["forecast"]["products"].items() if n != CASH}
    fiscal = int(current["assumptions"]["settings"].get("fiscal_year_start") or 1)
    actual = _actual_nii(folder, months, sides, fiscal) if folder else {}
    nii = [{"month": m, "forecast": prior["forecast"]["nii"][i], "actual": actual.get(m)} for i, m in enumerate(months)]
    short_forecast = prior["forecast"]["short_rate"][k - 1]
    short_actual = float(current["assumptions"]["curve"].get("1", 0.0)) if "1" in current["assumptions"]["curve"] \
        else current["forecast"]["short_rate"][0]
    return {"from": prior["as_of"], "to": current["as_of"], "months": k, "products": products, "nii": nii,
            "short_forecast": short_forecast, "short_actual": short_actual}


def review(folder, current):
    """Trend, change log and back-test for `current` against the history in
    `folder` (not counting a saved snapshot of the same date)."""
    earlier = [s for s in load_all(folder) if s["as_of"] < current["as_of"]]
    trend = earlier + [current]
    prior = earlier[-1] if earlier else None
    return {"trend": [{"as_of": s["as_of"], **s["measures"]} for s in trend],
            "prior": prior["as_of"] if prior else None,
            "changes": changes(prior, current) if prior else [],
            "backtest": backtest(prior, current, folder) if prior else None}

