"""Everything the credit union reads, computed from the one projection.

ALM: NII by scenario and year, and NEV under each parallel shock, with the
NCUA NEV Supervisory Test (+300bp; thresholds from SL 22-01).
FP&A: the planned balance sheet and income statement, by year.
Liquidity: sources and uses, the contractual gap, ratios, the stress survival
horizon, and which tier of 12 CFR 741.12 applies.
Reconciliation: checks that the three agree, because the claim this tool makes
is that they are one model, and a claim like that has to be tested.
"""

import dataclasses

from keel import engine
from keel.engine import CASH

# NCUA Letter to Credit Unions SL 22-01 (September 2022). "Extreme" was removed.
NEV_RATIO_BANDS = ((0.07, "Low"), (0.04, "Moderate"), (-1e9, "High"))          # rating if ratio > bound
NEV_SENSITIVITY_BANDS = ((0.40, "Low"), (0.65, "Moderate"), (1e9, "High"))     # rating if sensitivity < bound
CFP_TIERS = ((250e6, "$250M or more: contingency funding plan, and access to at least one "
                     "contingent federal liquidity source (12 CFR 741.12(b)-(c))"),
             (50e6, "$50M to $250M: contingency funding plan (12 CFR 741.12(b))"),
             (0.0, "Under $50M: basic written liquidity policy and a list of contingent "
                   "sources (12 CFR 741.12(a))"))


def ratio_rating(ratio):
    for bound, label in NEV_RATIO_BANDS:
        if ratio > bound:
            return label
    return "High"


def sensitivity_rating(sensitivity):
    for bound, label in NEV_SENSITIVITY_BANDS:
        if sensitivity < bound:
            return label
    return "High"


# --------------------------------------------------------------- NEV

@dataclasses.dataclass
class NEV:
    scenario: str
    pv_assets: float
    pv_liabilities: float
    by_product: dict

    @property
    def nev(self):
        return self.pv_assets - self.pv_liabilities

    @property
    def ratio(self):
        return self.nev / self.pv_assets if self.pv_assets else 0.0


#: NCUA's NEV Supervisory Test prices every non-maturity share at these
#: standardized values, whatever the credit union's own assumptions say, and
#: leaves every other position at its own model value: 99.00 in the base
#: scenario (a 1% benefit) and 95.04 at +300bp (a further 4% decline). From
#: ALM First's and NCUA's descriptions of the test, read 2026-09-27.
SUPERVISORY_SHARE_PRICE = {0.0: 0.99, 300.0: 0.9504}


def is_share(position):
    """A non-maturity share: the positions the supervisory test standardizes."""
    return position.side == "liability" and position.amortization == "nonmaturity"


def nev(positions, assumptions, scenario, supervisory=False):
    """Present value of every position's runoff cash flows, discounted at the
    scenario curve (as shocked on the analysis date) plus the product's
    discount spread. Cash and non-earning positions count at book.

    With `supervisory`, non-maturity shares take NCUA's standardized prices
    instead of their modelled value; only the base and +300bp scenarios have
    one."""
    if supervisory and scenario.shock_bp not in SUPERVISORY_SHARE_PRICE:
        raise ValueError("the supervisory test prices shares only at base and +300bp, not %s"
                         % scenario.name)
    flows = engine.runoff(positions, assumptions, scenario)
    by_product = {}
    pv_assets = pv_liabilities = 0.0
    for p in positions:
        if supervisory and is_share(p):
            value = p.balance * SUPERVISORY_SHARE_PRICE[scenario.shock_bp]
        elif p.id in flows:
            spread = assumptions.products[p.product].discount_spread
            value = 0.0
            for k, f in enumerate(flows[p.id], 1):
                y = scenario.rate(assumptions.curve, 0, k) / 100.0 + spread
                value += (f.interest + f.principal) / (1.0 + y / 12.0) ** k
        else:
            value = p.balance
        by_product[p.product] = by_product.get(p.product, 0.0) + value
        if p.side == "asset":
            pv_assets += value
        else:
            pv_liabilities += value
    return NEV(scenario.name, pv_assets, pv_liabilities, by_product)


def ncua_test(base, shocked):
    """The NEV Supervisory Test, from the supervisory base and +300bp NEVs.

    Two measures, both at +300bp: the post-shock NEV ratio, and the NEV
    percent change (the decline in NEV itself), which is what NCUA and ALM
    practitioners describe the sensitivity rating as using. The decline in
    the ratio is reported beside it for reference, not rated."""
    ratio_decline = (base.ratio - shocked.ratio) / base.ratio if base.ratio else 0.0
    value_decline = (base.nev - shocked.nev) / base.nev if base.nev else 0.0
    return {
        "post_shock_ratio": shocked.ratio,
        "ratio_rating": ratio_rating(shocked.ratio),
        "sensitivity_value_decline": value_decline,
        "sensitivity_ratio_decline": ratio_decline,
        "sensitivity_rating": sensitivity_rating(value_decline),
    }


# --------------------------------------------------------------- years

def year(months, n):
    """Months 12(n-1)+1 .. 12n."""
    return months[12 * (n - 1):12 * n]


def total(months, attribute):
    return sum(getattr(m, attribute) for m in months)


