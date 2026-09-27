"""From the files a core system exports to Keel's positions, tied to the GL.

A credit union's ALM run starts the same way every quarter: pull loans,
certificates, shares, investments and borrowings from the core, and prove
they add up to the general ledger before modelling anything. This does both.

    data/loans.csv          one row per loan
    data/certificates.csv   one row per certificate
    data/shares.csv         non-maturity shares by product and balance tier
    data/investments.csv    one row per security
    data/borrowings.csv     one row per borrowing
    data/gl.csv             the trial balance
    data/product_map.json   core product codes -> Keel products, GL accounts
    data/member_shares.csv  optional: each member's share balance by product

Loans and certificates may carry `member_id` and `branch` columns; with
them, and member_shares.csv, the report reads profitability by account,
member and branch (keel/accounts.py). Every detail row is kept, with the
position it pooled into, in `Imported.accounts`.

Loans and certificates are pooled: rows that behave alike (product, rate
type, index and margin, reset timing, remaining term and rate within a band)
become one position at their balance-weighted rate. Securities and
borrowings stay one position each, since the portfolio report needs them by
name. Every pool is traceable: `pool` names the rule, and `rows` its count.
"""

import csv
import dataclasses
import datetime
import json
import os

from keel import tables, xlsx
from keel.model import InputError, Position

#: Pooling bands. Wide enough to keep a large credit union's 100,000 loans to
#: a few hundred positions, narrow enough that the pool's cash flows are the
#: loans' cash flows.
TERM_BAND = 6          # months
#: Loans this far past due stop accruing interest, as the accounting requires.
NONACCRUAL_DAYS = 90
RATE_BAND = 0.25       # percent
MATURITY_TOLERANCE = 0.01   # dollars: detail vs GL differences above this are reported


def months_between(start, end):
    return (end.year - start.year) * 12 + end.month - start.month + (1 if end.day > start.day else 0)


def _date(text):
    text = (text or "").strip()
    return datetime.date.fromisoformat(text) if text else None


def _num(text, default=0.0):
    text = (text or "").strip()
    return float(text) if text else default


def _read(folder, name, required=True):
    """A data table by name, from name.csv or name.xlsx."""
    path = tables.find(folder, os.path.splitext(name)[0], required)
    return tables.read_table(path) if path else []


def read_mapping(folder):
    """product_map.json, or product_map.xlsx with one sheet per section
    (code | product; the gl sheet is key | account)."""
    json_path = os.path.join(folder, "product_map.json")
    xlsx_path = os.path.join(folder, "product_map.xlsx")
    if os.path.isfile(json_path) and os.path.isfile(xlsx_path):
        raise InputError("both product_map.json and product_map.xlsx are in %s; keep one" % folder)
    if os.path.isfile(json_path):
        with open(json_path, encoding="utf-8") as handle:
            return json.load(handle)
    if not os.path.isfile(xlsx_path):
        raise InputError("missing product_map.json (or .xlsx) in %s" % folder)
    out = {}
    for name, rows in xlsx.read_workbook(xlsx_path).items():
        section = {}
        for row in xlsx.table(rows):
            key = xlsx.as_text(row.get("key", row.get("code")))
            value = xlsx.as_text(row.get("account", row.get("product")))
            if key:
                section[key] = value
        out[name] = section
    return out


@dataclasses.dataclass
class Tie:
    line: str
    detail: float
    ledger: float

    @property
    def difference(self):
        return self.detail - self.ledger

    @property
    def ties(self):
        return abs(self.difference) <= MATURITY_TOLERANCE


@dataclasses.dataclass
class Imported:
    positions: list
    ties: list
    summaries: dict          # name -> list of dict rows, for the portfolio pages
    rows: dict               # file -> row count
    accounts: list = dataclasses.field(default_factory=list)   # detail rows, see _account


