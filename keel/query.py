"""Ad hoc reporting: group, filter and total any table Keel holds.

    python -m keel query <folder> --table positions --by product,rate_band \\
        --measure "sum balance" --measure "wavg rate balance" --where "side = asset"
    python -m keel query <folder> queries/cd-ladder.json --out ladder.xlsx

A query names a table, the fields to group by, the measures, and filters:

    {"name": "Loans by rate band", "table": "positions",
     "by": ["product", "rate_band"],
     "measures": ["count", "sum balance", "wavg rate balance", "wavg ftp_rate balance"],
     "where": ["side = asset", "product in used_auto,new_auto"],
     "sort": "-sum balance", "limit": 50}

Measures: `count`, `sum F`, `avg F`, `min F`, `max F`, and `wavg F W` (F
weighted by W). Filters: `=`, `!=`, `>`, `>=`, `<`, `<=`, `in a,b,c` and
`contains`. Every query saved in the folder's `queries/` is run into the
report and the Excel workbook, so a report someone asked for once becomes
one the next run produces again.

Tables: `positions` (the book, with FTP, spread, capital and bands added),
`profitability` (by product), `budget` (product by month), `projection`
(the base plan by month), and, when the book came from core files, each of
those files as it is (`loans`, `certificates`, ...).
"""

import csv
import glob
import json
import os

from keel import profitability
from keel.engine import CASH
from keel.model import InputError

OPS = (">=", "<=", "!=", "=", ">", "<", " in ", " contains ")
TERM_BANDS = ((0, "no term"), (12, "up to 1 year"), (36, "1-3 years"), (60, "3-5 years"), (120, "5-10 years"),
              (240, "10-20 years"), (10 ** 6, "over 20 years"))


def _term_band(months):
    if months <= 0:
        return TERM_BANDS[0][1]
    for bound, label in TERM_BANDS[1:]:
        if months <= bound:
            return label


def _rate_band(rate_pct):
    low = int(rate_pct * 2) / 2.0
    return "%.1f-%.1f%%" % (low, low + 0.5)


def position_rows(positions, a):
    ftp = profitability.ftp_rates(positions, a)
    rows = []
    for p in positions:
        spec = a.products[p.product]
        f = ftp[p.id]
        weight = profitability.risk_weight(p.product, spec, p.side) if p.side == "asset" else 0.0
        rate = a.curve.rate(a.short_tenor) / 100.0 if p.product == CASH else p.rate
        spread = None if f is None else (rate - f if p.side == "asset" else f - rate)
        rows.append({"id": p.id, "name": p.name, "product": p.product, "side": p.side, "balance": p.balance,
                     "rate": 100 * rate, "rate_type": p.rate_type, "index": p.index,
                     "term_months": p.term_months, "amortization": p.amortization,
                     "reprices_in": (p.next_reset_months or p.reset_months) if p.rate_type == "variable"
                     else p.term_months,
                     "term_band": _term_band(p.term_months), "rate_band": _rate_band(100 * p.rate),
                     "ftp_rate": None if f is None else 100 * f, "spread": None if spread is None else 100 * spread,
                     "risk_weight": 100 * weight, "rwa": max(p.balance, 0.0) * weight,
                     "capital": max(p.balance, 0.0) * weight * a.target_capital})
    return rows


