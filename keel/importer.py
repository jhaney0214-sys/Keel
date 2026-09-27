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

from keel.model import InputError, Position

#: Pooling bands. Wide enough to keep a large credit union's 100,000 loans to
#: a few hundred positions, narrow enough that the pool's cash flows are the
#: loans' cash flows.
TERM_BAND = 6          # months
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
    path = os.path.join(folder, name)
    if not os.path.isfile(path):
        if required:
            raise InputError("missing %s" % path)
        return []
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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


class Pool(object):
    """Balance-weighted accumulation of detail rows that behave alike."""

    def __init__(self, key, template):
        self.key, self.template = key, template
        self.balance = self.rate_x = self.margin_x = self.term_x = self.amort_x = 0.0
        self.count = 0

    def add(self, balance, rate, term, margin=0.0, amort=0):
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
            id=pid, name="%s pool (%d rows)" % (t["product"], self.count), product=t["product"],
            side=t["side"], balance=round(self.balance, 2), rate=self.rate_x / b / 100.0,
            rate_type=t["rate_type"], index=t.get("index", ""), margin=self.margin_x / b / 100.0,
            reset_months=t.get("reset_months", 0), term_months=int(round(self.term_x / b)),
            amortization=t["amortization"], floor=t.get("floor"), cap=t.get("cap"),
            amort_months=int(round(self.amort_x / b)), next_reset_months=t.get("next_reset_months", 0))


def import_folder(folder, as_of):
    """Read `folder`/data and return Imported. `as_of` is a date."""
    data = os.path.join(folder, "data")
    with open(os.path.join(data, "product_map.json"), encoding="utf-8") as handle:
        mapping = json.load(handle)
    rows = {}
    positions, summaries = [], {}

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
        rate = _num(r["rate"])
        variable = r["rate_type"].strip().upper() == "V"
        maturity = _date(r["maturity_date"])
        revolving = maturity is None
        term = 0 if revolving else max(1, months_between(as_of, maturity))
        amort = 0
        if r.get("amortization_months"):
            opened = _date(r["origination_date"])
            amort = max(term + 1, int(_num(r["amortization_months"])) - months_between(opened, as_of))
        if revolving:
            key = (product, "revolving", variable, r["index"], round(rate / RATE_BAND))
            template = {"product": product, "side": "asset", "rate_type": "variable" if variable else "fixed",
                        "index": r["index"], "reset_months": int(_num(r["reset_months"], 1)) if variable else 0,
                        "amortization": "nonmaturity",
                        "floor": _num(r["rate_floor"]) / 100.0 if r["rate_floor"] else None}
        else:
            next_reset = 0
            if variable and r["next_reset_date"]:
                next_reset = max(1, months_between(as_of, _date(r["next_reset_date"])))
            key = (product, variable, r["index"], r["margin"], next_reset // 12 if variable else 0,
                   term // TERM_BAND, round(rate / RATE_BAND), bool(amort))
            template = {"product": product, "side": "asset", "rate_type": "variable" if variable else "fixed",
                        "index": r["index"], "reset_months": int(_num(r["reset_months"])) if variable else 0,
                        "amortization": "balloon" if amort else "level",
                        "floor": _num(r["rate_floor"]) / 100.0 if r["rate_floor"] else None,
                        "cap": _num(r["rate_cap"]) / 100.0 if r["rate_cap"] else None,
                        "next_reset_months": next_reset}
        pool = pools.get(key)
        if pool is None:
            pool = pools[key] = Pool(key, template)
        pool.add(balance, rate, term, _num(r["margin"]), amort)
        s = by_product.setdefault(product, {"product": product, "count": 0, "balance": 0.0, "rate_x": 0.0,
                                            "term_x": 0.0, "delinquent": 0.0})
        s["count"] += 1
        s["balance"] += balance
        s["rate_x"] += balance * rate
        s["term_x"] += balance * term
        if _num(r.get("days_delinquent")) >= 60:
            s["delinquent"] += balance
    for i, pool in enumerate(sorted(pools.values(), key=lambda p: (p.template["product"], p.key)), 1):
        positions.append(pool.position("loan%04d" % i))
    summaries["loans"] = [dict(s, rate=s["rate_x"] / s["balance"], term=s["term_x"] / s["balance"])
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
        band = "0-3 months" if term <= 3 else "4-6 months" if term <= 6 else "7-12 months" if term <= 12 \
            else "13-24 months" if term <= 24 else "over 24 months"
        s = ladder.setdefault(band, {"band": band, "count": 0, "balance": 0.0, "rate_x": 0.0})
        s["count"] += 1
        s["balance"] += balance
        s["rate_x"] += balance * rate
    for i, pool in enumerate(sorted(pools.values(), key=lambda p: p.key), 1):
        positions.append(pool.position("cert%04d" % i))
    order = ["0-3 months", "4-6 months", "7-12 months", "13-24 months", "over 24 months"]
    summaries["certificates"] = [dict(ladder[b], rate=ladder[b]["rate_x"] / ladder[b]["balance"])
                                 for b in order if b in ladder]

    # ---- non-maturity shares
    shares = _read(data, "shares.csv")
    rows["shares"] = len(shares)
    share_totals = {}
    for i, r in enumerate(shares, 1):
        product = mapping["shares"][r["product_code"]]
        balance = _num(r["balance"])
        positions.append(Position(
            id="share%03d" % i, name="%s %s+" % (r["product_code"], r["tier_low"]), product=product,
            side="liability", balance=balance, rate=_num(r["rate"]) / 100.0, rate_type="administered",
            amortization="nonmaturity"))
        share_totals[r["product_code"]] = share_totals.get(r["product_code"], 0.0) + balance

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
    return Imported(positions=positions, ties=ties, summaries=summaries, rows=rows)


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
