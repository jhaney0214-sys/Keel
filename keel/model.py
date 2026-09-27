"""What the credit union gives Keel: its positions and its assumptions.

Two files, both plain text so a validator can read every input:

positions.csv, one row per instrument or pool:
    id, name, product, side (asset | liability), balance, rate (annual %),
    rate_type (fixed | variable | administered | none), index, margin,
    reset_months, term_months (remaining), amortization
    (level | bullet | nonmaturity | none), floor, cap

assumptions.json: the curve, each product's behaviour (prepayment, decay,
beta, new-business terms, growth), income and expense, and the liquidity
stress. Everything the projection does that is not arithmetic on the
positions comes from this file, so nothing is buried in the code.
"""

import dataclasses
import json
import os

from keel.curve import Curve, Scenario, standard_scenarios

SIDES = ("asset", "liability")
RATE_TYPES = ("fixed", "variable", "administered", "none")
AMORTIZATIONS = ("level", "bullet", "balloon", "callable", "nonmaturity", "none")


class InputError(ValueError):
    """An input that would make the projection wrong rather than merely odd."""


@dataclasses.dataclass
class Position:
    id: str
    name: str
    product: str
    side: str
    balance: float
    rate: float                 # annual, as a decimal
    rate_type: str
    index: str = ""
    margin: float = 0.0         # decimal
    reset_months: int = 0
    term_months: int = 0        # remaining; 0 for no maturity
    amortization: str = "none"
    floor: float = None         # decimal
    cap: float = None           # decimal
    amort_months: int = 0       # balloon: remaining amortization period, longer than the term
    call_months: int = 0        # callable: months until it can first be called
    next_reset_months: int = 0  # variable: months to the first reset (default reset_months)
    age: int = 0                # months since the analysis date
    new_business: bool = False

    def copy(self):
        return dataclasses.replace(self)


def _pct(text, default=None):
    text = (text or "").strip()
    return default if text == "" else float(text) / 100.0


def _int(text, default=0):
    text = (text or "").strip()
    return default if text == "" else int(float(text))


def read_positions(path):
    """Positions from positions.csv or positions.xlsx."""
    from keel import tables
    positions, seen = [], set()
    for line, row in enumerate(tables.read_table(path), 2):
        where = "%s line %d" % (os.path.basename(path), line)
        p = Position(
            id=row["id"].strip(), name=row["name"].strip(), product=row["product"].strip(),
            side=row["side"].strip().lower(), balance=float(row["balance"]),
            rate=_pct(row.get("rate"), 0.0), rate_type=row["rate_type"].strip().lower(),
            index=(row.get("index") or "").strip(), margin=_pct(row.get("margin"), 0.0),
            reset_months=_int(row.get("reset_months")), term_months=_int(row.get("term_months")),
            amortization=row["amortization"].strip().lower(),
            floor=_pct(row.get("floor")), cap=_pct(row.get("cap")),
            amort_months=_int(row.get("amort_months")), call_months=_int(row.get("call_months")),
            next_reset_months=_int(row.get("next_reset_months")))
        if p.id in seen:
            raise InputError("%s: id %r appears twice" % (where, p.id))
        seen.add(p.id)
        if p.side not in SIDES:
            raise InputError("%s: side must be asset or liability, not %r" % (where, p.side))
        if p.rate_type not in RATE_TYPES:
            raise InputError("%s: unknown rate_type %r" % (where, p.rate_type))
        if p.amortization not in AMORTIZATIONS:
            raise InputError("%s: unknown amortization %r" % (where, p.amortization))
        if p.balance < 0 and not (p.rate_type == "none" and p.amortization == "none"):
            raise InputError("%s: only a non-earning contra account (an allowance) may be negative" % where)
        if p.amortization in ("level", "bullet", "balloon", "callable") and p.term_months <= 0:
            raise InputError("%s: a %s position needs term_months" % (where, p.amortization))
        if p.amortization == "balloon" and p.amort_months <= p.term_months:
            raise InputError("%s: a balloon needs amort_months longer than term_months" % where)
        if p.rate_type == "variable" and (not p.index or p.reset_months <= 0):
            raise InputError("%s: a variable rate needs an index and reset_months" % where)
        positions.append(p)
    return positions