class Pool(object):
    """Balance-weighted accumulation of detail rows that behave alike."""

    def __init__(self, key, template):
        self.key, self.template = key, template
        self.balance = self.rate_x = self.margin_x = self.term_x = self.amort_x = self.age_x = 0.0
        self.count = 0
        self.aged = False

    def add(self, balance, rate, term, margin=0.0, amort=0, age=None):
        if age is not None:
            self.age_x += balance * age
            self.aged = True
        self.balance += balance
        self.rate_x += balance * rate
        self.margin_x += balance * margin
        self.term_x += balance * term
        self.amort_x += balance * amort
        self.count += 1

    def position(self, pid):
        b = self.balance or 1.0
        t = self.template
        return Position(
            id=pid, name="%s%s pool (%d rows)" % (t["product"], " non-accrual" if t.get("nonaccrual") else "",
                                                 self.count), product=t["product"],
            side=t["side"], balance=round(self.balance, 2), rate=self.rate_x / b / 100.0,
            rate_type=t["rate_type"], index=t.get("index", ""), margin=self.margin_x / b / 100.0,
            reset_months=t.get("reset_months", 0), term_months=int(round(self.term_x / b)),
            amortization=t["amortization"], floor=t.get("floor"), cap=t.get("cap"),
            amort_months=int(round(self.amort_x / b)), next_reset_months=t.get("next_reset_months", 0),
            loan_age=int(round(self.age_x / b)) if self.aged else None)


def _account(kind, account_id, product, key, balance, rate, row, **extra):
    """One detail row as the account-profitability view reads it. `key` is
    the pool it joined (a position id once the pools are numbered)."""
    out = {"kind": kind, "id": account_id, "product": product, "position": key, "balance": balance,
           "rate": rate / 100.0, "member_id": (row.get("member_id") or "").strip(),
           "branch": (row.get("branch") or "").strip()}
    out.update(extra)
    return out


