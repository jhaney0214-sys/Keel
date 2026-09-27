"""Liquidity beyond the single stress: collateral, graded scenarios and
deposit concentration.

**Collateral.** A borrowing line is only as good as what can be pledged
against it. A contingent source marked `secured` (an FHLB line, the Federal
Reserve's discount window) is capped at the lendable value of the loans the
lender accepts, less what is already borrowed: each product's
`collateral_value` is the share of its balance a lender will lend against
(first mortgages typically 70-80%, commercial real estate 50-65%).
Securities are left out on purpose. They already count in the stress as
liquid investments after their haircut, and counting them again as
collateral would count the same dollar twice.

**Graded scenarios.** One stress answers one question. Examiners expect a
contingency funding plan to show several, of rising severity, and since 2023
one where large uninsured depositors leave first. Each scenario scales the
products' stress runoff, adds to the haircut on liquid investments, cuts
what the contingent sources will lend, and can add a run on uninsured
balances; each is run through the same stressed projection as the base.

**Concentration.** From `depositors.csv` in the folder (one row per member:
member_id, balance), the uninsured share (balances over $250,000, the
NCUSIF and FDIC standard maximum per depositor per ownership category) and
the share held by the largest 10 and 20 depositors. Ownership categories
are not in the file, so the uninsured figure is an upper-bound estimate; the
report says so. Without the file, the uninsured part of each certificate
over $250,000 gives a floor.
"""

import dataclasses
import os

from keel import engine, measures, tables
from keel.model import InputError

INSURED_LIMIT = 250000.0

#: Used when the settings name no scenarios. (name, runoff multiplier,
#: haircut points added, share of contingent capacity still available,
#: months, share of uninsured balances that leave).
DEFAULT_SCENARIOS = (
    ("As configured", 1.0, 0.0, 1.00, None, 0.0),
    ("Severe", 2.0, 5.0, 0.75, None, 0.0),
    ("Systemic", 1.5, 10.0, 0.50, 6, 0.0),
    ("Uninsured run", 1.0, 0.0, 1.00, None, 0.5),
)


@dataclasses.dataclass
class Stress:
    name: str
    runoff_multiplier: float = 1.0
    haircut_add: float = 0.0          # decimal, added to each liquid product's haircut
    contingent_available: float = 1.0
    months: int = None                # default: the settings' stress_months
    uninsured_runoff: float = 0.0     # share of uninsured balances that leave over the stress


def scenarios(raw_list):
    """Stresses from the settings' liquidity.stresses, or the defaults."""
    if not raw_list:
        return [Stress(n, m, h / 100.0, c, mo, u) for n, m, h, c, mo, u in DEFAULT_SCENARIOS]
    out = []
    for n, spec in enumerate(raw_list, 1):
        try:
            out.append(Stress(name=str(spec["name"]), runoff_multiplier=float(spec.get("runoff_multiplier", 1.0)),
                              haircut_add=float(spec.get("haircut_add", 0.0)) / 100.0,
                              contingent_available=float(spec.get("contingent_available", 100.0)) / 100.0,
                              months=int(spec["months"]) if spec.get("months") not in (None, "") else None,
                              uninsured_runoff=float(spec.get("uninsured_runoff", 0.0)) / 100.0))
        except (KeyError, TypeError, ValueError) as error:
            raise InputError("assumptions: liquidity stress %d: %s" % (n, error))
    return out


# --------------------------------------------------------------- collateral

def collateral(positions, a):
    """[(product, balance, lendable share, lendable value)] and the total."""
    rows = {}
    for p in positions:
        spec = a.products[p.product]
        share = getattr(spec, "collateral_value", 0.0) or 0.0
        if p.side != "asset" or share <= 0 or p.balance <= 0:
            continue
        row = rows.setdefault(p.product, [p.product, 0.0, share, 0.0])
        row[1] += p.balance
        row[3] += p.balance * share
    out = sorted(rows.values(), key=lambda r: -r[3])
    return [tuple(r) for r in out], sum(r[3] for r in out)


def borrowed(positions):
    return sum(p.balance for p in positions if p.side == "liability" and p.product == "borrowings")


