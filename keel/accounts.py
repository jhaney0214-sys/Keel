"""Profitability by account, member and branch, from the core's detail rows.

The product view says auto loans earn their capital; it cannot say which
members carry the credit union and which it carries. This reads the same
P&L one row at a time: every loan and certificate in the core files, and
each member's share balances from `member_shares.csv`, priced exactly as
their product is (keel/profitability.py):

    spread         interest less the FTP charge (a loan), or the FTP credit
                   less the dividend (a deposit), at the rate of the pool the
                   row joined, so the rows add up to the product
    + capital credit, fees (fee_yield)
    - servicing (servicing_cost, a share of balance)
    - account cost (the product's `account_cost`, dollars a year per account)
    - expected loss (charge_off)
    = contribution, before tax, then after it

`account_cost` is the one piece the product view does not carry: a fixed
cost per account, which is what makes a $300 checking account lose money
whatever its rate. It is an allocation of the operating expense the products
do not carry, and the report says how much of that it takes. Contribution is
before the rest of overhead: a member with a positive contribution helps pay
for the branches, not only for themselves.

Rows with a `member_id` roll up to members, who are then ranked: the whale
curve (cumulative contribution against the share of members, most profitable
first) shows how few members earn the whole of it. Members with a `branch`
roll up to branches. A member's branch is the branch most of their balance
sits in.
"""

from keel import profitability

KIND_LABEL = {"loan": "Loans", "certificate": "Certificates", "share": "Shares"}


def _row(x, spec, ftp_rate, a):
    balance, rate = x["balance"], x["rate"]
    side = "liability" if x["kind"] in ("certificate", "share") else "asset"
    interest = balance * rate
    ftp = balance * (ftp_rate or 0.0)
    spread = interest - ftp if side == "asset" else ftp - interest
    capital = credit = expected = 0.0
    if side == "asset":
        capital = max(balance, 0.0) * profitability.risk_weight(x["product"], spec, side) * a.target_capital
        credit = capital * (ftp_rate or 0.0)
        expected = max(balance, 0.0) * spec.charge_off
    fees = balance * spec.fee_yield
    servicing = balance * spec.servicing_cost
    pre_tax = spread + credit + fees - servicing - spec.account_cost - expected
    return {"id": x["id"], "kind": x["kind"], "product": x["product"], "member_id": x["member_id"],
            "branch": x["branch"], "balance": balance, "rate": rate, "ftp_rate": ftp_rate or 0.0,
            "interest": interest, "ftp": ftp, "spread": spread, "capital_credit": credit, "fees": fees,
            "servicing": servicing, "account_cost": spec.account_cost, "expected_loss": expected,
            "capital": capital, "pre_tax": pre_tax, "net": pre_tax * (1.0 - a.tax_rate),
            "days_delinquent": x.get("days_delinquent", 0)}


def _sum(rows, *fields):
    return {f: sum(r[f] for r in rows) for f in fields}


