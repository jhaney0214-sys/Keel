"""Deposit pricing: the marginal cost of new money.

A rate move's marginal cost has a closed form, r + d + 1 / (100 x
sensitivity), and a special's is worked on a small book; the break-even
new-money share is plugged back in to land exactly on wholesale.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import depositpricing, model  # noqa: E402
from keel.__main__ import load  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402


def book():
    products = {"cash": model.Product("cash"),
                "mma": model.Product("mma", runoff_per_100bp=0.05),
                "cds": model.Product("cds", spread=-0.002)}
    a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={}, products=products,
                          scenarios=[Scenario("base", 0)])
    positions = [
        model.Position(id="cash", name="cash", product="cash", side="asset", balance=50e6, rate=0.0,
                       rate_type="none", amortization="none"),
        model.Position(id="m", name="m", product="mma", side="liability", balance=20e6, rate=0.02,
                       rate_type="administered", amortization="nonmaturity"),
        model.Position(id="c1", name="c1", product="cds", side="liability", balance=10e6, rate=0.03,
                       rate_type="fixed", term_months=2, amortization="bullet"),
        model.Position(id="c2", name="c2", product="cds", side="liability", balance=15e6, rate=0.035,
                       rate_type="fixed", term_months=9, amortization="bullet"),
    ]
    return positions, a


SPEC = {"rate": 4.5, "term_months": 12, "window_months": 3, "volume": 10e6, "product": "cds",
        "maturing_renewal": 50, "sources": {"new_money": 60, "mma": 40}, "wholesale_spread": 0.15}


class Moves(unittest.TestCase):
    def test_the_marginal_cost_has_a_closed_form(self):
        positions, a = book()
        m = depositpricing.rate_moves(positions, a)
        mma = next(p for p in m["products"] if p["product"] == "mma")
        for c in mma["moves"]:
            self.assertAlmostEqual(c["marginal"], 0.02 + c["move"] / 10000.0 + 1 / (100 * 0.05), places=10)

    def test_a_cut_stops_at_the_floor(self):
        positions, a = book()
        positions[1].rate = 0.001            # 10bp, over a 0% floor
        mma = depositpricing.rate_moves(positions, a)["products"][0]
        cut = {c["move"]: c for c in mma["moves"]}
        self.assertEqual(cut[-25]["applied"], -10)
        self.assertEqual(cut[-50]["applied"], -10)
        self.assertEqual(cut[25]["applied"], 25)

    def test_the_study_is_used_only_when_it_fits(self):
        positions, a = book()
        good = {"products": [{"product": "mma", "sensitivity": {"runoff_per_100bp": 0.08, "r2": 0.5}}]}
        poor = {"products": [{"product": "mma", "sensitivity": {"runoff_per_100bp": 0.08, "r2": 0.1}}]}
        self.assertEqual(depositpricing.sensitivity("mma", a, good)[0], 0.08)
        self.assertEqual(depositpricing.sensitivity("mma", a, poor), (0.05, "settings"))


class Special(unittest.TestCase):
    def setUp(self):
        self.positions, self.a = book()

    def test_worked_by_hand(self):
        s = depositpricing.special(self.positions, self.a, SPEC)
        standard = 0.04 - 0.002
        # $10M maturing in the window, half renews: $5M; of the other $5M, 60% new.
        self.assertAlmostEqual(s["rows"][0]["balance"], 5e6)
        self.assertAlmostEqual(s["new_money"], 3e6)
        cost = 10e6 * 0.045 - 5e6 * standard - 2e6 * 0.02
        self.assertAlmostEqual(s["incremental_cost"], cost, places=4)
        self.assertAlmostEqual(s["marginal"], cost / 3e6, places=10)
        self.assertAlmostEqual(s["wholesale"], 0.0415, places=10)

    def test_at_the_break_even_share_it_costs_wholesale(self):
        # Even all-new money costs the special's rate plus the renewals it
        # reprices, so a break-even needs a special priced under wholesale.
        cheap = dict(SPEC, rate=4.0, maturing_renewal=20)
        s = depositpricing.special(self.positions, self.a, cheap)
        n = s["breakeven_new_share"]
        self.assertTrue(0 < n < 1, n)
        at = dict(cheap, sources={"new_money": 100 * n, "mma": 100 * (1 - n)})
        self.assertAlmostEqual(depositpricing.special(self.positions, self.a, at)["marginal"], s["wholesale"],
                               places=10)

    def test_the_grid_moves_the_right_way(self):
        grid = depositpricing.special(self.positions, self.a, SPEC)["grid"]
        self.assertLess(grid[0]["cells"][0]["marginal"], grid[-1]["cells"][0]["marginal"])   # dearer at a higher rate
        self.assertGreater(grid[0]["cells"][0]["marginal"], grid[0]["cells"][-1]["marginal"])  # cheaper with more new

    def test_when_renewals_take_the_whole_volume(self):
        spec = dict(SPEC, volume=4e6)                   # under the $5M of expected renewals
        s = depositpricing.special(self.positions, self.a, spec)
        self.assertEqual(s["new_money"], 0.0)
        self.assertIsNone(s["marginal"])
        page = depositpricing.page({"special": s, "moves": None}, "")
        self.assertIn("no new money", page)
        self.assertNotIn("n/a", page)

    def test_bad_specials_are_refused(self):
        for spec, message in ((dict(SPEC, sources={"new_money": 50}), "add to 100"),
                              (dict(SPEC, sources={"new_money": 10, "nothing": 90}), "not a liability"),
                              (dict(SPEC, volume=1e9, sources={"new_money": 0, "mma": 100}), "cannot give"),
                              ({"rate": 4}, "give rate")):
            with self.assertRaisesRegex(model.InputError, message):
                depositpricing.special(self.positions, self.a, spec)


class Sample(unittest.TestCase):
    def test_mid_cu(self):
        positions, a, _, _ = load(os.path.join(ROOT, "examples", "mid-cu"))
        import json
        with open(os.path.join(ROOT, "examples", "specials", "13-month-special.json"), encoding="utf-8") as handle:
            spec = json.load(handle)
        result = {"special": depositpricing.special(positions, a, spec),
                  "moves": depositpricing.rate_moves(positions, a)}
        self.assertLess(result["special"]["new_money"], spec["volume"])
        self.assertIn("marginal cost of new money", depositpricing.page(result, ""))


if __name__ == "__main__":
    unittest.main()