def income_statement(months):
    return {
        "interest_income": total(months, "interest_income"),
        "interest_expense": total(months, "interest_expense"),
        "net_interest_income": total(months, "interest_income") - total(months, "interest_expense"),
        "fee_income": total(months, "fee_income"),
        "operating_expense": total(months, "operating_expense"),
        "credit_losses": total(months, "credit_losses"),
        "net_income": total(months, "net_income"),
    }


def balance_sheet(month):
    return {"assets": month.assets, "liabilities": month.liabilities, "equity": month.equity,
            "net_worth_ratio": month.equity / month.assets if month.assets else 0.0,
            "cash": month.cash, "overnight_borrowing": month.overnight}


# --------------------------------------------------------------- liquidity

#: The survival horizon is measured over the first year: after it, the
#: stress's rule that shares never regrow while loans fund to plan stops being
#: a stress and becomes a different plan. Found on the large sample, whose
#: "survival" fell to month 48 from plan growth alone.
SURVIVAL_MONTHS = 12


def survival(stressed, months=SURVIVAL_MONTHS):
    """The first month within `months` that available liquidity is negative
    under stress, or None if it stays positive throughout."""
    for m in stressed[:months]:
        if m.available_liquidity < 0:
            return m.month
    return None


def funding_gap(run):
    """The plan's own funding need: the most overnight borrowing it draws,
    and the month it does. Growth that outruns share growth shows here, not
    in the stress."""
    peak = max(run, key=lambda m: m.overnight)
    return peak.overnight, peak.month


def contractual_gap(positions, assumptions, scenario, months=12):
    """Monthly asset inflows less liability outflows from today's positions
    alone, and its running total: the gap before any new business."""
    flows = engine.runoff(positions, assumptions, scenario, months)
    side = {p.id: p.side for p in positions}
    rows, running = [], 0.0
    for k in range(months):
        net = 0.0
        for pid, fs in flows.items():
            if k < len(fs):
                cash_flow = fs[k].interest + fs[k].principal
                net += cash_flow if side[pid] == "asset" else -cash_flow
        running += net
        rows.append((k + 1, net, running))
    return rows


def cfp_tier(total_assets):
    for bound, text in CFP_TIERS:
        if total_assets >= bound:
            return text
    return CFP_TIERS[-1][1]


def ratios(positions, assumptions):
    a = assumptions
    shares = sum(p.balance for p in positions if p.side == "liability"
                 and p.product not in ("borrowings", "other_liabilities"))
    loans = sum(p.balance for p in positions if p.side == "asset" and a.products[p.product].charge_off > 0)
    liquid = sum(p.balance for p in positions if p.product == CASH or a.products[p.product].liquid)
    assets = sum(p.balance for p in positions if p.side == "asset")
    borrowed = sum(p.balance for p in positions if p.product == "borrowings")
    return {"loans_to_shares": loans / shares if shares else 0.0,
            "liquid_to_shares": liquid / shares if shares else 0.0,
            "borrowings_to_assets": borrowed / assets if assets else 0.0}


# --------------------------------------------------------------- reconciliation

@dataclasses.dataclass
class Check:
    name: str
    passed: bool
    detail: str


def reconcile(positions, assumptions, runs):
    """`runs` is {scenario name: [Month]} for going concern."""
    checks = []
    worst, where = 0.0, ""
    for name, months in runs.items():
        for m in months:
            gap = abs(m.assets - m.liabilities - m.equity)
            if gap > worst:
                worst, where = gap, "%s month %d" % (name, m.month)
    checks.append(Check("The balance sheet balances every month in every scenario",
                        worst < 1.0, "largest gap $%.4f%s" % (worst, (" (" + where + ")") if where else "")))

    # Two independent paths to the same number: the income statement's two
    # lines, and the product-by-product interest detail ALM reports from.
    base = runs["base"]
    by_line = total(year(base, 1), "interest_income") - total(year(base, 1), "interest_expense")
    sides = {p.product: p.side for p in positions}
    sides[CASH] = "asset"
    by_product = sum((value if sides[product] == "asset" else -value)
                     for m in year(base, 1) for product, value in m.interest.items())
    by_product -= total(year(base, 1), "overnight_interest")
    checks.append(Check("Year-one NII is the same in the FP&A income statement and the ALM product detail",
                        abs(by_line - by_product) < 1.0,
                        "income statement $%s, product detail $%s" % (_m(by_line), _m(by_product))))

    cash_line = [m.balances[CASH] for m in base]
    cash_path = [m.cash for m in base]
    checks.append(Check("Liquidity's cash path is the balance sheet's cash line",
                        cash_line == cash_path, "%d months compared" % len(base)))

    flows = engine.runoff(positions, assumptions, assumptions.scenarios[0])
    worst, which = 0.0, ""
    for p in positions:
        if p.id in flows:
            paid = sum(f.principal + f.chargeoff for f in flows[p.id])
            if abs(paid - p.balance) > worst:
                worst, which = abs(paid - p.balance), p.id
    checks.append(Check("Every position's runoff repays exactly its balance (NEV counts all of it)",
                        worst < 0.01, "largest difference $%.4f%s" % (worst, (" (" + which + ")") if which else "")))
    return checks


def _m(value):
    return "{:,.0f}".format(value)