@dataclasses.dataclass
class Product:
    """How one product behaves. Rates and speeds are annual decimals."""
    name: str
    cpr: float = 0.0              # prepayment, level-amortizing products
    cpr_per_100bp: float = 0.0    # CPR added per 100bp *fall* in rates
    cpr_floor: float = 0.0
    cpr_cap: float = 0.6
    runoff: float = 0.0           # annual decay, non-maturity products
    runoff_per_100bp: float = 0.0 # decay added per 100bp *rise* in rates
    beta: float = 0.0             # share of a market move an administered rate follows
    rate_floor: float = 0.0
    new_term: int = 0             # months, for new business
    new_amortization: str = ""
    new_amort_term: int = 0       # balloon new business: amortization period
    new_call_months: int = 12     # callable new business: months to first call
    call_threshold: float = 0.0   # callable: called when coupon exceeds the market by this
    spread: float = 0.0           # new-business rate over the curve at new_term
    discount_spread: float = 0.0  # NEV discount rate over the curve
    growth: float = 0.0           # FP&A plan: annual balance growth
    charge_off: float = 0.0       # annual net charge-offs, loans
    liquid: bool = False          # an investment that can be sold or pledged
    haircut: float = 0.0          # its liquidity-stress haircut
    stress_runoff: float = 0.0    # extra share of balance lost over the stress period
    # Profitability, capital and pricing. None means "use the rule": see
    # profitability.risk_weight.
    risk_weight: float = None     # capital allocated per dollar, before the target ratio
    servicing_cost: float = 0.0   # annual operating cost, share of balance
    fee_yield: float = 0.0        # annual fee income, share of balance
    origination_cost: float = 0.0 # one-time cost of new business, share of the amount
    collateral_value: float = 0.0 # share of the balance a secured lender (FHLB) lends against


def _decimal(value):
    return float(value) / 100.0


#: Every Product field entered in percent. Beta was missing the first time
#: this ran, so a 10% beta read as 1000% and a +100bp shock paid regular
#: shares 10.1%; `test_every_percent_field_is_converted` now guards the list.
PERCENT_FIELDS = ("cpr", "cpr_per_100bp", "cpr_floor", "cpr_cap", "runoff", "runoff_per_100bp",
                  "beta", "rate_floor", "spread", "discount_spread", "growth", "charge_off",
                  "haircut", "stress_runoff", "call_threshold", "risk_weight", "servicing_cost",
                  "fee_yield", "origination_cost", "collateral_value")


#: Policy limits: (key, kind, default, label). "max" limits cap a measure,
#: "min" limits floor it; values in percent except months. Defaults are
#: typical of credit-union ALM policies and are reported as defaults, never
#: as the board's own, until the settings say otherwise.
LIMITS = (
    ("nii_decline_300", "max", 15.0, "Year-one NII decline, worst of +/-300bp"),
    ("nii_decline_200", "max", 10.0, "Year-one NII decline, worst of +/-200bp"),
    ("nev_decline_300", "max", 40.0, "NEV decline, own assumptions, worst of +/-300bp"),
    ("nev_ratio_min", "min", 6.0, "NEV ratio, own assumptions, after the worst +/-300bp"),
    ("net_worth_min", "min", 7.0, "Net worth ratio, lowest month of the base plan"),
    ("liquid_to_shares_min", "min", 15.0, "Cash and liquid investments to shares"),
    ("loans_to_shares_max", "max", 95.0, "Loans to shares"),
    ("borrowings_to_assets_max", "max", 25.0, "Borrowings to assets"),
    ("survival_months_min", "min", 6.0, "Months of liquidity under the stress"),
    ("capital_to_rwa_min", "min", 10.0, "Net worth to risk-weighted assets"),
    ("uninsured_shares_max", "max", 15.0, "Uninsured shares, estimated"),
)
INSTITUTIONS = ("credit_union", "bank")
WARNING_BAND = 10.0   # percent of a limit counted as "near" it


