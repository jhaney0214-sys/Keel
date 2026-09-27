"""Regulatory capital, and what unrealized securities losses would do to it.

**Credit unions** (12 CFR 702): the net worth ratio, net worth over total
assets, sets the prompt-corrective-action category: well capitalized at 7%,
adequately at 6%, undercapitalized at 4%, significantly at 2%, critically
below that. A complex credit union (over $500 million) also meets the
risk-based capital ratio, 10% for well capitalized and 8% for adequately,
or elects the complex credit union leverage ratio (CCULR) and holds 9%
instead.

**Banks** (12 CFR 324 and the parallel rules): well capitalized at Tier 1
leverage 5%, common equity Tier 1 6.5%, Tier 1 risk-based 8% and total
risk-based 10%; a qualifying community bank may elect the community bank
leverage ratio (CBLR) at 9%. Here Tier 1 and CET1 are taken as equity, and
total capital adds the allowance up to 1.25% of risk-weighted assets.

Both rest on Keel's product-level risk weights, so they are estimates, not a
call report: no deductions, no off-balance-sheet exposures, no past-due or
concentration adjustments.

**Unrealized losses.** Regulatory capital for credit unions, and for banks
that opted out of AOCI, ignores market losses on securities until they are
sold. Liquidity does not: a security sold in a stress sells at market. So
the page also shows net worth with the portfolio at market, today and after
a +300bp shock.
"""

from keel import engine, measures, profitability
from keel.curve import Scenario

CU_BANDS = ((0.07, "Well capitalized"), (0.06, "Adequately capitalized"), (0.04, "Undercapitalized"),
            (0.02, "Significantly undercapitalized"), (-1e9, "Critically undercapitalized"))
COMPLEX_CU = 500e6


def cu_category(ratio):
    for bound, name in CU_BANDS:
        if ratio >= bound:
            return name


def measures_for(positions, a, securities):
    """Every ratio this institution is held to, with its thresholds and status."""
    _, assets, _, equity = engine.opening(positions)
    rwa = profitability.rwa(positions, a)
    allowance = -sum(p.balance for p in positions if p.side == "asset" and p.balance < 0)
    rows = []

    def add(name, value, well, adequate=None, note=""):
        status = "within" if value >= well else ("near" if adequate is not None and value >= adequate else "breach")
        rows.append({"measure": name, "value": value, "well": well, "adequate": adequate, "status": status,
                     "note": note})

    if a.institution == "bank":
        add("Tier 1 leverage ratio", equity / assets, 0.05, 0.04)
        if rwa:
            add("Common equity Tier 1 ratio", equity / rwa, 0.065, 0.045)
            add("Tier 1 risk-based ratio", equity / rwa, 0.08, 0.06)
            add("Total risk-based ratio", (equity + min(allowance, 0.0125 * rwa)) / rwa, 0.10, 0.08,
                "equity plus the allowance, up to 1.25% of risk-weighted assets")
        if assets < 10e9:
            add("Community bank leverage ratio (if elected)", equity / assets, 0.09, None,
                "an election for banks under $10 billion; at or above 9% it replaces the risk-based ratios")
        category = None
    else:
        ratio = equity / assets
        category = cu_category(ratio)
        add("Net worth ratio", ratio, 0.07, 0.06, category)
        if assets >= COMPLEX_CU and rwa:
            add("Risk-based capital ratio", (equity + min(allowance, 0.0125 * rwa)) / rwa, 0.10, 0.08,
                "complex credit unions; equity plus the allowance, up to 1.25% of risk-weighted assets")
            add("Complex credit union leverage ratio (if elected)", ratio, 0.09, None,
                "at or above 9% it replaces the risk-based capital ratio")
    book = sum(s["book"] for s in securities)
    market = sum(s["market"] for s in securities)
    shocked = _market_at(positions, a, securities, 300)
    lens = {"book": book, "market": market, "unrealized": market - book, "market_300": shocked,
            "unrealized_300": shocked - book, "equity": equity, "assets": assets,
            "ratio_now": (equity + market - book) / (assets + market - book) if assets else 0.0,
            "ratio_300": (equity + shocked - book) / (assets + shocked - book) if assets else 0.0}
    return {"rows": rows, "category": category, "rwa": rwa, "equity": equity, "assets": assets,
            "complex": a.institution != "bank" and assets >= COMPLEX_CU, "lens": lens}


def _market_at(positions, a, securities, bp):
    ids = {s["id"] for s in securities}
    shock = Scenario("+%d" % bp, bp, floor=a.rate_floor)
    total = 0.0
    for p in positions:
        if p.id not in ids:
            continue
        if p.amortization == "none":
            total += p.balance                  # stakes at book, as in NEV
        else:
            total += measures.nev([p], a, shock).pv_assets
    return total