def run(positions, a, detail, lines=None, totals=None):
    """Account rows and their roll-ups, or None when there is no detail."""
    if not detail:
        return None
    ftp = profitability.ftp_rates(positions, a)
    rows = [_row(x, a.products[x["product"]], ftp.get(x["position"]), a) for x in detail]

    # ---- by product: the account rows against the product view
    by_product = {}
    for r in rows:
        p = by_product.setdefault(r["product"], {"product": r["product"], "kind": r["kind"], "accounts": 0,
                                                 "balance": 0.0, "interest": 0.0, "ftp": 0.0, "net": 0.0,
                                                 "account_cost": 0.0, "losing": 0})
        p["accounts"] += 1
        for f in ("balance", "interest", "ftp", "net", "account_cost"):
            p[f] += r[f]
        p["losing"] += r["net"] < 0
    products = sorted(by_product.values(), key=lambda p: (p["kind"], -p["balance"]))
    for p in products:
        p["average_net"] = p["net"] / p["accounts"]
        p["losing_share"] = p["losing"] / float(p["accounts"])
        p["breakeven_balance"] = _breakeven(a.products[p["product"]], p, a)

    # ---- members
    members = {}
    for r in rows:
        if not r["member_id"]:
            continue
        m = members.setdefault(r["member_id"], {"member_id": r["member_id"], "loans": 0.0, "deposits": 0.0,
                                                "accounts": 0, "net": 0.0, "branches": {}, "products": set(),
                                                "delinquent": False})
        m["accounts"] += 1
        m["net"] += r["net"]
        m["loans" if r["kind"] == "loan" else "deposits"] += r["balance"]
        m["products"].add(r["product"])
        if r["branch"]:
            m["branches"][r["branch"]] = m["branches"].get(r["branch"], 0.0) + abs(r["balance"])
        if r["days_delinquent"] >= 60:
            m["delinquent"] = True
    member_rows = []
    for m in members.values():
        m["branch"] = max(m["branches"], key=m["branches"].get) if m["branches"] else ""
        m["relationship"] = ("borrower and saver" if m["loans"] and m["deposits"] else
                             "borrower only" if m["loans"] else "saver only")
        m["products"] = len(m["products"])
        del m["branches"]
        member_rows.append(m)
    member_rows.sort(key=lambda m: -m["net"])
    total = sum(m["net"] for m in member_rows)
    return {"rows": rows, "products": products, "members": member_rows,
            "whale": _whale(member_rows, total), "deciles": _deciles(member_rows),
            "relationships": _group(member_rows, "relationship"), "branches": _group(member_rows, "branch"),
            "totals": dict(_sum(rows, "balance", "net", "account_cost", "pre_tax"), accounts=len(rows),
                           members=len(member_rows), member_net=total,
                           losing_members=sum(1 for m in member_rows if m["net"] < 0),
                           unassigned=sum(1 for r in rows if not r["member_id"])),
            "unallocated_expense": None if totals is None else totals["unallocated_expense"],
            "check": check(rows, lines) if lines is not None else None}


def _breakeven(spec, p, a):
    """The balance at which the product's average account covers its account
    cost, at the product's own margin per dollar: None when it has no fixed
    cost or loses money at any size."""
    if not spec.account_cost or not p["balance"]:
        return None
    per_dollar = (p["net"] / (1.0 - a.tax_rate) + p["account_cost"]) / p["balance"]
    return spec.account_cost / per_dollar if per_dollar > 0 else None


def _whale(members, total):
    """[(share of members, cumulative contribution as a share of the total)],
    most profitable first; 101 points."""
    n = len(members)
    if not n or not total:
        return []
    cumulative, out, running = [], [], 0.0
    for m in members:
        running += m["net"]
        cumulative.append(running)
    for step in range(101):
        i = min(n, int(round(step / 100.0 * n)))
        out.append((step / 100.0, (cumulative[i - 1] if i else 0.0) / total))
    return out


def _deciles(members):
    n = len(members)
    out = []
    for d in range(10):
        group = members[d * n // 10:(d + 1) * n // 10]
        if not group:
            continue
        out.append({"decile": d + 1, "members": len(group), "net": sum(m["net"] for m in group),
                    "loans": sum(m["loans"] for m in group), "deposits": sum(m["deposits"] for m in group),
                    "average_net": sum(m["net"] for m in group) / len(group),
                    "products": sum(m["products"] for m in group) / float(len(group))})
    return out


def _group(members, key):
    out = {}
    for m in members:
        g = out.setdefault(m[key] or "(none)", {key: m[key] or "(none)", "members": 0, "loans": 0.0,
                                                "deposits": 0.0, "net": 0.0, "losing": 0})
        g["members"] += 1
        g["loans"] += m["loans"]
        g["deposits"] += m["deposits"]
        g["net"] += m["net"]
        g["losing"] += m["net"] < 0
    rows = sorted(out.values(), key=lambda g: -g["net"])
    for g in rows:
        g["average_net"] = g["net"] / g["members"]
        g["losing_share"] = g["losing"] / float(g["members"])
    return rows


def check(rows, lines):
    """Loans and certificates are every row of their product, so their
    interest and FTP must add up to the product view's."""
    from keel.measures import Check
    by = {}
    for r in rows:
        if r["kind"] in ("loan", "certificate"):
            b = by.setdefault(r["product"], [0.0, 0.0])
            b[0] += r["interest"]
            b[1] += r["ftp"]
    worst = 0.0
    for line in lines:
        if line.product in by:
            worst = max(worst, abs(by[line.product][0] - line.interest), abs(by[line.product][1] - line.ftp))
    return Check("Loan and certificate accounts add up to their products' interest and FTP", worst < 1.0,
                 "%d products; largest difference $%s" % (len(by), "{:,.2f}".format(worst)))
