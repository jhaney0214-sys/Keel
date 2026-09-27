"""The import, the supervisory test, instruments and what-ifs.

Each test pins a behaviour that was either specified by an outside source
(NCUA's standardized share prices) or found wrong once while building
(FHLB stock at 150% of book, an advance that raised liquidity, a sale at
book that hid its loss).
"""

import copy
import csv
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from keel import engine, importer, measures, model, whatif  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402

AS_OF = datetime.date(2026, 6, 30)


def assumptions(products, **kw):
    base = dict(as_of="2026-06-30", curve=Curve({"1": 5.0, "360": 5.0}), indexes={"IDX": (12, 0.0)},
                products=products, scenarios=[Scenario("base", 0), Scenario("+300", 300), Scenario("-300", -300)])
    base.update(kw)
    return model.Assumptions(**base)


def pos(**kw):
    base = dict(id="x", name="x", product="p", side="asset", balance=100000.0, rate=0.05,
                rate_type="fixed", term_months=60, amortization="bullet")
    base.update(kw)
    return model.Position(**base)


class Supervisory(unittest.TestCase):

    def test_shares_take_ncuas_standardized_prices(self):
        """99.00 in the base case, 95.04 at +300bp, whatever the model says."""
        a = assumptions({"s": model.Product("s", runoff=0.1)})
        share = pos(id="s1", product="s", side="liability", rate=0.001, rate_type="administered",
                    amortization="nonmaturity", term_months=0)
        base = measures.nev([share], a, Scenario("base", 0), supervisory=True)
        up = measures.nev([share], a, Scenario("+300", 300), supervisory=True)
        self.assertAlmostEqual(base.pv_liabilities, 99000.0, places=6)
        self.assertAlmostEqual(up.pv_liabilities, 95040.0, places=6)

    def test_only_shares_are_standardized(self):
        a = assumptions({"p": model.Product("p")})
        bond = pos()
        self.assertAlmostEqual(measures.nev([bond], a, Scenario("base", 0), supervisory=True).pv_assets,
                               measures.nev([bond], a, Scenario("base", 0)).pv_assets, places=6)

    def test_other_shocks_have_no_supervisory_price(self):
        a = assumptions({"p": model.Product("p")})
        with self.assertRaises(ValueError):
            measures.nev([pos()], a, Scenario("+200", 200), supervisory=True)

    def test_the_rating_uses_the_nev_percent_change(self):
        base = measures.NEV("base", 1000.0, 900.0, {})
        up = measures.NEV("+300", 950.0, 890.0, {})          # NEV 100 -> 60: a 40% decline
        t = measures.ncua_test(base, up)
        self.assertAlmostEqual(t["sensitivity_value_decline"], 0.40)
        self.assertEqual(t["sensitivity_rating"], "Moderate")


class Instruments(unittest.TestCase):

    def test_a_balloon_amortizes_long_and_pays_off_at_maturity(self):
        a = assumptions({"p": model.Product("p")})
        loan = pos(amortization="balloon", term_months=12, amort_months=300, rate=0.06)
        flows = engine.runoff([loan], a, Scenario("base", 0))["x"]
        self.assertEqual(len(flows), 12)
        self.assertLess(flows[0].principal, 200.0)                  # amortizing slowly
        self.assertGreater(flows[-1].principal, 90000.0)            # the balloon
        self.assertAlmostEqual(sum(f.principal for f in flows), 100000.0, places=4)

    def test_a_callable_is_called_when_rates_fall_and_kept_when_they_rise(self):
        a = assumptions({"p": model.Product("p", call_threshold=0.0025)})
        bond = pos(rate=0.05, amortization="callable", term_months=60, call_months=12)
        down = engine.runoff([bond], a, Scenario("-300", -300))["x"]
        up = engine.runoff([bond], a, Scenario("+300", 300))["x"]
        self.assertEqual(len(down), 12)                              # called at its first call date
        self.assertEqual(len(up), 60)                                # held to maturity

    def test_an_arm_resets_on_its_own_date_then_on_schedule(self):
        a = assumptions({"p": model.Product("p")})
        arm = pos(rate=0.03, rate_type="variable", index="IDX", margin=0.02, reset_months=12,
                  next_reset_months=3, amortization="level", term_months=300)
        s = engine.Stepper(a, Scenario("base", 0))
        rates = []
        for month in range(1, 17):
            s.step(arm, month)
            rates.append(round(arm.rate, 6))
        self.assertEqual(rates[2], 0.03)             # before the first reset
        self.assertEqual(rates[3], 0.07)             # reset at age 3: 5% + 2%
        self.assertEqual(rates[14], 0.07)            # next at age 15

    def test_stock_with_no_maturity_is_valued_at_book(self):
        """FHLB stock paying 7% once valued at 150% of book as a perpetuity."""
        a = assumptions({"p": model.Product("p")})
        stock = pos(rate=0.07, amortization="none", term_months=0)
        self.assertEqual(measures.nev([stock], a, Scenario("base", 0)).pv_assets, 100000.0)

    def test_a_shaped_scenario_moves_the_ends_differently(self):
        flattener = Scenario("flat", shape={"1": 200, "120": 0})
        c = Curve({"1": 4.0, "360": 4.0})
        self.assertAlmostEqual(flattener.rate(c, 0, 1), 6.0)
        self.assertAlmostEqual(flattener.rate(c, 0, 120), 4.0)
        self.assertFalse(flattener.parallel)
        self.assertTrue(flattener.instantaneous)


