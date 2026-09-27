"""The RAROC pricing calculator: what a loan (or a deposit) earns over its
life, and the rate it needs.

    python -m keel price <folder> --product new_auto --amount 30000 --term 60 --rate 6.25

A deal is priced on the institution's own assumptions: its product's
prepayment or decay, charge-offs, servicing cost, fees and capital weight,
any of which the deal can override. The deal is run month by month through
the same engine as the book, on the base curve, and everything is measured
over its whole life, per dollar of average balance:

    yield                     interest earned / balance-years
  - FTP                       strip-funded cost of its own cash flows
  = spread
  + capital credit            its allocated capital, credited at FTP
  + fees                      upfront fee spread over the life, plus fee yield
  - origination cost          spread over the life
  - servicing cost
  - expected loss             its charge-offs
  = pre-tax
  - tax
  = net;  RAROC = net / allocated capital

The calculator solves for the rate that earns the hurdle RAROC and the rate
that breaks even, and shows RAROC across a band of rates. A deposit has no
capital, so it is priced on spread: the highest rate it can pay and still
cover its costs.
"""

import dataclasses

from keel import engine, profitability
from keel.model import InputError, Position


@dataclasses.dataclass
class Deal:
    product: str
    amount: float
    term_months: int = 0
    rate: float = None               # decimal; None prices at the hurdle
    side: str = None                 # default: the product's side in the book
    amortization: str = None         # default: the product's new-business amortization
    amort_months: int = 0            # balloon amortization period
    rate_type: str = "fixed"         # fixed | variable | administered
    reset_months: int = 0
    upfront_fee: float = 0.0         # share of the amount, collected at origination
    # Product overrides (decimals); None keeps the product's own.
    cpr: float = None
    runoff: float = None
    charge_off: float = None
    servicing_cost: float = None
    fee_yield: float = None
    origination_cost: float = None
    risk_weight: float = None


OVERRIDES = ("cpr", "runoff", "charge_off", "servicing_cost", "fee_yield", "origination_cost", "risk_weight")


def _spec(deal, a):
    if deal.product not in a.products:
        raise InputError("pricing: no product %r in the settings" % deal.product)
    spec = a.products[deal.product]
    changes = {k: getattr(deal, k) for k in OVERRIDES if getattr(deal, k) is not None}
    return dataclasses.replace(spec, **changes)


def _life(deal, spec, side, a, rate):
    """Monthly flows of the deal at `rate`, and the balance at each month start."""
    amortization = deal.amortization or spec.new_amortization or ("nonmaturity" if deal.term_months == 0
                                                                  else "level")
    if amortization in ("level", "bullet", "balloon") and deal.term_months <= 0:
        raise InputError("pricing: a %s deal needs a term" % amortization)
    p = Position(id="deal", name="deal", product=deal.product, side=side, balance=deal.amount, rate=rate,
                 rate_type="administered" if amortization == "nonmaturity" and deal.rate_type == "fixed"
                 else deal.rate_type, term_months=deal.term_months, amortization=amortization,
                 amort_months=deal.amort_months or (max(deal.term_months * 2, 300) if amortization == "balloon" else 0),
                 reset_months=deal.reset_months, index="")
    if p.rate_type == "variable":
        # Priced as a floating deal: its rate is held, and it is funded to its reset.
        p.rate_type = "fixed"
    products = dict(a.products)
    products[deal.product] = spec
    b = dataclasses.replace(a, products=products)
    stepper = engine.Stepper(b, b.scenarios[0])
    flows, starts = [], []
    months = deal.term_months or a.nev_max_months
    for month in range(1, months + 1):
        starts.append(p.balance)
        flows.append(stepper.step(p, month))
        if p.balance <= 0.005:
            break
    if p.balance > 0.005:
        flows[-1].principal += p.balance
    return flows, starts, amortization


def economics(deal, a, rate=None):
    """Every line of the deal's life economics at `rate` (default the
    deal's), per dollar of average balance, as a dict."""
    spec = _spec(deal, a)
    side = deal.side or "asset"
    rate = deal.rate if rate is None else rate
    if rate is None:
        raise InputError("pricing: give a rate, or solve for one")
    flows, starts, amortization = _life(deal, spec, side, a, rate)
    balance_years = sum(starts) / 12.0
    if balance_years <= 0:
        raise InputError("pricing: the deal has no balance to price")
    interest = sum(f.interest for f in flows) / balance_years
    if deal.rate_type == "variable" and deal.reset_months:
        ftp = a.curve.rate(deal.reset_months) / 100.0
    else:
        ftp = profitability.strip_rate(flows, a.curve)
    losses = sum(f.chargeoff for f in flows) / balance_years
    capital = profitability.risk_weight(deal.product, spec, side) * a.target_capital if side == "asset" else 0.0
    upfront = deal.upfront_fee * deal.amount / balance_years
    origination = spec.origination_cost * deal.amount / balance_years
    spread = interest - ftp if side == "asset" else ftp - interest
    out = {"rate": rate, "side": side, "amortization": amortization, "yield": interest, "ftp": ftp,
           "spread": spread, "capital_credit": capital * ftp, "fees": upfront + spec.fee_yield,
           "origination": origination, "servicing": spec.servicing_cost, "expected_loss": losses,
           "capital": capital, "average_life": balance_years / deal.amount,
           "balance_years": balance_years}
    out["pre_tax"] = (spread + out["capital_credit"] + out["fees"] - origination - spec.servicing_cost - losses)
    out["tax"] = out["pre_tax"] * a.tax_rate
    out["net"] = out["pre_tax"] - out["tax"]
    out["raroc"] = out["net"] / capital if capital > 0 else None
    out["lifetime_net"] = out["net"] * balance_years
    return out


