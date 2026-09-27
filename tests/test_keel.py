"""Keel's arithmetic against answers known independently of Keel.

A model a credit union's validator has to sign off on (SR 11-7 style) is only
as good as the cases it has been checked against, so most tests here pin a
closed-form result: an amortization payment, a bond priced at par, a beta.
"""

import dataclasses
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import curve, engine, measures, model  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")


def assumptions(products, curve_points=None, **kw):
    base = dict(as_of="2026-06-30", curve=Curve(curve_points or {"1": 5.0, "360": 5.0}),
                indexes={"IDX": (12, 0.0)}, products=products, scenarios=curve.standard_scenarios())
    base.update(kw)
    return model.Assumptions(**base)


def pos(**kw):
    base = dict(id="x", name="x", product="p", side="asset", balance=100000.0, rate=0.06,
                rate_type="fixed", term_months=360, amortization="level")
    base.update(kw)
    return model.Position(**base)


BASE = Scenario("base", 0)


class Arithmetic(unittest.TestCase):

    def test_a_level_loan_pays_the_textbook_payment(self):
        """$100,000 over 30 years at 6% pays $599.55 a month."""
        a = assumptions({"p": model.Product("p")})
        p = pos()
        f = engine.Stepper(a, BASE).step(p, 1)
        self.assertAlmostEqual(f.interest + f.principal, 599.55, places=2)
        self.assertAlmostEqual(f.interest, 500.00, places=2)

    def test_runoff_repays_the_balance_exactly(self):
        a = assumptions({"p": model.Product("p", cpr=0.1)})
        flows = engine.runoff([pos()], a, BASE)["x"]
        self.assertAlmostEqual(sum(f.principal for f in flows), 100000.0, places=4)

    def test_prepayment_is_the_monthly_rate_that_compounds_to_the_cpr(self):
        self.assertAlmostEqual(1 - (1 - engine.monthly(0.12)) ** 12, 0.12, places=12)

    def test_a_bond_yielding_its_discount_rate_is_worth_par(self):
        """Flat 5% curve, a 5% bullet, no spread: PV equals book to the cent."""
        a = assumptions({"p": model.Product("p")})
        bond = pos(rate=0.05, amortization="bullet", term_months=24)
        self.assertAlmostEqual(measures.nev([bond], a, BASE).pv_assets, 100000.0, places=2)

    def test_a_rate_rise_lowers_a_fixed_bonds_value(self):
        a = assumptions({"p": model.Product("p")})
        bond = pos(rate=0.05, amortization="bullet", term_months=60)
        self.assertLess(measures.nev([bond], a, Scenario("+300", 300)).pv_assets, 100000.0)

    def test_a_share_rate_moves_by_its_beta(self):
        """A 10% beta on a +100bp shock adds 10bp. It once added 1000%."""
        a = assumptions({"p": model.Product("p", beta=0.10, rate_floor=0.0005, runoff=0.1)})
        share = pos(side="liability", rate=0.001, rate_type="administered", amortization="nonmaturity",
                    term_months=0)
        engine.Stepper(a, Scenario("+100", 100)).step(share, 1)
        self.assertAlmostEqual(share.rate, 0.002, places=10)

    def test_every_percent_field_is_converted(self):
        numeric = {f.name for f in dataclasses.fields(model.Product)
                   if f.type in (float, "float")} - {"name"}
        self.assertEqual(numeric - set(model.PERCENT_FIELDS) - set(model.DOLLAR_FIELDS), set())

    def test_a_variable_rate_resets_to_index_plus_margin_within_its_cap(self):
        a = assumptions({"p": model.Product("p")})
        arm = pos(rate=0.05, rate_type="variable", index="IDX", margin=0.025, reset_months=12, cap=0.08)
        s = engine.Stepper(a, Scenario("+200", 200))
        for month in range(1, 13):
            s.step(arm, month)
        self.assertAlmostEqual(arm.rate, 0.05, places=10)       # not reset yet at age 11
        s.step(arm, 13)
        self.assertAlmostEqual(arm.rate, 0.08, places=10)       # 5% + 2% + 2.5% = 9.5%, capped at 8%

    def test_a_ramp_reaches_its_move_and_holds(self):
        r = Scenario("ramp", 200, ramp_months=12)
        self.assertEqual((r.shift_bp(0), r.shift_bp(6), r.shift_bp(12), r.shift_bp(30)), (0, 100, 200, 200))

    def test_the_curve_interpolates_and_holds_flat_past_its_ends(self):
        c = Curve({"12": 4.0, "24": 5.0})
        self.assertEqual((c.rate(1), c.rate(18), c.rate(360)), (4.0, 4.5, 5.0))

    def test_a_down_shock_is_floored(self):
        self.assertEqual(Scenario("-300", -300, floor=0.0).rate(Curve({"1": 1.0}), 0, 1), 0.0)


