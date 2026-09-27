"""The settings file, as an Excel workbook or as JSON, and conversion between them.

Most credit unions keep their ALM settings in Excel. The workbook Keel reads
and writes has one sheet per kind of setting, in the same units as the JSON
(rates and speeds in percent):

    Settings       key | value | note         analysis date, horizon, income and expense...
    Curve          tenor_months | rate
    Indexes        index | tenor_months | spread
    Products       product | cpr | cpr_per_100bp | ... (a blank cell is the default)
    Scenarios      name | shock_bp | ramp_months | shape ("1:200, 24:100, 120:0")
    Contingent     name | capacity
    Limits         key | value | note         the board's policy limits (blank: default)
    Notes          key | text

Both forms become the same dictionary before anything reads them, so a
what-if changes a setting by the same dotted path either way.

    python -m keel convert assumptions.json assumptions.xlsx
    python -m keel convert assumptions.xlsx assumptions.json
"""

import dataclasses
import json
import os

from keel import xlsx
from keel.model import InputError, Product

SETTINGS = (  # key, type, note for the workbook
    ("as_of", "date", "The analysis date: positions are as of this day."),
    ("institution", "text", "credit_union or bank: sets the regulator's tests and the words used."),
    ("horizon_months", "int", "How far the plan and NII run."),
    ("nev_max_months", "int", "How far runoff cash flows run for NEV."),
    ("rate_floor", "float", "No scenario rate goes below this, in percent."),
    ("short_tenor_months", "int", "The curve point share rates follow."),
    ("fee_income", "float", "Fee and other income, dollars a year."),
    ("operating_expense", "float", "Operating expense, dollars a year."),
    ("expense_growth", "float", "Annual growth in operating expense, percent."),
    ("cash_minimum", "float", "Cash held; below it the plan borrows overnight."),
    ("overnight_spread", "float", "Overnight borrowing cost over the short rate, percent."),
    ("stress_months", "int", "Length of the liquidity stress, months."),
    ("tax_rate", "float", "Income tax, percent of pre-tax income (default 0 for a credit union, 21 for a bank)."),
    ("target_capital", "float", "Capital held per dollar of risk-weighted assets, percent, for product capital."),
    ("hurdle_rate", "float", "Return on allocated capital that pricing aims for (RAROC hurdle), percent."),
)
PRODUCT_FIELDS = [f.name for f in dataclasses.fields(Product) if f.name != "name"]
SHEETS = ("Settings", "Curve", "Indexes", "Products", "Scenarios", "Contingent", "Limits", "Notes")


def find(folder):
    """The settings file in `folder`: assumptions.xlsx or assumptions.json."""
    paths = [os.path.join(folder, "assumptions" + ext) for ext in (".xlsx", ".json")]
    found = [p for p in paths if os.path.isfile(p)]
    if len(found) > 1:
        raise InputError("both assumptions.xlsx and assumptions.json are in %s; keep one" % folder)
    if not found:
        raise InputError("missing assumptions.xlsx (or assumptions.json) in %s" % folder)
    return found[0]


def load(path):
    """The raw settings dictionary, from either form."""
    if path.lower().endswith(".xlsx"):
        try:
            return from_workbook(xlsx.read_workbook(path), path)
        except xlsx.WorkbookError as error:
            raise InputError(str(error))
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _number(value, kind, where):
    if value in (None, ""):
        return None
    try:
        if kind == "int":
            return int(float(value))
        if kind == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "yes", "y", "1", "x"):
                return True
            if text in ("false", "no", "n", "0"):
                return False
            raise ValueError(value)
        return float(value)
    except (TypeError, ValueError):
        raise InputError("%s: %r is not a %s" % (where, value, {"int": "whole number", "bool": "yes/no",
                                                             "float": "number"}[kind]))


def _shape(text, where):
    out = {}
    for part in str(text).replace(";", ",").split(","):
        if part.strip():
            try:
                tenor, bp = part.split(":")
                out[str(int(float(tenor)))] = float(bp)
            except ValueError:
                raise InputError("%s: shape %r should read like '1:200, 24:100, 120:0'" % (where, text))
    return out


