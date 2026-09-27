"""Actuals straight from the general ledger's monthly trial balance.

A finance team closes the month in the general ledger, not in a Keel file.
Put the trial balance in the folder, one row per account per month, and a
map from accounts to Keel's lines, and the budget variance and the NII
back-test read their actuals from it instead of `actuals.csv`:

    trial_balance.csv              month,account,balance
                                   2026-06,1210,88100000.00       month-end, natural sign
                                   2026-07,4110,-3080000.00       income: a credit
    gl_map.csv                     account,line,measure
                                   1210,used_auto,balance         month-end balance of a product
                                   4110,used_auto,ytd             interest, year to date
                                   5300,certificates,ytd          dividends paid
                                   4400,fee_income,activity       the month's own activity

Balances carry their natural sign (debits positive, credits negative), as a
trial balance prints them, and Keel turns them around by the line: a
liability's balance and an income account's credit come out positive. Several
accounts can map to one line, and they add up. `measure` says what the
account's number is: `balance` (a month-end balance), `activity` (the month
alone) or `ytd` (year to date, which Keel differences month over month and
restarts in the settings' `fiscal_year_start` month). Accounts the map leaves
out (capital, fixed assets, clearing) are ignored.

A product's average balance for the month is the mean of its two month-ends,
so the trial balance should include the analysis date's month; a month whose
prior month is missing uses its own month-end. A year-to-date account needs
the prior month for the same reason, except in the fiscal year's first month.
"""

import os

from keel import tables
from keel.model import InputError

MEASURES = ("balance", "activity", "ytd")
#: Income-statement lines, and whether the ledger carries them as credits.
CREDIT_LINES = {"fee_income": True, "operating_expense": False, "credit_losses": False, "income_tax": False}


def present(folder):
    return bool(folder) and tables.find(folder, "trial_balance", required=False) is not None


def _month(text):
    return (text or "").strip()[:7]


def _previous(month):
    y, m = int(month[:4]), int(month[5:7])
    y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return "%04d-%02d" % (y, m)


def read_map(folder, sides):
    """{account: (line, measure)} from gl_map.csv/.xlsx; `sides` is {product: asset|liability}."""
    path = tables.find(folder, "gl_map", required=False)
    if path is None:
        raise InputError("trial_balance is in %s but gl_map (account, line, measure) is not" % folder)
    out = {}
    for n, row in enumerate(tables.read_table(path), 2):
        where = "%s line %d" % (os.path.basename(path), n)
        account = (row.get("account") or "").strip()
        line = (row.get("line") or "").strip()
        measure = (row.get("measure") or "").strip().lower()
        if not account and not line:
            continue
        if line not in sides and line not in CREDIT_LINES:
            raise InputError("%s: %r is neither a product nor one of %s" % (where, line, ", ".join(CREDIT_LINES)))
        if measure not in MEASURES:
            raise InputError("%s: measure must be one of %s, not %r" % (where, ", ".join(MEASURES), measure))
        if measure == "balance" and line not in sides:
            raise InputError("%s: %s is an income-statement line and has no balance" % (where, line))
        if account in out:
            raise InputError("%s: account %s is mapped twice" % (where, account))
        out[account] = (line, measure)
    return out


def read(folder, sides, fiscal_year_start=1):
    """The trial balance as actuals rows: [{month, line, average_balance, amount}]
    for every month it can say something about, sorted by month."""
    accounts = read_map(folder, sides)
    path = tables.find(folder, "trial_balance")
    name = os.path.basename(path)
    raw = {}                        # (month, account) -> balance
    for n, row in enumerate(tables.read_table(path), 2):
        month, account = _month(row.get("month")), (row.get("account") or "").strip()
        if not month and not account:
            continue
        if len(month) != 7 or month[4] != "-":
            raise InputError("%s line %d: month must be YYYY-MM, not %r" % (name, n, row.get("month")))
        if account not in accounts:
            continue
        try:
            value = float(row.get("balance") or 0)
        except ValueError:
            raise InputError("%s line %d: balance must be a number" % (name, n))
        if (month, account) in raw:
            raise InputError("%s line %d: account %s appears twice in %s" % (name, n, account, month))
        raw[(month, account)] = value
    months = sorted({m for m, _ in raw})
    balances, amounts = {}, {}      # (month, line) -> value in Keel's sign
    for month in months:
        for account, (line, measure) in accounts.items():
            if (month, account) not in raw:
                continue
            value = raw[(month, account)]
            credit = sides.get(line) == "liability" if measure == "balance" else (
                sides.get(line) == "asset" or CREDIT_LINES.get(line, False))
            value = -value if credit else value
            if measure == "balance":
                balances[(month, line)] = balances.get((month, line), 0.0) + value
                continue
            if measure == "ytd" and int(month[5:7]) != fiscal_year_start:
                before = (_previous(month), account)
                if before not in raw:
                    continue        # no prior year-to-date: this month cannot be read
                prior = raw[before]
                value -= -prior if credit else prior
            amounts[(month, line)] = amounts.get((month, line), 0.0) + value
    rows = []
    for (month, line), amount in sorted(amounts.items()):
        average = None
        if line in sides and (month, line) in balances:
            end = balances[(month, line)]
            start = balances.get((_previous(month), line), end)
            average = (start + end) / 2.0
        rows.append({"month": month, "line": line, "average_balance": average, "amount": amount})
    return rows


def month_ends(folder, sides, month):
    """{product: month-end balance} for one month of the trial balance, for the tie-out."""
    accounts = read_map(folder, sides)
    out = {}
    for row in tables.read_table(tables.find(folder, "trial_balance")):
        account = (row.get("account") or "").strip()
        if _month(row.get("month")) != month or accounts.get(account, (None, None))[1] != "balance":
            continue
        line = accounts[account][0]
        value = float(row.get("balance") or 0)
        out[line] = out.get(line, 0.0) + (-value if sides[line] == "liability" else value)
    return out
