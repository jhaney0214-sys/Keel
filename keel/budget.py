"""The budget, month by month, and how the actuals compare.

The budget is the first year of the base plan, read at the grain a budget
is kept: each product's month-end and average balance, its interest, and
its yield; and the income statement by month. It comes from the same
projection as the rate-risk and liquidity numbers, so the budget ALCO
approves is the one it stress-tests.

Put `actuals.csv` (or .xlsx) in the folder, or the general ledger's monthly
trial balance with a map of its accounts (see keel/ledger.py), and the
report compares:

    month,line,average_balance,amount
    2026-07,used_auto,88500000,512000          interest on a product
    2026-07,certificates,145100000,519000      interest paid on a liability
    2026-07,fee_income,,525000                 or a line of the income statement
    2026-07,operating_expense,,1690000

For each product the variance in net interest income splits into **volume**
(the balance differed: the budget's yield on the difference) and **rate**
(the rest: the yield differed on the actual balance). Liabilities count
against NII, so paying more is a negative variance.
"""

import datetime
import os

from keel import measures, tables, xlsx
from keel.engine import CASH
from keel.model import InputError

LINES = ("fee_income", "operating_expense", "credit_losses", "income_tax")


def month_labels(as_of, count):
    d = datetime.date.fromisoformat(as_of)
    out = []
    y, m = d.year, d.month
    for _ in range(count):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        out.append("%04d-%02d" % (y, m))
    return out


def build(positions, a, run, months=12):
    """The budget: {labels, products: [{product, side, end, average, interest, yield}], income: [...]}."""
    months = min(months, len(run))
    labels = month_labels(a.as_of, months)
    sides = {p.product: p.side for p in positions}
    sides[CASH] = "asset"
    opening = {}
    for p in positions:
        opening[p.product] = opening.get(p.product, 0.0) + p.balance
    products = []
    for product in sorted(sides, key=lambda k: (sides[k] != "asset", -opening.get(k, 0.0))):
        ends = [run[t].balances.get(product, 0.0) for t in range(months)]
        starts = [opening.get(product, 0.0)] + ends[:-1]
        average = [(s + e) / 2.0 for s, e in zip(starts, ends)]
        interest = [run[t].interest.get(product, 0.0) for t in range(months)]
        products.append({"product": product, "side": sides[product], "end": ends, "average": average,
                         "interest": interest,
                         "yield": [12 * i / b if b else 0.0 for i, b in zip(interest, average)]})
    overnight = [run[t].overnight_interest for t in range(months)]
    income = []
    for t in range(months):
        m = run[t]
        income.append({"month": labels[t], "interest_income": m.interest_income,
                       "interest_expense": m.interest_expense, "nii": m.nii, "fee_income": m.fee_income,
                       "operating_expense": m.operating_expense, "credit_losses": m.credit_losses,
                       "income_tax": m.income_tax, "net_income": m.net_income})
    lines = [{"line": x.line, "kind": x.kind, "months": [x.month(t + 1) for t in range(months)]}
             for x in a.noninterest]
    drivers = [{"product": product, "month": labels[m - 1] if m <= months else "month %d" % m, "plan_month": m,
                "volume": d.get("volume"), "balance": d.get("balance"),
                "rate": None if d.get("rate") is None else d["rate"]}
               for product, plan in sorted(a.drivers.items()) for m, d in sorted(plan.items())]
    return {"labels": labels, "products": products, "income": income, "overnight_interest": overnight,
            "lines": lines, "drivers": drivers}


def actual_rows(folder, sides, fiscal_year_start=1):
    """(source file name, [(where, row)]) from actuals.csv/.xlsx or the trial
    balance, or (None, []). Both at once is refused rather than guessed between."""
    from keel import ledger
    path = tables.find(folder, "actuals", required=False) if folder else None
    if ledger.present(folder):
        if path:
            raise InputError("both %s and a trial_balance are in %s; keep one source of actuals" % (
                os.path.basename(path), folder))
        rows = ledger.read(folder, sides, fiscal_year_start)
        return "trial_balance", [("trial balance, %s %s" % (r["month"], r["line"]), r) for r in rows]
    if path is None:
        return None, []
    return os.path.basename(path), [("%s line %d" % (os.path.basename(path), n), row)
                                    for n, row in enumerate(tables.read_table(path), 2)]


