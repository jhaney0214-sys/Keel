"""The one projection that ALM, FP&A and liquidity all read.

Monthly steps. In each month every position reprices (variable rates at their
reset, administered share rates by their beta to the short rate), accrues
interest, and pays principal: scheduled amortization or a bullet at maturity,
prepayment on level-amortizing products, decay on non-maturity products, and
net charge-offs on loans.

Two modes read the same step:

* **runoff** follows today's positions to the end, with no new business. Its
  cash flows are what NEV discounts, and what a contractual liquidity gap
  counts.
* **going concern** adds new business to meet each product's planned balance,
  at the rate the scenario offers that month, and carries cash and equity. It
  is what NII, the FP&A statements and the liquidity stress read.

Cash is the account everything settles through: it earns the short rate, and
when it falls below the minimum the gap is borrowed overnight. Equity moves
only by net income. So the balance sheet balances every month by construction,
and `reconcile` checks that it did.
"""

import dataclasses

from keel.model import Position

CASH = "cash"


def monthly(annual):
    """An annual rate of loss (CPR, decay) as the monthly one that compounds to it."""
    annual = min(max(annual, 0.0), 1.0)
    return 1.0 - (1.0 - annual) ** (1.0 / 12.0)


@dataclasses.dataclass
class Flow:
    interest: float = 0.0
    principal: float = 0.0     # scheduled + prepaid + decayed
    chargeoff: float = 0.0


class Stepper(object):
    """Moves positions forward one month under one scenario."""

    def __init__(self, assumptions, scenario):
        self.a = assumptions
        self.s = scenario
        self.base_short = assumptions.curve.rate(assumptions.short_tenor) / 100.0
        self.start_rate = {}

    def curve_rate(self, month, tenor):
        return self.s.rate(self.a.curve, month, tenor) / 100.0

    def short_rate(self, month):
        return self.curve_rate(month, self.a.short_tenor)

    def index_rate(self, index, month):
        tenor, spread = self.a.indexes[index]
        return self.curve_rate(month, tenor) + spread

    def reprice(self, p, month):
        product = self.a.products[p.product]
        first = p.next_reset_months or p.reset_months
        if (p.rate_type == "variable" and p.age >= first > 0
                and (p.age - first) % p.reset_months == 0):
            rate = self.index_rate(p.index, month) + p.margin
            if p.floor is not None:
                rate = max(rate, p.floor)
            if p.cap is not None:
                rate = min(rate, p.cap)
            p.rate = rate
        elif p.rate_type == "administered":
            start = self.start_rate.setdefault(p.id, p.rate)
            p.rate = max(product.rate_floor, start + product.beta * (self.short_rate(month) - self.base_short))

    def step(self, p, month):
        """Advance `p` by one month (month 1 is the first after the analysis date)."""
        self.reprice(p, month)
        balance = p.balance
        if balance <= 0.005:
            p.balance = 0.0
            return Flow()
        product = self.a.products[p.product]
        interest = balance * p.rate / 12.0
        scheduled = prepaid = decayed = 0.0
        # Prepayment follows the long end (mortgage rates track ten years);
        # share decay follows the short end, where members compare rates.
        shift = self.s.shift_bp(month, 120) / 100.0
        short_shift = self.s.shift_bp(month, self.a.short_tenor) / 100.0
        if p.amortization == "level":
            n, r = p.term_months, p.rate / 12.0
            if n <= 1:
                scheduled = balance
            else:
                payment = balance * r / (1.0 - (1.0 + r) ** -n) if r > 0 else balance / n
                scheduled = min(balance, max(0.0, payment - interest))
            cpr = min(max(product.cpr - product.cpr_per_100bp * shift, product.cpr_floor), product.cpr_cap)
            prepaid = (balance - scheduled) * monthly(cpr)
        elif p.amortization == "balloon":
            # Paid as if over `amort_months`, and due in full at maturity.
            if p.term_months <= 1:
                scheduled = balance
            else:
                n, r = max(p.amort_months, 1), p.rate / 12.0
                payment = balance * r / (1.0 - (1.0 + r) ** -n) if r > 0 else balance / n
                scheduled = min(balance, max(0.0, payment - interest))
            cpr = min(max(product.cpr - product.cpr_per_100bp * shift, product.cpr_floor), product.cpr_cap)
            prepaid = (balance - scheduled) * monthly(cpr)
        elif p.amortization == "bullet":
            if p.term_months <= 1:
                scheduled = balance
        elif p.amortization == "callable":
            # Due at maturity; from its first call date the issuer calls it
            # whenever the coupon exceeds what the market would charge for the
            # remaining term by more than the product's threshold.
            if p.term_months <= 1:
                scheduled = balance
            elif p.age + 1 >= p.call_months:
                market = self.curve_rate(month, p.term_months) + product.spread
                if p.rate - market > product.call_threshold:
                    scheduled = balance
        elif p.amortization == "nonmaturity":
            decayed = balance * monthly(product.runoff + product.runoff_per_100bp * short_shift)
        remaining = balance - scheduled - prepaid - decayed
        chargeoff = remaining * product.charge_off / 12.0 if p.side == "asset" else 0.0
        p.balance = remaining - chargeoff
        if p.term_months:
            p.term_months -= 1
        if p.amort_months:
            p.amort_months -= 1
        p.age += 1
        return Flow(interest, scheduled + prepaid + decayed, chargeoff)


