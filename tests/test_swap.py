"""Investment purchases and swaps.

The settlement is held to accounting: proceeds are market (or the given
price), the difference from book comes out of equity, and cash moves by
exactly what changes hands. The earn-back is worked on a made-up NII path.
Then mid-cu's loss swap and Treasury purchase run end to end.
"""

import collections
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import engine, model, swap  # noqa: E402
from keel.__main__ import load  # noqa: E402

MID = os.path.join(ROOT, "examples", "mid-cu")
TRADES = os.path.join(ROOT, "examples", "trades")
Month = collections.namedtuple("Month", "month nii")


class Settlement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.positions, cls.a, _, _ = load(MID)
        with open(os.path.join(TRADES, "loss-swap.json"), encoding="utf-8") as handle:
            cls.spec = json.load(handle)

    def cash(self, book):
        return sum(p.balance for p in book if p.product == engine.CASH)

    def test_a_sale_at_market_realizes_the_difference_in_equity(self):
        sid = self.spec["sell"][0]
        changed, t = swap.build(self.positions, self.a, {"sell": [sid]})
        sold = t["sold"][0]
        self.assertAlmostEqual(t["realized"], sold["market"] - sold["book"], places=4)
        self.assertAlmostEqual(self.cash(changed) - self.cash(self.positions), sold["proceeds"], places=4)
        self.assertNotIn(sid, {p.id for p in changed})
        before, after = engine.opening(self.positions)[3], engine.opening(changed)[3]
        self.assertAlmostEqual(after - before, t["realized"], places=2)

    def test_a_given_price_and_a_partial_sale(self):
        sid = self.spec["sell"][0]
        book = next(p.balance for p in self.positions if p.id == sid)
        changed, t = swap.build(self.positions, self.a, {"sell": [{"id": sid, "share": 0.5, "price": 90}]})
        self.assertAlmostEqual(t["sold"][0]["proceeds"], book * 0.5 * 0.9, places=4)
        self.assertAlmostEqual(next(p.balance for p in changed if p.id == sid), book * 0.5, places=4)

    def test_buying_the_proceeds_leaves_cash_where_it_was(self):
        changed, t = swap.build(self.positions, self.a, self.spec)
        self.assertAlmostEqual(t["spent"], t["proceeds"], places=4)
        self.assertAlmostEqual(self.cash(changed), self.cash(self.positions), places=2)

    def test_a_spread_is_over_the_curve_at_the_term(self):
        _, t = swap.build(self.positions, self.a, self.spec)
        self.assertAlmostEqual(t["bought"][0]["yield"], (self.a.curve.rate(360) + 0.90) / 100.0)

    def test_bad_trades_are_refused(self):
        liability = next(p.id for p in self.positions if p.side == "liability")
        for spec, message in (({"sell": ["NOPE"]}, "not a position"), ({"sell": [liability]}, "not an asset"),
                              ({"buy": [{"product": "treasuries", "amount": 1e6}]}, "yield or a spread"),
                              ({"buy": [{"product": "nothing", "amount": 1e6, "yield": 4}]}, "not a product"),
                              ({}, "sells and buys nothing")):
            with self.assertRaisesRegex(model.InputError, message):
                swap.build(self.positions, self.a, spec)


class EarnBack(unittest.TestCase):
    def test_the_first_month_the_extra_income_covers_the_loss(self):
        before = [Month(m, 100.0) for m in range(1, 13)]
        after = [Month(m, 130.0) for m in range(1, 13)]
        month, path = swap.earn_back(before, after, 100.0)
        self.assertEqual(month, 4)                 # 30, 60, 90, 120
        self.assertAlmostEqual(path[-1], 360.0)

    def test_never_and_nothing_to_earn(self):
        flat = [Month(m, 100.0) for m in range(1, 13)]
        self.assertIsNone(swap.earn_back(flat, flat, 50.0)[0])
        self.assertEqual(swap.earn_back(flat, flat, -10.0)[0], 0)


class Sample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.positions, cls.a, _, _ = load(MID)

    def run_trade(self, name):
        with open(os.path.join(TRADES, name), encoding="utf-8") as handle:
            return swap.analyse(self.positions, self.a, json.load(handle))

    def test_the_loss_swap_earns_back_in_the_base_plan(self):
        r = self.run_trade("loss-swap.json")
        self.assertLess(r["trade"]["realized"], 0)
        self.assertGreater(r["summary"]["pickup"], 0)
        self.assertIsNotNone(r["runs"]["base"]["earn_back"])
        self.assertIn("Earning it back", swap.page(r, ""))

    def test_a_purchase_from_cash(self):
        r = self.run_trade("buy-treasuries.json")
        self.assertEqual(r["trade"]["sold"], [])
        self.assertAlmostEqual(r["trade"]["cash_change"], -10e6)
        # A fixed two-year note gains against cash when rates fall and lags when they rise.
        self.assertGreater(r["runs"]["-300"]["years"][0]["change"], r["runs"]["+300"]["years"][0]["change"])


if __name__ == "__main__":
    unittest.main()
