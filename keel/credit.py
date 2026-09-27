"""Credit losses under stress, and a CECL allowance estimate.

The rate scenarios move the curve and hold credit steady; a recession does
the opposite first. A credit scenario multiplies every loan product's
charge-off rate for a stressed period, then phases the multiplier back to
one over a reversion period:

    Baseline                  x1
    Moderate recession        x2   for 12 months, back to x1 over 12
    Severe recession          x3.5 for 18 months, back to x1 over 12

(or the settings' CreditScenarios sheet). Each runs through the plan: credit
losses, net income and the net worth ratio's low.

**CECL.** The Current Expected Credit Loss standard books, on day one, the
losses expected over each loan's remaining life. The estimate here is the
remaining-life method: each position's own runoff (amortization and
prepayment) with its product's charge-off rate applied month by month, so a
loan that pays off quickly carries less reserve than one that lasts. Under a
credit scenario the same calculation over the forecast gives the allowance
that scenario would require; the difference from the baseline is the
provision a turn in the forecast forces at once, and it comes out of net
worth before a single loan defaults. The estimate is set against the
allowance on the books (a negative contra-asset position).

This is a single-factor, product-level estimate, not a vintage, PD/LGD or
discounted-cash-flow model, and it has no qualitative adjustment; it is a
check on the booked allowance and a sizing of the stress, not a filing.
"""

import dataclasses

from keel import engine, measures
from keel.model import InputError


@dataclasses.dataclass
class CreditScenario:
    name: str
    multiplier: float = 1.0
    months: int = 0
    reversion_months: int = 0

    def factor(self, month):
        if month <= self.months:
            return self.multiplier
        if self.reversion_months and month <= self.months + self.reversion_months:
            left = 1.0 - (month - self.months) / float(self.reversion_months)
            return 1.0 + (self.multiplier - 1.0) * left
        return 1.0


DEFAULT_SCENARIOS = (("Baseline", 1.0, 0, 0), ("Moderate recession", 2.0, 12, 12),
                     ("Severe recession", 3.5, 18, 12))


def scenarios(raw_list):
    if not raw_list:
        return [CreditScenario(*x) for x in DEFAULT_SCENARIOS]
    out = []
    for n, spec in enumerate(raw_list, 1):
        try:
            out.append(CreditScenario(str(spec["name"]), float(spec.get("multiplier", 1.0)),
                                      int(float(spec.get("months") or 0)), int(float(spec.get("reversion_months") or 0))))
        except (KeyError, TypeError, ValueError) as error:
            raise InputError("assumptions: credit scenario %d: %s" % (n, error))
    if not any(s.multiplier == 1.0 and s.months == 0 for s in out):
        out.insert(0, CreditScenario("Baseline"))
    return out


def booked_allowance(positions):
    """The allowance on the books: negative-balance asset positions, as a positive number."""
    return -sum(p.balance for p in positions if p.side == "asset" and p.balance < 0)


def lifetime(positions, a, scenario=None):
    """{product: {balance, rate, wal_years, lifetime}} and the total lifetime loss."""
    loans = [p for p in positions if p.side == "asset" and p.balance > 0 and a.products[p.product].charge_off > 0]
    flows = engine.runoff(loans, a, a.scenarios[0], credit=scenario.factor if scenario else None)
    out = {}
    for p in loans:
        f = flows.get(p.id, [])
        losses = sum(x.chargeoff for x in f)
        paid = sum(x.principal + x.chargeoff for x in f)
        wal = sum(k * (x.principal + x.chargeoff) for k, x in enumerate(f, 1)) / paid / 12.0 if paid else 0.0
        row = out.setdefault(p.product, {"product": p.product, "balance": 0.0, "rate": a.products[p.product].charge_off,
                                         "wal_x": 0.0, "lifetime": 0.0})
        row["balance"] += p.balance
        row["wal_x"] += p.balance * wal
        row["lifetime"] += losses
    rows = []
    for row in sorted(out.values(), key=lambda r: -r["lifetime"]):
        row["wal_years"] = row.pop("wal_x") / row["balance"] if row["balance"] else 0.0
        rows.append(row)
    return rows, sum(r["lifetime"] for r in rows)


def run(positions, a, credit_scenarios, base_run=None):
    """Each credit scenario through the plan and the CECL estimate."""
    _, opening_assets, _, opening_equity = engine.opening(positions)
    rows_base, life_base = lifetime(positions, a)
    out = []
    for s in credit_scenarios:
        is_base = s.multiplier == 1.0 and s.months == 0
        plan = base_run if (is_base and base_run is not None) else \
            engine.going_concern(positions, a, a.scenarios[0], credit=s.factor)
        _, life = (rows_base, life_base) if is_base else lifetime(positions, a, s)
        build = life - life_base
        y = [measures.income_statement(measures.year(plan, n)) for n in (1, 2)]
        out.append({"name": s.name, "multiplier": s.multiplier, "months": s.months,
                    "reversion_months": s.reversion_months,
                    "losses_y1": y[0]["credit_losses"], "losses_y2": y[1]["credit_losses"],
                    "net_income_2y": y[0]["net_income"] + y[1]["net_income"],
                    "lifetime": life, "allowance_build": build,
                    "net_worth_after_build": (opening_equity - build) / (opening_assets - build),
                    "lowest_net_worth": min(m.equity / m.assets for m in plan)})
    return {"products": rows_base, "estimate": life_base, "booked": booked_allowance(positions),
            "scenarios": out}