class Tables(object):
    """Every table a query can read, each built only when a query asks for
    it: a query of the loan file should not wait for a five-year plan. Pass
    a `results.compute` result as `r` to reuse what it already computed."""

    def __init__(self, positions, a, folder=None, imported=None, r=None):
        self.positions, self.a, self.folder, self.imported, self.r = positions, a, folder, imported, r
        self.cache = {}
        self.files = {}
        if folder and imported is not None:
            data = os.path.join(folder, "data")
            for path in sorted(glob.glob(os.path.join(data, "*.csv")) + glob.glob(os.path.join(data, "*.xlsx"))):
                self.files[os.path.splitext(os.path.basename(path))[0]] = path

    def names(self):
        extra = ["accounts", "members"] if self.imported is not None and self.imported.accounts else []
        return ["positions", "profitability", "budget", "projection"] + extra + sorted(self.files)

    def _accounts(self):
        if self.r is not None and self.r.get("accounts"):
            return self.r["accounts"]
        if "_accounts" not in self.cache:
            from keel import accounts
            self.cache["_accounts"] = accounts.run(self.positions, self.a, self.imported.accounts)
        return self.cache["_accounts"]

    def __contains__(self, name):
        return name in self.names()

    def __getitem__(self, name):
        if name not in self.cache:
            self.cache[name] = self._build(name)
        return self.cache[name]

    def _base(self):
        if self.r is not None:
            return self.r["base_run"]
        if "_base" not in self.cache:
            from keel import engine
            self.cache["_base"] = engine.going_concern(self.positions, self.a, self.a.scenarios[0])
        return self.cache["_base"]

    def _build(self, name):
        if name == "positions":
            return position_rows(self.positions, self.a)
        if name == "profitability":
            lines = (self.r["profitability"]["lines"] if self.r is not None
                     else profitability.product_lines(self.positions, self.a)[0])
            return [{"product": x.product, "side": x.side, "balance": x.balance, "yield": 100 * x.rate(x.interest),
                     "ftp_rate": 100 * x.rate(x.ftp), "spread": x.spread, "fees": x.fees, "servicing": x.servicing,
                     "expected_loss": x.expected_loss, "capital": x.capital, "net": x.net,
                     "roa": 100 * x.rate(x.net), "raroc": None if x.raroc is None else 100 * x.raroc}
                    for x in lines]
        if name in ("accounts", "members") and name in self.names():
            rows = self._accounts()["rows" if name == "accounts" else "members"]
            return [dict(x, rate=100 * x["rate"], ftp_rate=100 * x["ftp_rate"]) if name == "accounts" else dict(x)
                    for x in rows]
        if name == "budget":
            from keel import budget
            b = self.r["budget"] if self.r is not None else budget.build(self.positions, self.a, self._base())
            return [{"month": b["labels"][t], "product": p["product"], "side": p["side"], "end": p["end"][t],
                     "average": p["average"][t], "interest": p["interest"][t], "yield": 100 * p["yield"][t]}
                    for p in b["products"] for t in range(len(b["labels"]))]
        if name == "projection":
            return [{"month": m.month, "cash": m.cash, "overnight": m.overnight, "nii": m.nii,
                     "interest_income": m.interest_income, "interest_expense": m.interest_expense,
                     "net_income": m.net_income, "assets": m.assets, "liabilities": m.liabilities,
                     "equity": m.equity, "net_worth_ratio": 100 * m.equity / m.assets}
                    for m in self._base()]
        if name in self.files:
            from keel import tables
            return tables.read_table(self.files[name])
        raise KeyError(name)

    def fields(self, name):
        rows = self[name]
        return list(rows[0]) if rows else []


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _filter(text):
    for op in OPS:
        if op in text:
            field, value = text.split(op, 1)
            return field.strip(), op.strip(), value.strip()
    raise InputError("query: cannot read the filter %r (use =, !=, >, >=, <, <=, in, contains)" % text)


