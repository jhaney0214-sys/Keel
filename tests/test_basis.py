"""Separate index curves, basis scenarios and the prepayment model.

Each rule is checked on one position whose answer can be worked by hand:
an index on its own curve with a beta, a basis move and its ramp, share
rates pushed by a basis, a mortgage's speed from its own refinancing
incentive, seasoning and burnout. Then the settings round-trip and the
basis-risk table on a sample.
"""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import basis, curve, engine, model, settings  # noqa: E402
from keel.__main__ import load  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402


def assumptions(products, indexes=None):
    return model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "120": 5.0, "360": 5.0}),
                             indexes=indexes or {"IDX": model.Index(12, 0.0)}, products=products,
                             scenarios=curve.standard_scenarios())


def pos(**kw):
    base = dict(id="x", name="x", product="p", side="asset", balance=100000.0, rate=0.06,
                rate_type="fixed", term_months=360, amortization="level")
    base.update(kw)
    return model.Position(**base)


class Indexes(unittest.TestCase):
    def test_the_default_index_is_the_curve_plus_its_spread(self):
        a = assumptions({"p": model.Product("p")}, {"PRIME": model.Index(1, 0.03)})
        s = engine.Stepper(a, Scenario("+200", 200))
        self.assertAlmostEqual(s.index_rate("PRIME", 1), 0.04 + 0.02 + 0.03, places=12)

    def test_an_index_on_its_own_curve_with_a_beta(self):
        own = model.Index(1, 0.0, beta=0.5, curve=Curve({"1": 7.5}))
        a = assumptions({"p": model.Product("p")}, {"PRIME": own})
        s = engine.Stepper(a, Scenario("+200", 200))
        self.assertAlmostEqual(s.index_rate("PRIME", 1), 0.075 + 0.5 * 0.02, places=12)

    def test_a_basis_move_ramps_with_its_scenario(self):
        a = assumptions({"p": model.Product("p")})
        flat = engine.Stepper(a, Scenario("basis", 0, 0, basis={"IDX": -50}))
        ramp = engine.Stepper(a, Scenario("basis", 0, 12, basis={"IDX": -50}))
        base = engine.Stepper(a, Scenario("base", 0))
        self.assertAlmostEqual(base.index_rate("IDX", 6) - flat.index_rate("IDX", 6), 0.005, places=12)
        self.assertAlmostEqual(base.index_rate("IDX", 6) - ramp.index_rate("IDX", 6), 0.0025, places=12)

    def test_share_rates_take_the_shares_basis(self):
        a = assumptions({"s": model.Product("s", beta=0.5)})
        share = pos(product="s", side="liability", rate=0.02, rate_type="administered", amortization="nonmaturity",
                    term_months=0)
        engine.Stepper(a, Scenario("comp", 0, 0, basis={"shares": 25})).step(share, 1)
        self.assertAlmostEqual(share.rate, 0.0225, places=12)

    def test_old_tuple_indexes_still_read(self):
        a = assumptions({"p": model.Product("p")}, {"IDX": (12, 0.01)})
        self.assertAlmostEqual(engine.Stepper(a, Scenario("base", 0)).index_rate("IDX", 1),
                               a.curve.rate(12) / 100.0 + 0.01, places=12)


class Prepayment(unittest.TestCase):
    def product(self, **kw):
        base = dict(cpr=0.08, cpr_per_100bp=0.06, cpr_floor=0.02, cpr_cap=0.60, new_term=360, spread=0.01,
                    refi_incentive=True)
        base.update(kw)
        return model.Product("p", **base)

    def speed(self, product, p, month=1, scenario=None):
        a = assumptions({"p": product})
        return engine.Stepper(a, scenario or Scenario("base", 0)).cpr(p, product, month, 0.0)

    def test_the_incentive_is_the_loans_own_rate_over_todays(self):
        # Today's rate is the curve at 360 months (5%) plus the 1% spread: 6%.
        product = self.product()
        self.assertAlmostEqual(self.speed(product, pos(rate=0.08)), 0.08 + 0.06 * 2.0, places=12)
        self.assertAlmostEqual(self.speed(product, pos(rate=0.03)), 0.02, places=12)   # locked in: the floor
        self.assertAlmostEqual(self.speed(product, pos(rate=0.06)), 0.08, places=12)

    def test_a_shock_moves_the_incentive(self):
        product = self.product()
        self.assertAlmostEqual(self.speed(product, pos(rate=0.06), scenario=Scenario("-100", -100)),
                               0.08 + 0.06 * 1.0, places=12)

    def test_seasoning_ramps_a_new_loan(self):
        product = self.product(refi_incentive=False, seasoning_months=30)
        self.assertAlmostEqual(self.speed(product, pos(loan_age=0)), 0.08 / 30, places=12)
        self.assertAlmostEqual(self.speed(product, pos(loan_age=40)), 0.08, places=12)
        self.assertAlmostEqual(self.speed(product, pos()), 0.08, places=12)      # unknown age: seasoned

    def test_burnout_takes_the_refinancing_speed_away(self):
        product = self.product(burnout=0.25)
        p = pos(rate=0.08)
        first = self.speed(product, p)
        for _ in range(11):
            last = self.speed(product, p)
        self.assertEqual(p.in_money_months, 12)
        self.assertAlmostEqual(first, 0.08 + 0.12 * math.exp(-0.25 / 12), places=12)
        self.assertAlmostEqual(last, 0.08 + 0.12 * math.exp(-0.25), places=12)


class Wiring(unittest.TestCase):
    def test_indexes_and_basis_round_trip_through_the_workbook(self):
        raw = settings.load(settings.find(os.path.join(ROOT, "examples", "sample-cu")))
        raw["indexes"]["PRIME"] = {"tenor_months": 1, "spread": 0.0, "beta": 80.0, "curve": {"1": 7.5, "12": 7.25}}
        raw.setdefault("extra_scenarios", []).append({"name": "prime lags", "shock_bp": 100,
                                                      "basis": {"PRIME": -50, "shares": 25}})
        back = settings.from_workbook(settings.to_workbook(raw))
        self.assertEqual(back["indexes"]["PRIME"]["beta"], 80.0)
        self.assertEqual(back["indexes"]["PRIME"]["curve"], {"1": 7.5, "12": 7.25})
        self.assertEqual(back["extra_scenarios"][-1]["basis"], {"PRIME": -50.0, "shares": 25.0})
        a = model.parse_assumptions(back)
        self.assertAlmostEqual(a.indexes["PRIME"].beta, 0.8)
        self.assertEqual(a.scenarios[-1].basis, {"PRIME": -50.0, "shares": 25.0})

    def test_imported_loans_carry_their_age(self):
        positions, _, _, _ = load(os.path.join(ROOT, "examples", "mid-cu"))
        aged = [p for p in positions if p.id.startswith("loan") and p.amortization == "level"]
        self.assertTrue(aged and all(p.loan_age is not None and p.loan_age >= 0 for p in aged))

    def test_the_basis_table(self):
        positions, a, _, _ = load(os.path.join(ROOT, "examples", "mid-cu"))
        rows = basis.run(positions, a)
        prime = {x["move_bp"]: x for x in rows if x["key"] == "PRIME"}
        self.assertGreater(prime[-50]["exposure_assets"], 0)
        self.assertLess(prime[-50]["y1_change"], 0)          # prime loans earn less
        self.assertGreater(prime[50]["y1_change"], 0)
        shares = {x["move_bp"]: x for x in rows if x["key"] == "shares"}
        self.assertLess(shares[25]["y1_change"], 0)          # paying more on shares costs NII


if __name__ == "__main__":
    unittest.main()