@dataclasses.dataclass
class Assumptions:
    as_of: str
    curve: Curve
    indexes: dict               # index name -> (tenor months, spread decimal)
    products: dict              # product name -> Product
    horizon_months: int = 60
    nev_max_months: int = 360
    rate_floor: float = 0.0     # scenario rates are floored here (percent)
    short_tenor: int = 1        # the curve point administered rates follow
    fee_income: float = 0.0     # annual
    operating_expense: float = 0.0
    expense_growth: float = 0.0
    cash_minimum: float = 0.0
    overnight_spread: float = 0.0
    stress_months: int = 3
    contingent: list = dataclasses.field(default_factory=list)   # [(name, capacity)]
    scenarios: list = dataclasses.field(default_factory=list)
    notes: dict = dataclasses.field(default_factory=dict)
    limits: dict = dataclasses.field(default_factory=dict)       # key -> value, as set
    warning_band: float = WARNING_BAND
    institution: str = "credit_union"
    tax_rate: float = 0.0       # decimal; credit unions are exempt
    target_capital: float = 0.10  # capital held per dollar of risk-weighted assets, for allocation
    hurdle_rate: float = 0.12   # the return on allocated capital pricing aims for (RAROC)
    base_case: str = "flat"     # flat | forward | forecast: where rates go in the plan
    rate_forecast: list = dataclasses.field(default_factory=list)   # [(month, tenor, rate %)]
    path: object = None         # the RatePath the plan follows, or None for flat
    drivers: dict = dataclasses.field(default_factory=dict)   # product -> {month: {volume, balance, rate}}
    noninterest: list = dataclasses.field(default_factory=list)   # [NonInterest]
    secured: frozenset = frozenset()   # contingent sources capped by pledgeable collateral
    stresses: list = dataclasses.field(default_factory=list)      # [liquidity.Stress]