def from_workbook(book, path="settings workbook"):
    missing = [s for s in ("Settings", "Curve", "Products") if s not in book]
    if missing:
        raise InputError("%s: missing sheet%s %s" % (path, "s" if len(missing) > 1 else "", ", ".join(missing)))
    raw = {}
    kinds = {k: t for k, t, _ in SETTINGS}
    for row in xlsx.table(book["Settings"]):
        key = xlsx.as_text(row.get("key"))
        if not key:
            continue
        if key not in kinds:
            raise InputError("%s, Settings: unknown key %r" % (path, key))
        value = row.get("value")
        if kinds[key] == "date":
            raw[key] = xlsx.excel_date(value)
        elif kinds[key] == "text":
            if xlsx.as_text(value):
                raw[key] = xlsx.as_text(value)
        else:
            number = _number(value, kinds[key], "%s, Settings, %s" % (path, key))
            if number is not None:
                raw[key] = number
    raw["curve"] = {}
    for row in xlsx.table(book["Curve"]):
        tenor = _number(row.get("tenor_months"), "int", "%s, Curve" % path)
        rate = _number(row.get("rate"), "float", "%s, Curve" % path)
        if tenor is not None and rate is not None:
            raw["curve"][str(tenor)] = rate
    raw["indexes"] = {}
    for row in xlsx.table(book.get("Indexes", [])):
        name = xlsx.as_text(row.get("index"))
        if name:
            raw["indexes"][name] = {"tenor_months": _number(row.get("tenor_months"), "int", "%s, Indexes" % path),
                                    "spread": _number(row.get("spread"), "float", "%s, Indexes" % path) or 0.0}
    raw["products"] = {}
    types = {f.name: f.type for f in dataclasses.fields(Product)}
    products = xlsx.table(book["Products"])
    # A misspelled header is refused even when its cells are empty: someone
    # meant to set something that would otherwise be silently ignored.
    header = [xlsx.as_text(h) for h in next((r for r in book["Products"] if any(v not in (None, "") for v in r)), [])]
    unknown = [h for h in header if h and h != "product" and h not in types]
    if unknown:
        raise InputError("%s, Products: unknown column %r" % (path, unknown[0]))
    for row in products:
        name = xlsx.as_text(row.get("product"))
        if not name:
            continue
        spec = {}
        for field, value in row.items():
            if field == "product" or value in (None, ""):
                continue
            if field not in types:
                raise InputError("%s, Products: unknown column %r" % (path, field))
            where = "%s, Products, %s, %s" % (path, name, field)
            kind = types[field]
            if kind in (bool, "bool"):
                spec[field] = _number(value, "bool", where)
            elif kind in (int, "int"):
                spec[field] = _number(value, "int", where)
            elif kind in (str, "str"):
                spec[field] = xlsx.as_text(value)
            else:
                spec[field] = _number(value, "float", where)
        raw["products"][name] = spec
    raw["extra_scenarios"] = []
    for row in xlsx.table(book.get("Scenarios", [])):
        name = xlsx.as_text(row.get("name"))
        if not name:
            continue
        spec = {"name": name}
        where = "%s, Scenarios, %s" % (path, name)
        if row.get("shock_bp") not in (None, ""):
            spec["shock_bp"] = _number(row["shock_bp"], "float", where)
        if row.get("ramp_months") not in (None, ""):
            spec["ramp_months"] = _number(row["ramp_months"], "int", where)
        if row.get("shape") not in (None, ""):
            spec["shape"] = _shape(row["shape"], where)
        raw["extra_scenarios"].append(spec)
    raw["liquidity"] = {"contingent": [
        {"name": xlsx.as_text(r.get("name")), "capacity": _number(r.get("capacity"), "float", "%s, Contingent" % path)}
        for r in xlsx.table(book.get("Contingent", [])) if xlsx.as_text(r.get("name"))]}
    if "stress_months" in raw:
        raw["liquidity"]["stress_months"] = raw.pop("stress_months")
    if "Limits" in book:
        from keel.model import LIMITS
        kinds = {k for k, _, _, _ in LIMITS} | {"warning_band"}
        raw["limits"] = {}
        for r in xlsx.table(book["Limits"]):
            key = xlsx.as_text(r.get("key"))
            if not key:
                continue
            if key not in kinds:
                raise InputError("%s, Limits: unknown key %r" % (path, key))
            value = _number(r.get("value"), "float", "%s, Limits, %s" % (path, key))
            if value is not None:
                raw["limits"][key] = value
    raw["notes"] = {xlsx.as_text(r.get("key")): xlsx.as_text(r.get("text"))
                    for r in xlsx.table(book.get("Notes", [])) if xlsx.as_text(r.get("key"))}
    for key in ("as_of",):
        if not raw.get(key):
            raise InputError("%s, Settings: %s is required" % (path, key))
    return raw


def to_workbook(raw):
    """The raw settings as workbook sheets."""
    notes = {k: n for k, _, n in SETTINGS}
    settings = [["key", "value", "note"]]
    liquidity = raw.get("liquidity", {})
    for key, _, _ in SETTINGS:
        value = liquidity.get(key) if key == "stress_months" else raw.get(key)
        if value is not None:
            settings.append([key, value, notes[key]])
    curve = [["tenor_months", "rate"]] + [[int(t), r] for t, r in sorted(raw["curve"].items(), key=lambda x: float(x[0]))]
    indexes = [["index", "tenor_months", "spread"]] + [
        [n, v["tenor_months"], v.get("spread", 0.0)] for n, v in raw.get("indexes", {}).items()]
    used = [f for f in PRODUCT_FIELDS if any(f in spec for spec in raw["products"].values())]
    products = [["product"] + used] + [[name] + [spec.get(f) for f in used]
                                       for name, spec in raw["products"].items()]
    scenarios = [["name", "shock_bp", "ramp_months", "shape"]] + [
        [s["name"], s.get("shock_bp"), s.get("ramp_months"),
         ", ".join("%s:%g" % (t, bp) for t, bp in sorted(s["shape"].items(), key=lambda x: float(x[0])))
         if s.get("shape") else None] for s in raw.get("extra_scenarios", [])]
    contingent = [["name", "capacity"]] + [[c["name"], c["capacity"]] for c in liquidity.get("contingent", [])]
    from keel.model import LIMITS
    set_limits = raw.get("limits", {})
    limits = [["key", "value", "note"]]
    for key, kind, default, label in LIMITS:
        value = set_limits.get(key)
        limits.append([key, value, "%s (%s). Blank uses the default, %g." % (
            label, "at most" if kind == "max" else "at least", default)])
    limits.append(["warning_band", set_limits.get("warning_band"),
                   "How close to a limit counts as near it, percent of the limit. Default 10."])
    notes_sheet = [["key", "text"]] + [[k, v] for k, v in raw.get("notes", {}).items()]
    return dict(zip(SHEETS, (settings, curve, indexes, products, scenarios, contingent, limits, notes_sheet)))


def convert(source, target):
    raw = load(source)
    if target.lower().endswith(".xlsx"):
        xlsx.write_workbook(target, to_workbook(raw))
    else:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2)
    return raw