def effective_contingent(positions, a):
    """(contingent list with secured lines capped by collateral, notes)."""
    _, lendable = collateral(positions, a)
    headroom = max(lendable - borrowed(positions), 0.0)
    out, notes = [], []
    for name, capacity in a.contingent:
        if name in a.secured:
            usable = min(capacity, headroom)
            notes.append({"name": name, "line": capacity, "collateral_headroom": headroom, "usable": usable})
            headroom -= usable            # two secured lines cannot pledge the same loans
            out.append((name, usable))
        else:
            out.append((name, capacity))
    return out, notes


def with_collateral(positions, a):
    """The settings with every secured line capped by its collateral."""
    if not a.secured:
        return a
    contingent, _ = effective_contingent(positions, a)
    return dataclasses.replace(a, contingent=contingent)


# --------------------------------------------------------------- scenarios

def _shares_total(positions):
    return sum(p.balance for p in positions if p.side == "liability"
               and p.product not in ("borrowings", "other_liabilities") and p.balance > 0)


def run(positions, a, stresses, uninsured=None):
    """Each stress run through the stressed projection: its survival month
    (None if liquidity lasts the measured year), low point and its month."""
    base = with_collateral(positions, a)
    shares = _shares_total(positions)
    out = []
    for s in stresses:
        months = s.months or base.stress_months
        extra = (uninsured or 0.0) * s.uninsured_runoff / shares if shares else 0.0
        products = {}
        for name, spec in base.products.items():
            changes = {}
            if spec.stress_runoff or extra:
                changes["stress_runoff"] = min(0.95, spec.stress_runoff * s.runoff_multiplier + extra) \
                    if name not in ("borrowings", "other_liabilities") else spec.stress_runoff
            if spec.liquid and s.haircut_add:
                changes["haircut"] = min(0.95, spec.haircut + s.haircut_add)
            products[name] = dataclasses.replace(spec, **changes) if changes else spec
        b = dataclasses.replace(base, products=products, stress_months=months,
                                contingent=[(n, c * s.contingent_available) for n, c in base.contingent])
        stressed = engine.going_concern(positions, b, b.scenarios[0], stress=True,
                                        months=max(measures.SURVIVAL_MONTHS, months))
        window = stressed[:measures.SURVIVAL_MONTHS]
        low = min(window, key=lambda m: m.available_liquidity)
        out.append({"name": s.name, "runoff_multiplier": s.runoff_multiplier, "haircut_add": s.haircut_add,
                    "contingent_available": s.contingent_available, "months": months,
                    "uninsured_runoff": s.uninsured_runoff, "survival": measures.survival(stressed),
                    "lowest": low.available_liquidity, "lowest_month": low.month,
                    "path": [m.available_liquidity for m in window]})
    return out


# --------------------------------------------------------------- concentration

def read_depositors(folder):
    """[balance] per member from depositors.csv/.xlsx, or None."""
    path = tables.find(folder, "depositors", required=False) if folder else None
    if path is None:
        return None
    out = []
    for n, row in enumerate(tables.read_table(path), 2):
        try:
            out.append(float(str(row.get("balance", "")).replace(",", "")))
        except ValueError:
            raise InputError("%s line %d: balance %r is not a number" % (os.path.basename(path), n,
                                                                         row.get("balance")))
    return out


def concentration(positions, folder=None, imported=None):
    """Uninsured balances and large-depositor shares, and how they were measured."""
    shares = _shares_total(positions)
    members = read_depositors(folder)
    if members:
        members.sort(reverse=True)
        total = sum(members)
        uninsured = sum(max(0.0, m - INSURED_LIMIT) for m in members)
        return {"source": "depositors", "members": len(members), "total": total, "shares": shares,
                "uninsured": uninsured, "uninsured_share": uninsured / total if total else 0.0,
                "over_limit": sum(1 for m in members if m > INSURED_LIMIT),
                "top10_share": sum(members[:10]) / total if total else 0.0,
                "top20_share": sum(members[:20]) / total if total else 0.0,
                "largest": members[0]}
    if imported is not None and folder:
        path = tables.find(os.path.join(folder, "data"), "certificates", required=False)
        if path:
            floor = 0.0
            for row in tables.read_table(path):
                try:
                    floor += max(0.0, float(row.get("balance") or 0) - INSURED_LIMIT)
                except ValueError:
                    continue
            return {"source": "certificates", "shares": shares, "uninsured": floor,
                    "uninsured_share": floor / shares if shares else 0.0}
    return None