def import_folder(folder, as_of):
    """Read `folder`/data and return Imported. `as_of` is a date."""
    data = os.path.join(folder, "data")
    mapping = read_mapping(data)
    rows = {}
    positions, summaries, detail = [], {}, []

    # ---- loans
    loans = _read(data, "loans.csv")
    rows["loans"] = len(loans)
    pools = {}
    by_product = {}
    for r in loans:
        code = r["product_code"]
        product = mapping["loans"].get(code) or mapping["loans"].get("*")
        if not product:
            raise InputError("loans.csv: loan %s has product code %r, which product_map.json does not map"
                             % (r["loan_id"], code))
        balance = _num(r["current_balance"])
        nonaccrual = _num(r.get("days_delinquent")) >= NONACCRUAL_DAYS
        # A non-accrual loan earns nothing, so it pools apart at a zero rate;
        # its principal still runs off on schedule, and charge-offs apply.
        rate = 0.0 if nonaccrual else _num(r["rate"])
        variable = r["rate_type"].strip().upper() == "V" and not nonaccrual
        maturity = _date(r["maturity_date"])
        revolving = maturity is None
        term = 0 if revolving else max(1, months_between(as_of, maturity))
        amort = 0
        if r.get("amortization_months"):
            opened = _date(r["origination_date"])
            amort = max(term + 1, int(_num(r["amortization_months"])) - months_between(opened, as_of))
        if revolving:
            key = (product, "revolving", variable, r["index"], round(rate / RATE_BAND), nonaccrual)
            template = {"product": product, "side": "asset", "rate_type": "variable" if variable else "fixed",
                        "nonaccrual": nonaccrual,
                        "index": r["index"], "reset_months": int(_num(r["reset_months"], 1)) if variable else 0,
                        "amortization": "nonmaturity",
                        "floor": _num(r["rate_floor"]) / 100.0 if r["rate_floor"] else None}
        else:
            next_reset = 0
            if variable and r["next_reset_date"]:
                next_reset = max(1, months_between(as_of, _date(r["next_reset_date"])))
            key = (product, variable, r["index"], r["margin"], next_reset // 12 if variable else 0,
                   term // TERM_BAND, round(rate / RATE_BAND), bool(amort), nonaccrual)
            template = {"product": product, "side": "asset", "rate_type": "variable" if variable else "fixed",
                        "nonaccrual": nonaccrual,
                        "index": r["index"], "reset_months": int(_num(r["reset_months"])) if variable else 0,
                        "amortization": "balloon" if amort else "level",
                        "floor": _num(r["rate_floor"]) / 100.0 if r["rate_floor"] else None,
                        "cap": _num(r["rate_cap"]) / 100.0 if r["rate_cap"] else None,
                        "next_reset_months": next_reset}
        pool = pools.get(key)
        if pool is None:
            pool = pools[key] = Pool(key, template)
        opened = _date(r.get("origination_date") or "")
        pool.add(balance, rate, term, _num(r["margin"]), amort,
                 max(0, months_between(opened, as_of)) if opened else None)
        detail.append(_account("loan", r["loan_id"], product, ("loan", key), balance, rate, r,
                                 nonaccrual=nonaccrual, days_delinquent=int(_num(r.get("days_delinquent"))),
                                 term_months=term))
        s = by_product.setdefault(product, {"product": product, "count": 0, "balance": 0.0, "rate_x": 0.0,
                                            "term_x": 0.0, "delinquent": 0.0, "nonaccrual": 0.0,
                                            "contract_x": 0.0})
        s["count"] += 1
        s["balance"] += balance
        s["rate_x"] += balance * rate
        s["contract_x"] += balance * _num(r["rate"])
        s["term_x"] += balance * term
        if nonaccrual:
            s["nonaccrual"] += balance
        if _num(r.get("days_delinquent")) >= 60:
            s["delinquent"] += balance
    ids = {}
    for i, pool in enumerate(sorted(pools.values(), key=lambda p: (p.template["product"], p.key)), 1):
        positions.append(pool.position("loan%04d" % i))
        ids[("loan", pool.key)] = positions[-1].id
    summaries["loans"] = [dict(s, rate=s["contract_x"] / s["balance"], term=s["term_x"] / s["balance"])
                          for s in sorted(by_product.values(), key=lambda s: -s["balance"])]

    # ---- certificates
    certs = _read(data, "certificates.csv")
    rows["certificates"] = len(certs)
    pools = {}
    ladder = {}
    for r in certs:
        product = mapping["certificates"].get(r["product_code"]) or mapping["certificates"].get("*")
        balance, rate = _num(r["balance"]), _num(r["rate"])
        term = max(1, months_between(as_of, _date(r["maturity_date"])))
        key = (product, term, round(rate / RATE_BAND))
        pool = pools.get(key)
        if pool is None:
            pool = pools[key] = Pool(key, {"product": product, "side": "liability", "rate_type": "fixed",
                                           "amortization": "bullet"})
        pool.add(balance, rate, term)
        detail.append(_account("certificate", r["certificate_id"], product, ("cert", key), balance, rate, r,
                                 term_months=term))
        band = "0-3 months" if term <= 3 else "4-6 months" if term <= 6 else "7-12 months" if term <= 12 \
            else "13-24 months" if term <= 24 else "over 24 months"
        s = ladder.setdefault(band, {"band": band, "count": 0, "balance": 0.0, "rate_x": 0.0})
        s["count"] += 1
        s["balance"] += balance
        s["rate_x"] += balance * rate
    for i, pool in enumerate(sorted(pools.values(), key=lambda p: p.key), 1):
        positions.append(pool.position("cert%04d" % i))
        ids[("cert", pool.key)] = positions[-1].id
    for x in detail:
        x["position"] = ids[x["position"]]
    order = ["0-3 months", "4-6 months", "7-12 months", "13-24 months", "over 24 months"]
    summaries["certificates"] = [dict(ladder[b], rate=ladder[b]["rate_x"] / ladder[b]["balance"])
                                 for b in order if b in ladder]

    # ---- non-maturity shares
    shares = _read(data, "shares.csv")
    rows["shares"] = len(shares)
    share_totals = {}
    tiers = {}
    for i, r in enumerate(shares, 1):
        product = mapping["shares"][r["product_code"]]
        balance = _num(r["balance"])
        tiers.setdefault(r["product_code"], []).append((_num(r["tier_low"]), _num(r["tier_high"], 0.0),
                                                        "share%03d" % i, _num(r["rate"]), product))
        positions.append(Position(
            id="share%03d" % i, name="%s %s+" % (r["product_code"], r["tier_low"]), product=product,
            side="liability", balance=balance, rate=_num(r["rate"]) / 100.0, rate_type="administered",
            amortization="nonmaturity"))
        share_totals[r["product_code"]] = share_totals.get(r["product_code"], 0.0) + balance
    member_shares = _read(data, "member_shares.csv", required=False)
    rows["member_shares"] = len(member_shares)
    for n, r in enumerate(member_shares, 2):
        code = r.get("product_code", "")
        if code not in tiers:
            raise InputError("member_shares.csv line %d: product code %r is not in shares.csv" % (n, code))
        balance = _num(r["balance"])
        # The tier the balance falls in sets its rate and its position; a
        # tier's high of 0 or blank means no upper bound.
        low, high, pid, rate, product = next(
            (t for t in tiers[code] if t[0] <= balance and (not t[1] or balance < t[1])), tiers[code][-1])
        detail.append(_account("share", "%s-%s" % (r["member_id"], code), product, pid, balance, rate, r))

    # ---- investments
    securities = _read(data, "investments.csv")
    rows["investments"] = len(securities)
    for r in securities:
        product = mapping["investments"][r["type"]]
        book, rate = _num(r["book_value"]), _num(r["book_yield"])
        maturity = _date(r["maturity_date"])
        term = max(1, months_between(as_of, maturity)) if maturity else 0
        if r["type"] in ("FHLBSTOCK", "CUSO"):
            kind, rate_type = "none", "fixed" if rate else "none"
        elif r["type"] in ("MBS", "CMO"):
            kind, rate_type = "level", "fixed"
            term = int(_num(r["wam_months"], term)) or term
        elif r["next_call_date"]:
            kind, rate_type = "callable", "fixed"
        else:
            kind, rate_type = "bullet", "fixed"
        call = max(1, months_between(as_of, _date(r["next_call_date"]))) if r["next_call_date"] else 0
        positions.append(Position(
            id=r["security_id"], name=r["description"], product=product, side="asset", balance=book,
            rate=rate / 100.0, rate_type=rate_type, term_months=term, amortization=kind, call_months=call))
    summaries["investments"] = securities

    # ---- borrowings
    borrowings = _read(data, "borrowings.csv", required=False)
    rows["borrowings"] = len(borrowings)
    for r in borrowings:
        positions.append(Position(
            id=r["borrowing_id"], name="%s %s" % (r["lender"], r["type"]),
            product=mapping["borrowings"][r["lender"]], side="liability", balance=_num(r["balance"]),
            rate=_num(r["rate"]) / 100.0, rate_type="fixed",
            term_months=max(1, months_between(as_of, _date(r["maturity_date"]))), amortization="bullet"))

    # ---- the ledger: what the detail must add up to, and what it cannot supply
    gl = {r["account"]: _num(r["balance"]) for r in _read(data, "gl.csv")}
    accounts = mapping["gl"]
    for key, product, side in (("cash", "cash", "asset"), ("fixed_assets", "fixed_assets", "asset"),
                               ("ncusif", "ncusif", "asset"), ("other_assets", "other_assets", "asset"),
                               ("allowance", "other_assets", "asset"),
                               ("other_liabilities", "other_liabilities", "liability")):
        if key not in accounts:
            continue
        value = gl.get(accounts[key], 0.0)
        balance = value if side == "asset" else -value
        positions.append(Position(id="gl_" + key, name=key.replace("_", " "), product=product, side=side,
                                  balance=balance, rate=0.0, rate_type="none", amortization="none"))
    ties = _ties(gl, loans, certs, shares, securities, borrowings)
    return Imported(positions=positions, ties=ties, summaries=summaries, rows=rows, accounts=detail)


def _ties(gl, loans, certs, shares, securities, borrowings):
    """Detail against ledger, line by line. The account numbers are the
    synthetic samples' own chart; a real credit union maps its own."""
    return [Tie("Loans (1200)", sum(_num(r["current_balance"]) for r in loans), abs(gl.get("1200", 0.0))),
            Tie("Investments at book (1100)", sum(_num(r["book_value"]) for r in securities),
                abs(gl.get("1100", 0.0))),
            Tie("Share certificates (3500)", sum(_num(r["balance"]) for r in certs), abs(gl.get("3500", 0.0))),
            Tie("Non-maturity shares (3000-3090)", sum(_num(r["balance"]) for r in shares),
                sum(abs(v) for a, v in gl.items() if a.startswith("30"))),
            Tie("Borrowings (2000)", sum(_num(r["balance"]) for r in borrowings), abs(gl.get("2000", 0.0)))]


def write_positions(positions, path):
    fields = ["id", "name", "product", "side", "balance", "rate", "rate_type", "index", "margin",
              "reset_months", "next_reset_months", "term_months", "amortization", "amort_months",
              "call_months", "floor", "cap"]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(fields)
        for p in positions:
            w.writerow([p.id, p.name, p.product, p.side, "%.2f" % p.balance, "%.4f" % (100 * p.rate),
                        p.rate_type, p.index, "%.4f" % (100 * p.margin) if p.margin else "",
                        p.reset_months or "", p.next_reset_months or "", p.term_months or "",
                        p.amortization, p.amort_months or "", p.call_months or "",
                        "%.4f" % (100 * p.floor) if p.floor is not None else "",
                        "%.4f" % (100 * p.cap) if p.cap is not None else ""])
