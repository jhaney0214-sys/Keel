"""Funds transfer pricing, product capital and product profitability.

**Funds transfer pricing (FTP)** charges every asset for the money it uses,
and credits every liability for the money it provides, at the rate the
market would charge for money of the same shape. That rate is matched to
each position's own cash flows. It is the base curve read at each month a
principal payment falls due, weighted by the payment times the months it is
outstanding (the strip-funding method), so a mortgage that prepays is funded
shorter than its contract says. A variable-rate position is funded to its
next reset, and cash at the short rate. What is left, the difference between
what the book earns on the curve and what it pays, is **treasury's** margin:
the reward (or cost) of the balance sheet's rate mismatch, which is exactly
what ALM manages. Every product's spread plus treasury's margin is the
book's net interest income, and a reconciliation check says so.

**Product capital** is each product's risk-weighted balance times the
target capital ratio. A product's own weight comes from the settings;
without one, loans and other assets weigh 100%, liquid investments 20%,
and cash and liabilities nothing. The allocated capital itself funds part
of the product, so the product is credited for it at its FTP rate.

**Profitability** is annualized on today's balances (a run-rate, not the
plan): spread over FTP, the capital credit, fee income, servicing cost and
expected loss (the product's charge-off rate), then tax. Return on
allocated capital is RAROC. Fees and expenses the products do not carry
are shown as unallocated, so the run-rate adds up to the institution's.
"""

import dataclasses

from keel import engine
from keel.engine import CASH


def risk_weight(product, spec, side):
    """A product's capital weight: its own if set, else the rule above."""
    if spec.risk_weight is not None:
        return spec.risk_weight
    if side == "liability" or product == CASH:
        return 0.0
    if spec.liquid:
        return 0.20
    return 1.0


def ftp_rates(positions, a):
    """{position id: annual FTP rate as a decimal}, or None for a position
    with neither a rate nor a term (fixed assets, stock, the allowance)."""
    base = a.scenarios[0]
    flows = engine.runoff(positions, a, base)
    short = a.curve.rate(a.short_tenor) / 100.0
    out = {}
    for p in positions:
        if p.product == CASH:
            out[p.id] = short
        elif p.rate_type == "variable":
            out[p.id] = a.curve.rate(p.next_reset_months or p.reset_months or 1) / 100.0
        elif p.id in flows:
            out[p.id] = strip_rate(flows[p.id], a.curve)
        else:
            out[p.id] = None
    return out


def strip_rate(flows, curve, start=0):
    """The strip-funding rate of a run of monthly flows: each principal
    payment funded to its own month, weighted by amount times months
    outstanding. `start` offsets the curve when the flows begin later."""
    weight = value = 0.0
    for k, f in enumerate(flows, 1):
        amount = f.principal + f.chargeoff
        weight += amount * k
        value += amount * k * curve.rate(k) / 100.0
    return value / weight if weight else curve.rate(1) / 100.0


def rwa(positions, a):
    """Risk-weighted assets. A negative balance (the allowance) is left out
    rather than netted, which is how the capital rules treat it."""
    return sum(max(p.balance, 0.0) * risk_weight(p.product, a.products[p.product], p.side)
               for p in positions if p.side == "asset")


@dataclasses.dataclass
class Line:
    product: str
    side: str
    count: int = 0
    balance: float = 0.0
    interest: float = 0.0          # earned (assets) or paid (liabilities), annual
    ftp: float = 0.0               # FTP charge (assets) or credit (liabilities), annual
    fees: float = 0.0
    servicing: float = 0.0
    expected_loss: float = 0.0
    rwa: float = 0.0
    capital: float = 0.0
    capital_credit: float = 0.0
    tax: float = 0.0

    @property
    def spread(self):
        """Spread income over FTP, in dollars."""
        return self.interest - self.ftp if self.side == "asset" else self.ftp - self.interest

    @property
    def pre_tax(self):
        return self.spread + self.capital_credit + self.fees - self.servicing - self.expected_loss

    @property
    def net(self):
        return self.pre_tax - self.tax

    def rate(self, value):
        return value / self.balance if self.balance else 0.0

    @property
    def raroc(self):
        return self.net / self.capital if self.capital > 0 else None


def product_lines(positions, a):
    """([Line] per product, the treasury margin, a dict of totals)."""
    ftp = ftp_rates(positions, a)
    short = a.curve.rate(a.short_tenor) / 100.0
    lines = {}
    for p in positions:
        spec = a.products[p.product]
        line = lines.setdefault(p.product, Line(p.product, p.side))
        rate = short if p.product == CASH else p.rate
        f = ftp[p.id]
        line.count += 1
        line.balance += p.balance
        line.interest += p.balance * rate
        # A position with no rate and no term is funded at nothing and earns
        # nothing: it neither pays nor is paid by treasury.
        line.ftp += p.balance * (f if f is not None else 0.0)
        line.fees += p.balance * spec.fee_yield
        line.servicing += p.balance * spec.servicing_cost
        if p.side == "asset":
            line.expected_loss += max(p.balance, 0.0) * spec.charge_off
            weighted = max(p.balance, 0.0) * risk_weight(p.product, spec, p.side)
            line.rwa += weighted
            line.capital += weighted * a.target_capital
            line.capital_credit += weighted * a.target_capital * (f if f is not None else 0.0)
    for line in lines.values():
        line.tax = line.pre_tax * a.tax_rate
    ordered = sorted(lines.values(), key=lambda x: (x.side != "asset", -x.balance))
    asset_ftp = sum(x.ftp for x in ordered if x.side == "asset")
    liability_ftp = sum(x.ftp for x in ordered if x.side == "liability")
    capital_credit = sum(x.capital_credit for x in ordered)
    # Treasury buys the assets' funding and sells the liabilities' at FTP,
    # and pays the products' capital credit: what remains is its margin.
    treasury = asset_ftp - liability_ftp - capital_credit
    nii = sum(x.interest for x in ordered if x.side == "asset") - sum(x.interest for x in ordered if x.side == "liability")
    fees = sum(x.fees for x in ordered)
    servicing = sum(x.servicing for x in ordered)
    expected_loss = sum(x.expected_loss for x in ordered)
    unallocated_fees = a.fee_income - fees
    unallocated_expense = a.operating_expense - servicing
    pre_tax = (sum(x.spread + x.capital_credit for x in ordered) + treasury + a.fee_income
               - a.operating_expense - expected_loss)
    totals = {"nii": nii, "treasury": treasury, "product_spread": sum(x.spread for x in ordered),
              "capital_credit": capital_credit, "fees": fees, "servicing": servicing,
              "unallocated_fees": unallocated_fees, "unallocated_expense": unallocated_expense,
              "expected_loss": expected_loss, "pre_tax": pre_tax, "tax": pre_tax * a.tax_rate,
              "net": pre_tax * (1.0 - a.tax_rate), "rwa": sum(x.rwa for x in ordered),
              "capital": sum(x.capital for x in ordered)}
    return ordered, treasury, totals


def check_ftp(lines, totals):
    """Products' spreads plus treasury's margin are net interest income."""
    from keel.measures import Check
    parts = totals["product_spread"] + totals["capital_credit"] + totals["treasury"]
    return Check("Product spreads over FTP plus treasury's margin are net interest income",
                 abs(parts - totals["nii"]) < 1.0,
                 "spreads and treasury $%s, run-rate NII $%s" % ("{:,.0f}".format(parts),
                                                                 "{:,.0f}".format(totals["nii"])))