@dataclasses.dataclass
class NonInterest:
    """One line of fee income or operating expense in the budget."""
    line: str
    kind: str                   # income | expense
    annual: float
    growth: float = 0.0         # decimal, applied each plan year
    start_month: int = 1        # a new hire or branch starts part-way through

    def month(self, t):
        if t < self.start_month:
            return 0.0
        return self.annual / 12.0 * (1.0 + self.growth) ** ((t - 1) // 12)


def month_index(value, as_of, where):
    """A plan month (1 = the month after the analysis date) from a number or a YYYY-MM."""
    text = str(value).strip()
    if len(text) >= 7 and text[4] == "-":
        y, m = int(text[:4]), int(text[5:7])
        ay, am = int(as_of[:4]), int(as_of[5:7])
        n = (y - ay) * 12 + (m - am)
    else:
        try:
            n = int(float(text))
        except ValueError:
            raise InputError("%s: month %r is neither a number nor YYYY-MM" % (where, value))
    if n < 1:
        raise InputError("%s: month %r is not after the analysis date" % (where, value))
    return n


def _drivers(rows, as_of, products):
    out = {}
    for n, row in enumerate(rows, 1):
        where = "assumptions: driver %d" % n
        product = row.get("product")
        if product not in products:
            raise InputError("%s: product %r is not in the products" % (where, product))
        month = month_index(row.get("month"), as_of, where)
        plan = out.setdefault(product, {}).setdefault(month, {})
        for key in ("volume", "balance", "rate"):
            value = row.get(key)
            if value not in (None, ""):
                plan[key] = float(value) / 100.0 if key == "rate" else float(value)
        if "volume" in plan and "balance" in plan:
            raise InputError("%s: give a volume or a balance for %s in month %d, not both" % (where, product, month))
    return out


def _noninterest(rows, as_of):
    out = []
    for n, row in enumerate(rows, 1):
        kind = str(row.get("kind", "expense")).strip().lower()
        if kind not in ("income", "expense"):
            raise InputError("assumptions: non-interest line %d: kind must be income or expense" % n)
        out.append(NonInterest(line=str(row.get("line") or "line %d" % n), kind=kind, annual=float(row["annual"]),
                               growth=_decimal(row.get("growth") or 0),
                               start_month=month_index(row.get("start_month") or 1, as_of,
                                                       "assumptions: non-interest line %d" % n)))
    return out


def read_assumptions(path):
    """Settings from assumptions.xlsx or assumptions.json."""
    from keel import settings
    return parse_assumptions(settings.load(path))


def parse_assumptions(raw):
    """Assumptions from the parsed JSON, so a what-if can change the raw
    values (in the same units as the file) before they are read."""
    products = {}
    for name, spec in raw["products"].items():
        values = {}
        for key, value in spec.items():
            if key not in {f.name for f in dataclasses.fields(Product)}:
                raise InputError("assumptions: product %s has an unknown field %r" % (name, key))
            values[key] = _decimal(value) if key in PERCENT_FIELDS else value
        products[name] = Product(name=name, **values)
    indexes = {k: (int(v["tenor_months"]), _decimal(v.get("spread", 0))) for k, v in raw["indexes"].items()}
    floor = raw.get("rate_floor", 0.0)
    scenarios = standard_scenarios(floor)
    for spec in raw.get("extra_scenarios", []):
        scenarios.append(Scenario(spec["name"], spec.get("shock_bp", 0), spec.get("ramp_months", 0), floor,
                                  spec.get("shape")))
    liquidity = raw.get("liquidity", {})
    institution = raw.get("institution", "credit_union")
    if institution not in INSTITUTIONS:
        raise InputError("assumptions: institution must be credit_union or bank, not %r" % institution)
    tax_default = 21.0 if institution == "bank" else 0.0
    base_case = raw.get("base_case", "flat") or "flat"
    forecast = [(month_index(r["month"], raw["as_of"], "rate forecast"), float(r["tenor_months"]), float(r["rate"]))
                for r in raw.get("rate_forecast", [])]
    path = None
    if base_case != "flat":
        from keel.curve import RatePath
        try:
            path = RatePath(base_case, Curve(raw["curve"]), forecast)
        except ValueError as error:
            raise InputError("assumptions: %s" % error)
    noninterest = _noninterest(raw.get("noninterest", []), raw["as_of"])
    fee_income = float(raw.get("fee_income", 0))
    operating_expense = float(raw.get("operating_expense", 0))
    if noninterest:
        # Itemized lines replace the two totals; the totals are kept for the
        # run-rate views (profitability) as the lines' first-year sums.
        fee_income = sum(x.annual for x in noninterest if x.kind == "income")
        operating_expense = sum(x.annual for x in noninterest if x.kind == "expense")
    if base_case != "flat":
        scenarios.append(Scenario("rates unchanged", 0, 0, floor, use_path=False))
    return Assumptions(
        base_case=base_case, rate_forecast=forecast, path=path,
        drivers=_drivers(raw.get("drivers", []), raw["as_of"], products), noninterest=noninterest,
        institution=institution, tax_rate=_decimal(raw.get("tax_rate", tax_default)),
        target_capital=_decimal(raw.get("target_capital", 10.0)),
        hurdle_rate=_decimal(raw.get("hurdle_rate", 12.0)),
        as_of=raw["as_of"], curve=Curve(raw["curve"]), indexes=indexes, products=products,
        horizon_months=int(raw.get("horizon_months", 60)),
        nev_max_months=int(raw.get("nev_max_months", 360)), rate_floor=floor,
        short_tenor=int(raw.get("short_tenor_months", 1)),
        fee_income=fee_income, operating_expense=operating_expense,
        expense_growth=_decimal(raw.get("expense_growth", 0)),
        cash_minimum=float(raw.get("cash_minimum", 0)),
        overnight_spread=_decimal(raw.get("overnight_spread", 0)),
        stress_months=int(liquidity.get("stress_months", 3)),
        contingent=[(c["name"], float(c["capacity"])) for c in liquidity.get("contingent", [])],
        secured=frozenset(c["name"] for c in liquidity.get("contingent", []) if _truthy(c.get("secured"))),
        stresses=_stresses(raw.get("liquidity_stresses") or liquidity.get("stresses")),
        scenarios=scenarios, notes=raw.get("notes", {}),
        limits=_limits(raw.get("limits", {})),
        warning_band=float(raw.get("limits", {}).get("warning_band", WARNING_BAND)))


def _truthy(value):
    return str(value).strip().lower() in ("true", "yes", "y", "1", "x") if value not in (None, "") else False


def _stresses(raw_list):
    from keel import liquidity
    return liquidity.scenarios(raw_list)


def _limits(raw):
    known = {k for k, _, _, _ in LIMITS} | {"warning_band"}
    unknown = [k for k in raw if k not in known]
    if unknown:
        raise InputError("assumptions: unknown limit %r (known: %s)" % (unknown[0], ", ".join(sorted(known))))
    return {k: float(v) for k, v in raw.items() if k != "warning_band" and v is not None}


def check(positions, assumptions):
    """Every product a position uses must be described, and every index priced."""
    for p in positions:
        if p.product not in assumptions.products:
            raise InputError("position %s: product %r has no assumptions" % (p.id, p.product))
        if p.rate_type == "variable" and p.index not in assumptions.indexes:
            raise InputError("position %s: index %r is not in assumptions" % (p.id, p.index))