class Regulation(unittest.TestCase):

    def test_nev_bands_are_sl_22_01(self):
        self.assertEqual([measures.ratio_rating(r) for r in (0.08, 0.07, 0.05, 0.04, 0.02)],
                         ["Low", "Moderate", "Moderate", "High", "High"])
        self.assertEqual([measures.sensitivity_rating(s) for s in (0.2, 0.4, 0.5, 0.65, 0.9)],
                         ["Low", "Moderate", "Moderate", "High", "High"])

    def test_the_liquidity_tier_follows_741_12(self):
        self.assertIn("Under $50M", measures.cfp_tier(40e6))
        self.assertIn("$50M to $250M", measures.cfp_tier(50e6))
        self.assertIn("federal liquidity source", measures.cfp_tier(250e6))


class Sample(unittest.TestCase):
    """The synthetic credit union, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.positions = model.read_positions(os.path.join(SAMPLE, "positions.csv"))
        cls.a = model.read_assumptions(os.path.join(SAMPLE, "assumptions.json"))
        model.check(cls.positions, cls.a)
        cls.runs = {s.name: engine.going_concern(cls.positions, cls.a, s) for s in cls.a.scenarios}

    def test_every_reconciliation_check_passes(self):
        for c in measures.reconcile(self.positions, self.a, self.runs):
            self.assertTrue(c.passed, "%s: %s" % (c.name, c.detail))

    def test_the_reconciliation_catches_a_balance_sheet_that_does_not_balance(self):
        runs = dict(self.runs)
        broken = [dataclasses.replace(m) for m in runs["base"]]
        broken[5].equity += 1000.0
        runs["base"] = broken
        first = measures.reconcile(self.positions, self.a, runs)[0]
        self.assertFalse(first.passed)

    def test_the_plan_reaches_its_growth_targets(self):
        start = sum(p.balance for p in self.positions if p.product == "new_auto")
        self.assertAlmostEqual(self.runs["base"][11].balances["new_auto"], start * 1.04, delta=1.0)

    def test_the_stress_takes_its_runoff_from_money_market(self):
        stressed = engine.going_concern(self.positions, self.a, self.a.scenarios[0], stress=True)
        base = self.runs["base"]
        opening = 90e6
        lost = base[2].balances["money_market"] - stressed[2].balances["money_market"]
        self.assertGreater(lost, opening * 0.25)

    def test_up_shocks_lower_nev_for_this_liability_short_balance_sheet(self):
        base = measures.nev(self.positions, self.a, self.a.scenarios[0]).nev
        up = measures.nev(self.positions, self.a, Scenario("+300", 300)).nev
        self.assertLess(up, base)


class Inputs(unittest.TestCase):

    def write(self, rows):
        path = os.path.join(tempfile.mkdtemp(), "positions.csv")
        head = "id,name,product,side,balance,rate,rate_type,index,margin,reset_months,term_months,amortization,floor,cap\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(head + "\n".join(rows) + "\n")
        return path

    def test_a_term_loan_without_a_term_is_refused(self):
        with self.assertRaisesRegex(model.InputError, "needs term_months"):
            model.read_positions(self.write(["a,A,p,asset,100,5,fixed,,,,,level,,"]))

    def test_a_duplicate_id_is_refused(self):
        with self.assertRaisesRegex(model.InputError, "appears twice"):
            model.read_positions(self.write(["a,A,p,asset,100,5,fixed,,,,12,level,,",
                                             "a,B,p,asset,100,5,fixed,,,,12,level,,"]))

    def test_a_product_without_assumptions_is_refused(self):
        a = assumptions({"p": model.Product("p")})
        with self.assertRaisesRegex(model.InputError, "has no assumptions"):
            model.check([pos(product="q")], a)


if __name__ == "__main__":
    unittest.main()