class Liquidity(unittest.TestCase):

    def test_survival_looks_only_at_the_first_year(self):
        months = [engine.Month(month=k, balances={}, interest={}) for k in range(1, 61)]
        for m in months:
            m.available_liquidity = -1.0 if m.month >= 48 else 1.0
        self.assertIsNone(measures.survival(months))
        months[5].available_liquidity = -1.0
        self.assertEqual(measures.survival(months), 6)


class Import(unittest.TestCase):
    """The small generated credit union, imported and tied."""

    @classmethod
    def setUpClass(cls):
        import make_samples
        cls.tmp = tempfile.mkdtemp()
        make_samples.ROOT = cls.tmp
        make_samples.Generator("small-cu", make_samples.PROFILES["small-cu"]).run()
        cls.folder = os.path.join(cls.tmp, "examples", "small-cu")
        cls.a = model.read_assumptions(os.path.join(cls.folder, "assumptions.json"))
        cls.imp = importer.import_folder(cls.folder, AS_OF)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_every_detail_file_ties_to_the_ledger(self):
        for tie in self.imp.ties:
            self.assertTrue(tie.ties, "%s off by %.2f" % (tie.line, tie.difference))

    def test_pooling_keeps_every_dollar(self):
        with open(os.path.join(self.folder, "data", "loans.csv"), encoding="utf-8") as handle:
            detail = sum(float(r["current_balance"]) for r in csv.DictReader(handle))
        pooled = sum(p.balance for p in self.imp.positions if p.id.startswith("loan"))
        self.assertAlmostEqual(pooled, detail, delta=0.01 * len(self.imp.positions))

    def test_equity_is_the_ledgers_net_worth(self):
        with open(os.path.join(self.folder, "data", "gl.csv"), encoding="utf-8") as handle:
            gl = {r["account"]: float(r["balance"]) for r in csv.DictReader(handle)}
        net_worth = -(gl["3900"] + gl["3910"])
        _, assets, liabilities, equity = engine.opening(self.imp.positions)
        self.assertAlmostEqual(equity, net_worth, delta=1.0)

    def test_the_imported_book_projects_and_reconciles(self):
        model.check(self.imp.positions, self.a)
        runs = {s.name: engine.going_concern(self.imp.positions, self.a, s) for s in self.a.scenarios[:3]}
        runs["base"] = runs[self.a.scenarios[0].name]
        for c in measures.reconcile(self.imp.positions, self.a, runs):
            self.assertTrue(c.passed, "%s: %s" % (c.name, c.detail))

    def test_a_loan_ninety_days_late_earns_nothing(self):
        late = [p for p in self.imp.positions if "non-accrual" in p.name]
        self.assertTrue(late, "the sample has non-accrual loans")
        self.assertTrue(all(p.rate == 0.0 for p in late))
        on_time = [p for p in self.imp.positions if p.id.startswith("loan") and "non-accrual" not in p.name]
        self.assertTrue(all(p.rate > 0 for p in on_time))

    def test_an_unmapped_loan_code_is_refused(self):
        folder = tempfile.mkdtemp()
        try:
            shutil.copytree(self.folder, os.path.join(folder, "cu"))
            path = os.path.join(folder, "cu", "data", "product_map.json")
            with open(path, encoding="utf-8") as handle:
                mapping = json.load(handle)
            del mapping["loans"]["CARD"]
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(mapping, handle)
            with self.assertRaisesRegex(model.InputError, "does not map"):
                importer.import_folder(os.path.join(folder, "cu"), AS_OF)
        finally:
            shutil.rmtree(folder)

    def test_the_generator_is_deterministic(self):
        import make_samples
        other = tempfile.mkdtemp()
        try:
            make_samples.ROOT = other
            make_samples.Generator("small-cu", make_samples.PROFILES["small-cu"]).run()
            for name in ("loans.csv", "investments.csv", "gl.csv"):
                with open(os.path.join(self.folder, "data", name), encoding="utf-8") as a, \
                        open(os.path.join(other, "examples", "small-cu", "data", name), encoding="utf-8") as b:
                    self.assertEqual(a.read(), b.read(), name)
        finally:
            make_samples.ROOT = self.tmp
            shutil.rmtree(other)