def side_of(product, positions):
    """The side a product sits on in the book (assets unless the book says otherwise)."""
    return next((p.side for p in positions if p.product == product), "asset")


def solve(deal, a, target, measure):
    """The rate at which `measure` ("raroc" or "pre_tax") equals `target`.
    Assets need a higher rate to earn more; deposits a lower one."""
    lo, hi = -0.05, 0.60
    f = lambda r: _value(economics(deal, a, r), measure) - target  # noqa: E731
    f_lo, f_hi = f(lo), f(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if (f_mid > 0) == (f_hi > 0):
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _value(e, measure):
    v = e[measure]
    return -1e9 if v is None else v


def quote(deal, a):
    """The full quote: economics at the offered rate (or the hurdle rate), the
    hurdle and break-even rates, and RAROC across rates around it."""
    spec = _spec(deal, a)
    side = deal.side or "asset"
    deal = dataclasses.replace(deal, side=side)
    breakeven = solve(deal, a, 0.0, "pre_tax")
    hurdle = solve(deal, a, a.hurdle_rate, "raroc") if side == "asset" else None
    rate = deal.rate if deal.rate is not None else (hurdle if hurdle is not None else breakeven)
    if rate is None:
        raise InputError("pricing: no rate between -5% and 60% earns the hurdle; check the deal")
    at = economics(deal, a, rate)
    band = []
    for bp in (-50, -25, 0, 25, 50):
        e = economics(deal, a, rate + bp / 10000.0)
        band.append({"bp": bp, "rate": e["rate"], "spread": e["spread"], "net": e["net"], "raroc": e["raroc"]})
    return {"deal": deal, "at": at, "hurdle_rate": hurdle, "breakeven_rate": breakeven, "band": band,
            "hurdle": a.hurdle_rate, "target_capital": a.target_capital, "tax_rate": a.tax_rate,
            "offered": deal.rate is not None}


LINES = (("Yield", "yield", 1), ("Funds transfer price", "ftp", -1), ("Spread over FTP", "spread", 0),
         ("Capital credit", "capital_credit", 1), ("Fees", "fees", 1), ("Origination cost", "origination", -1),
         ("Servicing cost", "servicing", -1), ("Expected loss", "expected_loss", -1),
         ("Pre-tax income", "pre_tax", 0), ("Tax", "tax", -1), ("Net income", "net", 0))


def text(q):
    """The quote as plain text, for the command line."""
    e, d = q["at"], q["deal"]
    out = ["%s %s, %s, %s months, %s" % (d.product.replace("_", " "), "loan" if e["side"] == "asset" else "deposit",
                                         "{:,.0f}".format(d.amount), d.term_months or "no fixed",
                                         e["amortization"]),
           "average life %.1f years; allocated capital %.2f%% of balance" % (e["average_life"], 100 * e["capital"]),
           ""]
    label = "at the offered rate" if q["offered"] else "at the hurdle rate"
    out.append("%-26s %9s   (%% of average balance, %s %.2f%%)" % ("", "", label, 100 * e["rate"]))
    for name, key, sign in LINES:
        if e["side"] == "liability" and key == "ftp":
            name = "Funds transfer credit"
        if e["side"] == "liability" and key == "yield":
            name = "Rate paid"
        out.append("%-26s %9.2f%%" % (name, 100 * e[key]))
    out.append("")
    out.append("RAROC %s   (hurdle %.1f%%)" % ("n/a (no capital)" if e["raroc"] is None else "%.1f%%" % (100 * e["raroc"]),
                                            100 * q["hurdle"]))
    if q["hurdle_rate"] is not None:
        out.append("rate for the hurdle      %.2f%%" % (100 * q["hurdle_rate"]))
    if q["breakeven_rate"] is not None:
        out.append("%-24s %.2f%%" % ("break-even rate" if e["side"] == "asset" else "highest rate that covers costs",
                                     100 * q["breakeven_rate"]))
    out.append("lifetime net income      %s" % "{:,.0f}".format(e["lifetime_net"]))
    return "\n".join(out)