def earning(p):
    return p.product != CASH and not (p.rate_type == "none" and p.amortization == "none")


# --------------------------------------------------------------- runoff

def runoff(positions, assumptions, scenario, months=None):
    """{position id: [Flow per month]} for today's positions, no new business.

    Stops at `months` (default the NEV horizon). Whatever balance a
    non-maturity position still holds then is paid as a final principal flow,
    so every position's principal and charge-offs sum to its balance."""
    months = months or assumptions.nev_max_months
    stepper = Stepper(assumptions, scenario)
    out = {}
    for original in positions:
        if not earning(original):
            continue
        p = original.copy()
        flows = []
        for month in range(1, months + 1):
            flows.append(stepper.step(p, month))
            if p.balance <= 0.005:
                break
        if p.balance > 0.005:
            flows[-1].principal += p.balance
            p.balance = 0.0
        out[p.id] = flows
    return out


# --------------------------------------------------------------- going concern

@dataclasses.dataclass
class Month:
    month: int
    balances: dict                  # product -> balance at month end
    interest: dict                  # product -> interest this month
    cash: float = 0.0
    overnight: float = 0.0
    interest_income: float = 0.0
    interest_expense: float = 0.0
    overnight_interest: float = 0.0
    fee_income: float = 0.0
    operating_expense: float = 0.0
    credit_losses: float = 0.0
    net_income: float = 0.0
    equity: float = 0.0
    assets: float = 0.0
    liabilities: float = 0.0
    asset_principal_in: float = 0.0
    new_assets_out: float = 0.0
    liability_principal_out: float = 0.0
    new_liabilities_in: float = 0.0
    liquid_assets: float = 0.0      # liquid investments after haircut
    available_liquidity: float = 0.0

    @property
    def nii(self):
        return self.interest_income - self.interest_expense


def opening(positions):
    """Cash, total assets, liabilities and the equity that balances them."""
    cash = sum(p.balance for p in positions if p.product == CASH)
    assets = sum(p.balance for p in positions if p.side == "asset")
    liabilities = sum(p.balance for p in positions if p.side == "liability")
    return cash, assets, liabilities, assets - liabilities