class WhatIf(unittest.TestCase):

    def setUp(self):
        self.raw = {"as_of": "2026-06-30", "curve": {"1": 5.0, "360": 5.0},
                    "indexes": {}, "products": {"cash": {}, "bond": {"liquid": True}, "borrowings": {}},
                    "liquidity": {"contingent": [{"name": "FHLB", "capacity": 1000000}]}}
        self.book = [pos(id="cash", product="cash", balance=500000.0, rate=0.0, rate_type="none",
                         amortization="none", term_months=0),
                     pos(id="b1", product="bond", balance=400000.0, rate=0.02, term_months=120)]

    def test_an_assumption_changes_by_its_path_in_file_units(self):
        spec = {"assumptions": {"products.bond.haircut": 7}}
        _, a, _ = whatif.apply(self.book, self.raw, spec)
        self.assertAlmostEqual(a.products["bond"].haircut, 0.07)

    def test_a_borrowing_adds_cash_and_draws_its_source_down(self):
        """An FHLB advance once *raised* stress liquidity by counting capacity twice."""
        spec = {"actions": [{"add": {"id": "adv", "product": "borrowings", "side": "liability",
                                     "balance": 300000, "rate": 4, "term_months": 36, "draws_on": "FHLB"}}]}
        book, a, _ = whatif.apply(self.book, self.raw, spec)
        self.assertEqual([p.balance for p in book if p.id == "cash"], [800000.0])
        self.assertEqual(a.contingent, [("FHLB", 700000.0)])

    def test_a_sale_is_priced_at_market_and_realizes_its_loss(self):
        """A 2% ten-year bond on a 5% curve is underwater; selling half at book hid that."""
        spec = {"actions": [{"scale": {"product": "bond", "factor": 0.5}}]}
        book, _, notes = whatif.apply(self.book, self.raw, spec)
        cash = [p.balance for p in book if p.id == "cash"][0]
        self.assertLess(cash, 700000.0)                  # proceeds below the 200,000 book sold
        self.assertTrue(any("realized loss" in n for n in notes))
        _, _, _, equity_after = engine.opening(book)
        _, _, _, equity_before = engine.opening(self.book)
        self.assertLess(equity_after, equity_before)

    def test_the_base_book_is_not_changed(self):
        before = copy.deepcopy([p.balance for p in self.book])
        whatif.apply(self.book, self.raw, {"actions": [{"scale": {"product": "bond", "factor": 0.1}}]})
        self.assertEqual([p.balance for p in self.book], before)

    def test_an_unknown_path_is_refused(self):
        with self.assertRaises(model.InputError):
            whatif.apply(self.book, self.raw, {"assumptions": {"nothing.here": 1}})


if __name__ == "__main__":
    unittest.main()