def _matches(row, field, op, value):
    have = row.get(field)
    if op == "in":
        return str(have) in [v.strip() for v in value.split(",")]
    if op == "contains":
        return value.lower() in str(have).lower()
    a, b = _num(have), _num(value)
    if a is not None and b is not None:
        return {"=": a == b, "!=": a != b, ">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]
    have, value = str(have), str(value)
    return {"=": have == value, "!=": have != value, ">": have > value, ">=": have >= value,
            "<": have < value, "<=": have <= value}[op]


def _measure(text):
    parts = text.split()
    if parts == ["count"]:
        return ("count", None, None)
    if len(parts) == 2 and parts[0] in ("sum", "avg", "min", "max"):
        return (parts[0], parts[1], None)
    if len(parts) == 3 and parts[0] == "wavg":
        return ("wavg", parts[1], parts[2])
    raise InputError("query: cannot read the measure %r (count, sum F, avg F, min F, max F, wavg F W)" % text)


def _aggregate(rows, measures):
    out = []
    for kind, field, weight in measures:
        if kind == "count":
            out.append(len(rows))
            continue
        values = [(_num(r.get(field)), _num(r.get(weight)) if weight else 1.0) for r in rows]
        values = [(v, w) for v, w in values if v is not None and w is not None]
        if not values:
            out.append(None)
        elif kind == "sum":
            out.append(sum(v for v, _ in values))
        elif kind == "avg":
            out.append(sum(v for v, _ in values) / len(values))
        elif kind == "min":
            out.append(min(v for v, _ in values))
        elif kind == "max":
            out.append(max(v for v, _ in values))
        else:
            total = sum(w for _, w in values)
            out.append(sum(v * w for v, w in values) / total if total else None)
    return out


def run(spec, tables):
    """{name, table, columns, rows, total} for a query spec."""
    table = spec.get("table", "positions")
    if table not in tables:
        names = tables.names() if hasattr(tables, "names") else sorted(tables)
        raise InputError("query: no table %r (have: %s)" % (table, ", ".join(names)))
    rows = tables[table]
    fields = set(rows[0]) if rows else set()
    by = [b for b in (spec.get("by") or []) if b]
    measures_text = spec.get("measures") or ["count"]
    measures = [_measure(m) for m in measures_text]
    filters = [_filter(w) for w in (spec.get("where") or []) if w.strip()]
    for name in by + [f for f, _, _ in filters] + [x for _, f, w in measures for x in (f, w) if x]:
        if name not in fields:
            raise InputError("query: table %s has no field %r (fields: %s)" % (table, name, ", ".join(sorted(fields))))
    kept = [r for r in rows if all(_matches(r, f, op, v) for f, op, v in filters)]
    groups = {}
    for r in kept:
        groups.setdefault(tuple(str(r.get(b, "")) for b in by), []).append(r)
    result = [list(key) + _aggregate(members, measures) for key, members in groups.items()]
    columns = by + list(measures_text)
    sort = spec.get("sort")
    if sort:
        descending = sort.startswith("-")
        name = sort.lstrip("-")
        if name not in columns:
            raise InputError("query: sort %r is not a column (%s)" % (name, ", ".join(columns)))
        i = columns.index(name)
        result.sort(key=lambda row: (row[i] is None, row[i] if isinstance(row[i], (int, float)) else str(row[i])),
                    reverse=descending)
    else:
        result.sort(key=lambda row: [str(v) for v in row[:len(by)]])
    limit = spec.get("limit")
    if limit:
        result = result[:int(limit)]
    total = ["Total"] + [""] * (len(by) - 1) + _aggregate(kept, measures) if by else None
    return {"name": spec.get("name", "Query"), "table": table, "columns": columns, "rows": result,
            "total": total, "matched": len(kept), "of": len(rows)}


def saved(folder):
    """The queries saved in `folder`/queries, as specs."""
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "queries", "*.json"))):
        with open(path, encoding="utf-8") as handle:
            try:
                spec = json.load(handle)
            except ValueError as error:
                raise InputError("%s: %s" % (path, error))
        spec.setdefault("name", os.path.splitext(os.path.basename(path))[0])
        out.append(spec)
    return out


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, int) and not isinstance(v, bool):
        return "{:,}".format(v)
    if isinstance(v, float):
        return "{:,.0f}".format(v) if abs(v) >= 1000 else "{:,.2f}".format(v)
    return str(v)


def write(result, path):
    rows = [result["columns"]] + result["rows"] + ([result["total"]] if result["total"] else [])
    if path.lower().endswith(".xlsx"):
        from keel import xlsx
        xlsx.write_workbook(path, {result["name"][:31]: rows})
    else:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
