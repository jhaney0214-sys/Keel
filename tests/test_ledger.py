"""Actuals from the general ledger's monthly trial balance.

A small ledger worked by hand: natural signs turned around by the line,
year-to-date accounts differenced and restarted at the fiscal year, two
accounts on one line added, unmapped accounts ignored. Then the mid-cu
sample, whose trial balance holds the same numbers as the actuals it
replaced, and whose analysis-date month ties to its positions.
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import budget, engine, history, ledger, model, results  # noqa: E402
from keel.__main__ import load  # noqa: E402

SIDES = {"auto": "asset", "certificates": "liability"}
MAP = """account,line,measure
1210,auto,balance
1211,auto,balance
4110,auto,ytd
3500,certificates,balance
5300,certificates,activity
4400,fee_income,ytd
6000,operating_expense,ytd
"""
TB = """month,account,balance
2026-11,1210,900
2026-11,1211,100
2026-11,4110,-550
2026-11,3500,-2000
2026-11,5300,7
2026-11,4400,-110
2026-11,6000,440
2026-11,3900,-12345
2026-12,1210,1100
2026-12,1211,100
2026-12,4110,-610
2026-12,3500,-2200
2026-12,5300,8
2026-12,4400,-120
2026-12,6000,480
2027-01,1210,1200
2027-01,1211,100
2027-01,4110,-9
2027-01,3500,-2400
2027-01,5300,9
2027-01,4400,-11
2027-01,6000,40
"""


def folder(tb=TB, gl_map=MAP, extra=None):
    tmp = tempfile.mkdtemp()
    for name, text in (("trial_balance.csv", tb), ("gl_map.csv", gl_map)) + tuple(extra or ()):
        with open(os.path.join(tmp, name), "w") as handle:
            handle.write(text)
    return tmp


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = folder()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rows(self, start=1):
        return {(r["month"], r["line"]): r for r in ledger.read(self.tmp, SIDES, start)}

    def test_year_to_date_is_differenced_and_restarts(self):
        r = self.rows()
        self.assertNotIn(("2026-11", "auto"), r)             # no October year-to-date to difference
        self.assertAlmostEqual(r[("2026-12", "auto")]["amount"], 60)
        self.assertAlmostEqual(r[("2027-01", "auto")]["amount"], 9)    # January starts the year
        self.assertAlmostEqual(r[("2026-12", "fee_income")]["amount"], 10)
        self.assertAlmostEqual(r[("2026-12", "operating_expense")]["amount"], 40)

    def test_a_fiscal_year_starting_in_december(self):
        r = self.rows(start=12)
        self.assertAlmostEqual(r[("2026-12", "auto")]["amount"], 610)
        self.assertAlmostEqual(r[("2027-01", "auto")]["amount"], 9 - 610)

    def test_activity_is_the_month_itself(self):
        r = self.rows()
        self.assertAlmostEqual(r[("2026-11", "certificates")]["amount"], 7)
        self.assertAlmostEqual(r[("2027-01", "certificates")]["amount"], 9)

    def test_balances_average_two_month_ends_and_add_accounts(self):
        r = self.rows()
        self.assertAlmostEqual(r[("2026-12", "auto")]["average_balance"], (1000 + 1200) / 2.0)
        self.assertAlmostEqual(r[("2026-12", "certificates")]["average_balance"], 2100)   # a credit, turned around
        self.assertAlmostEqual(r[("2026-11", "certificates")]["average_balance"], 2000)   # no prior month-end

    def test_month_ends_for_the_tie(self):
        self.assertEqual(ledger.month_ends(self.tmp, SIDES, "2026-12"), {"auto": 1200.0, "certificates": 2200.0})

    def test_both_sources_are_refused(self):
        with open(os.path.join(self.tmp, "actuals.csv"), "w") as handle:
            handle.write("month,line,average_balance,amount\n")
        with self.assertRaisesRegex(model.InputError, "keep one source"):
            budget.actual_rows(self.tmp, SIDES)

    def test_bad_maps_are_refused(self):
        for text, message in (("account,line,measure\n1,nowhere,balance\n", "neither a product"),
                              ("account,line,measure\n1,auto,monthly\n", "measure must be"),
                              ("account,line,measure\n1,fee_income,balance\n", "has no balance"),
                              ("account,line,measure\n1,auto,balance\n1,auto,ytd\n", "mapped twice")):
            tmp = folder(gl_map=text)
            try:
                with self.assertRaisesRegex(model.InputError, message):
                    ledger.read(tmp, SIDES)
            finally:
                shutil.rmtree(tmp)
        os.remove(os.path.join(self.tmp, "gl_map.csv"))
        with self.assertRaisesRegex(model.InputError, "gl_map"):
            ledger.read(self.tmp, SIDES)

    def test_the_back_test_reads_it(self):
        nii = history._actual_nii(self.tmp, ["2026-12", "2027-01"], SIDES)
        self.assertAlmostEqual(nii["2026-12"], 60 - 8)
        self.assertAlmostEqual(nii["2027-01"], 9 - 9)

    def test_fiscal_year_start_must_be_a_month(self):
        with self.assertRaisesRegex(model.InputError, "fiscal_year_start"):
            model._fiscal(13)


class Sample(unittest.TestCase):
    """mid-cu's trial balance: its variance, and its tie to the positions."""

    @classmethod
    def setUpClass(cls):
        cls.folder = os.path.join(ROOT, "examples", "mid-cu")
        cls.positions, cls.a, _, _ = load(cls.folder)
        cls.b = budget.build(cls.positions, cls.a, engine.going_concern(cls.positions, cls.a, cls.a.scenarios[0]))

    def test_the_variance_comes_from_the_ledger(self):
        sides = {p["product"]: p["side"] for p in self.b["products"]}
        v = budget.variance(self.b, budget.read_actuals(self.folder, self.b["labels"], sides, self.a.fiscal_year_start))
        self.assertEqual(v["source"], "trial_balance")
        self.assertEqual(v["months"], 3)
        for row in v["products"]:
            self.assertAlmostEqual(row["volume"] + row["rate"], row["nii_variance"], places=4)

    def test_the_analysis_date_ties(self):
        check = results.ledger_tie(self.folder, self.positions, self.a)
        self.assertTrue(check.passed, check.detail)


if __name__ == "__main__":
    unittest.main()