def going_concern(positions, assumptions, scenario, stress=False, months=None):
    """[Month] for `months` (default the horizon), with new business to plan.

    With `stress`, the liquidity stress replaces the plan for liabilities:
    for the first `stress_months` no share or certificate money comes in and
    each product loses its `stress_runoff` share of opening balance on top of
    ordinary decay; afterwards liability balances are held where the stress
    left them. Loans keep funding to plan, which is the pressure the stress
    exists to measure."""
    a = assumptions
    months = months or a.horizon_months
    stepper = Stepper(a, scenario)
    book = [p.copy() for p in positions if p.product != CASH]
    cash, _, _, equity = opening(positions)
    overnight = 0.0
    start = {}
    for p in book:
        if earning(p):
            start[p.product] = start.get(p.product, 0.0) + p.balance
    template = {}
    for p in book:
        if earning(p) and (p.product not in template or p.balance > template[p.product].balance):
            template[p.product] = p
    held = {}
    out = []
    for t in range(1, months + 1):
        flows = {p.id: stepper.step(p, t) for p in book if earning(p)}
        month = Month(month=t, balances={}, interest={})
        by_id = {p.id: p for p in book}
        for pid, f in flows.items():
            p = by_id[pid]
            month.interest[p.product] = month.interest.get(p.product, 0.0) + f.interest
            month.credit_losses += f.chargeoff
            if p.side == "asset":
                month.interest_income += f.interest
                month.asset_principal_in += f.principal
            else:
                month.interest_expense += f.interest
                month.liability_principal_out += f.principal
        existing = {}
        for p in book:
            if earning(p):
                existing[p.product] = existing.get(p.product, 0.0) + p.balance
        new_positions = []
        for product, opening_balance in start.items():
            spec = a.products[product]
            side = template[product].side
            target = opening_balance * (1.0 + spec.growth) ** (t / 12.0)
            if stress and side == "liability":
                if t <= a.stress_months:
                    extra = opening_balance * spec.stress_runoff / a.stress_months
                    target = max(0.0, existing[product] - extra)
                    held[product] = target
                else:
                    target = held.get(product, existing[product])
            gap = target - existing[product]
            if spec.new_term == 0 and template[product].amortization == "nonmaturity":
                # A pooled non-maturity product: money in or out moves the pool.
                pools = [p for p in book if p.product == product]
                total = sum(p.balance for p in pools)
                for p in pools:
                    p.balance += gap * (p.balance / total if total else 1.0 / len(pools))
                if side == "asset":
                    month.new_assets_out += gap
                else:
                    month.new_liabilities_in += gap
                continue
            if gap <= 0.005 or spec.new_term <= 0:
                continue
            new_positions.append(_originate(template[product], spec, gap, t, stepper))
            if side == "asset":
                month.new_assets_out += gap
            else:
                month.new_liabilities_in += gap
        book.extend(new_positions)

        short = stepper.short_rate(t)
        cash_interest = cash * short / 12.0
        overnight_interest = overnight * (short + a.overnight_spread) / 12.0
        month.interest_income += cash_interest
        month.interest_expense += overnight_interest
        month.overnight_interest = overnight_interest
        month.interest[CASH] = cash_interest
        month.fee_income = a.fee_income / 12.0
        month.operating_expense = a.operating_expense / 12.0 * (1.0 + a.expense_growth) ** ((t - 1) // 12)
        month.net_income = (month.interest_income - month.interest_expense + month.fee_income
                            - month.operating_expense - month.credit_losses)

        cash += (month.asset_principal_in - month.new_assets_out
                 - month.liability_principal_out + month.new_liabilities_in
                 + month.interest_income - month.interest_expense
                 + month.fee_income - month.operating_expense)
        if cash < a.cash_minimum:
            overnight += a.cash_minimum - cash
            cash = a.cash_minimum
        elif overnight > 0:
            repay = min(overnight, cash - a.cash_minimum)
            overnight -= repay
            cash -= repay
        equity += month.net_income

        for p in book:
            month.balances[p.product] = month.balances.get(p.product, 0.0) + p.balance
        month.balances[CASH] = cash
        month.cash, month.overnight, month.equity = cash, overnight, equity
        month.assets = cash + sum(p.balance for p in book if p.side == "asset")
        month.liabilities = overnight + sum(p.balance for p in book if p.side == "liability")
        month.liquid_assets = sum(p.balance * (1.0 - a.products[p.product].haircut)
                                  for p in book if a.products[p.product].liquid)
        month.available_liquidity = (cash - a.cash_minimum + month.liquid_assets
                                     + sum(c for _, c in a.contingent) - overnight)
        out.append(month)
    return out


def _originate(template, spec, amount, month, stepper):
    """New business in `month`, priced off the scenario."""
    p = Position(
        id="%s@%d" % (template.product, month), name="new " + template.product,
        product=template.product, side=template.side, balance=amount,
        rate=0.0, rate_type=template.rate_type, index=template.index, margin=template.margin,
        reset_months=template.reset_months, term_months=spec.new_term,
        amortization=spec.new_amortization or template.amortization,
        floor=template.floor, cap=template.cap, new_business=True,
        amort_months=spec.new_amort_term, call_months=spec.new_call_months)
    if p.amortization == "balloon" and p.amort_months <= p.term_months:
        p.amort_months = max(p.term_months * 2, 300)
    if p.rate_type == "variable":
        p.rate = stepper.index_rate(p.index, month) + p.margin
    else:
        p.rate = stepper.curve_rate(month, spec.new_term) + spec.spread
    return p
