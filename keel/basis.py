"""Basis risk: what happens when rates that usually move together don't.

A parallel shock moves every rate by the same amount. The losses that
surprise an ALCO often come instead from a spread moving: prime held while
funding costs rose, SOFR loans repricing down while share rates stayed, a
competitor forcing share rates up with the market standing still. Each test
here moves one rate against the curve and leaves everything else where the
base plan has it:

    each index a variable-rate position resets to     -50bp and +50bp
    administered share rates                          +25bp and -25bp

and reports year-one and year-two net interest income against the base.
Scenarios of any shape can also carry a `basis` in the settings (Scenarios
sheet: "PRIME:-50, shares:25"), combining a basis move with a curve move.
"""

from keel import engine, measures
from keel.curve import Scenario

INDEX_MOVE = 50
SHARE_MOVE = 25


def _nii(run, year):
    return measures.income_statement(measures.year(run, year))["net_interest_income"]


def run(positions, a, base_run=None, index_move=INDEX_MOVE, share_move=SHARE_MOVE):
    """[{test, rate, move_bp, exposure_assets, exposure_liabilities, y1, y1_change, y2_change}], or []."""
    base = base_run or engine.going_concern(positions, a, a.scenarios[0])
    b1, b2 = _nii(base, 1), _nii(base, 2) if len(base) >= 24 else None
    exposure = {}
    for p in positions:
        if p.balance <= 0:
            continue
        if p.rate_type == "variable" and p.index:
            key = p.index
        elif p.rate_type == "administered" and p.side == "liability":
            key = "shares"
        else:
            continue
        e = exposure.setdefault(key, [0.0, 0.0])
        e[0 if p.side == "asset" else 1] += p.balance
    from keel import parallel
    tests = [(key, bp) for key in sorted(exposure, key=lambda k: (k == "shares", k))
             for bp in ((-share_move, share_move) if key == "shares" else (-index_move, index_move))]
    plans = parallel.run([("keel.engine.going_concern",
                           (positions, a, Scenario("%s %+dbp" % (key, bp), 0, 0, a.rate_floor, basis={key: bp})), {})
                          for key, bp in tests])
    rows = []
    for (key, bp), r in zip(tests, plans):
        y1, y2 = _nii(r, 1), _nii(r, 2) if len(r) >= 24 else None
        rows.append({"test": "share rates" if key == "shares" else key, "key": key, "move_bp": bp,
                     "exposure_assets": exposure[key][0], "exposure_liabilities": exposure[key][1],
                     "y1": y1, "y1_change": y1 - b1, "y1_change_pct": (y1 - b1) / b1 if b1 else 0.0,
                     "y2_change": None if y2 is None or b2 is None else y2 - b2})
    return rows