def read_actuals(folder, labels, products, fiscal_year_start=1):
    """{(month, line): (average_balance or None, amount)} from actuals.csv/.xlsx
    or the trial balance, or None. `products` is {product: side} (or a list of
    products, when there is no trial balance to sign)."""
    sides = products if isinstance(products, dict) else {p: "asset" for p in products}
    source, table_rows = actual_rows(folder, sides, fiscal_year_start)
    if source is None:
        return None
    out = {}
    known = set(products) | set(LINES)
    for where, row in table_rows:
        month = str(row.get("month") or "").strip()
        if month.replace(".", "").isdigit() and float(month) > 20000:
            month = xlsx.excel_date(float(month))      # Excel turned "2026-07" into a date
        month = month[:7]
        line = (row.get("line") or "").strip()
        if not month and not line:
            continue
        if len(month) == 7 and month < labels[0]:
            continue            # an earlier period's actuals: the back-test reads those
        if month not in labels:
            raise InputError("%s: month %r is not in the budget year (%s to %s)" % (where, month, labels[0],
                                                                                 labels[-1]))
        if line not in known:
            raise InputError("%s: %r is neither a product nor one of %s" % (where, line, ", ".join(LINES)))
        try:
            amount = float(row.get("amount") or 0)
            balance = row.get("average_balance")
            balance = None if balance is None else str(balance)
            balance = float(balance) if balance not in (None, "") else None
        except ValueError:
            raise InputError("%s: amount and average_balance must be numbers" % where)
        if (month, line) in out:
            raise InputError("%s: %s %s appears twice" % (where, month, line))
        out[(month, line)] = (balance, amount)
    return out if source != "trial_balance" else _Sourced(out, source)


class _Sourced(dict):
    """Actuals that remember where they came from."""

    def __init__(self, rows, source):
        dict.__init__(self, rows)
        self.source = source


def variance(budget, actuals):
    """Year-to-date variance over the months the actuals cover."""
    labels = budget["labels"]
    covered = [i for i, m in enumerate(labels) if any(k[0] == m for k in actuals)]
    if not covered:
        return None
    n = covered[-1] + 1        # through the latest month reported
    months = labels[:n]
    rows = []
    for p in budget["products"]:
        name = p["product"]
        reported = [actuals.get((m, name)) for m in months]
        if not any(reported):
            continue
        b_bal = sum(p["average"][:n]) / n
        b_int = sum(p["interest"][:n])
        a_bal = sum((r[0] if r and r[0] is not None else p["average"][i]) for i, r in enumerate(reported)) / n
        a_int = sum((r[1] if r else p["interest"][i]) for i, r in enumerate(reported))
        budget_yield = b_int / b_bal if b_bal else 0.0          # per the period, not annualized
        volume = (a_bal - b_bal) * budget_yield
        rate = (a_int - b_int) - volume
        sign = 1.0 if p["side"] == "asset" else -1.0
        rows.append({"product": name, "side": p["side"], "budget_balance": b_bal, "actual_balance": a_bal,
                     "budget_interest": b_int, "actual_interest": a_int,
                     "budget_yield": 12.0 * b_int / n / b_bal if b_bal else 0.0,
                     "actual_yield": 12.0 * a_int / n / a_bal if a_bal else 0.0,
                     "volume": sign * volume, "rate": sign * rate, "nii_variance": sign * (a_int - b_int)})
    income = budget["income"][:n]
    b = {k: sum(m[k] for m in income) for k in ("nii", "fee_income", "operating_expense", "credit_losses",
                                               "income_tax", "net_income")}
    # Actual NII: the budget's, moved by every product variance reported.
    act = {"nii": b["nii"] + sum(r["nii_variance"] for r in rows)}
    for line in LINES:
        act[line] = sum(actuals[(m, line)][1] if (m, line) in actuals else sum(
            x[line] for x in income if x["month"] == m) for m in months)
    act["net_income"] = act["nii"] + act["fee_income"] - act["operating_expense"] - act["credit_losses"] \
        - act["income_tax"]
    statement = []
    for key, label, good in (("nii", "Net interest income", 1), ("fee_income", "Fee and other income", 1),
                             ("operating_expense", "Operating expense", -1), ("credit_losses", "Credit losses", -1),
                             ("income_tax", "Income tax", -1), ("net_income", "Net income", 1)):
        statement.append({"line": label, "budget": b[key], "actual": act[key],
                          "variance": good * (act[key] - b[key])})
    return {"through": months[-1], "months": n, "products": rows, "statement": statement,
            "source": getattr(actuals, "source", "actuals")}


def check_budget(budget, run):
    """The budget's product interest is the plan's NII: the same projection, read another way."""
    by_product = sum((sum(p["interest"]) if p["side"] == "asset" else -sum(p["interest"])) for p in budget["products"])
    by_product -= sum(budget["overnight_interest"])
    plan = sum(m.nii for m in run[:len(budget["labels"])])
    return measures.Check("The budget's product interest adds up to the plan's net interest income",
                          abs(by_product - plan) < 1.0,
                          "budget $%s, plan $%s" % ("{:,.0f}".format(by_product), "{:,.0f}".format(plan)))
