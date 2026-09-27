"""Collateral-limited borrowing, graded liquidity stresses and deposit
concentration: each held to what it must do on a book small enough to
work by hand."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import liquidity, model, results, settings, xlsx  # noqa: E402
from keel.curve import Curve, standard_scenarios  # noqa: E402


def book():
    P = model.Position
    return [P("cash", "cash", "cash", "asset", 2e6, 0.0, "none"),
            P("mtg", "mtg", "mortgage", "asset", 10e6, 0.05, "fixed", term_months=300, amortization="level"),
            P("bond", "bond", "bond", "asset", 3e6, 0.04, "fixed", term_months=24, amortization="bullet"),
            P("sh", "sh", "shares", "liability", 12e6, 0.01, "administered", amortization="nonmaturity"),
            P("adv", "adv", "borrowings", "liability", 1e6, 0.045, "fixed", term_months=24, amortization="bullet")]


def assumptions(contingent, secured=(), stresses=None):
    products = {"cash": model.Product("cash"),
                "mortgage": model.Product("mortgage", cpr=0.06, collateral_value=0.75, new_term=360),
                "bond": model.Product("bond", liquid=True, haircut=0.05, new_term=24),
                "shares": model.Product("shares", runoff=0.1, stress_runoff=0.20),
                "borrowings": model.Product("borrowings")}
    return model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={}, products=products,
                             scenarios=standard_scenarios(), contingent=contingent, secured=frozenset(secured),
                             stresses=liquidity.scenarios(stresses), cash_minimum=5e5, stress_months=3)


class Collateral(unittest.TestCase):

    def test_a_secured_line_is_capped_by_collateral_left(self):
        """$10M of mortgages lend at 75%, $1M is borrowed: $6.5M is left, not the $20M line."""
        a = assumptions([("FHLB", 20e6), ("Bank line", 2e6)], secured=["FHLB"])
        contingent, notes = liquidity.effective_contingent(book(), a)
        self.assertEqual(dict(contingent), {"FHLB": 6.5e6, "Bank line": 2e6})
        self.assertEqual(notes[0]["collateral_headroom"], 6.5e6)

    def test_two_secured_lines_cannot_pledge_the_same_loans(self):
        a = assumptions([("FHLB", 5e6), ("Discount window", 5e6)], secured=["FHLB", "Discount window"])
        contingent, _ = liquidity.effective_contingent(book(), a)
        self.assertEqual(dict(contingent), {"FHLB": 5e6, "Discount window": 1.5e6})

    def test_securities_are_not_collateral_twice(self):
        lines, lendable = liquidity.collateral(book(), assumptions([]))
        self.assertEqual([l[0] for l in lines], ["mortgage"])
        self.assertEqual(lendable, 7.5e6)


class Scenarios(unittest.TestCase):

    def test_the_defaults_and_their_order_of_severity(self):
        a = assumptions([("FHLB", 3e6)], secured=["FHLB"])
        out = {s["name"]: s for s in liquidity.run(book(), a, a.stresses, uninsured=2e6)}
        self.assertEqual(list(out), ["As configured", "Severe", "Systemic", "Uninsured run"])
        self.assertLess(out["Severe"]["lowest"], out["As configured"]["lowest"])
        self.assertLess(out["Uninsured run"]["lowest"], out["As configured"]["lowest"])

    def test_scenarios_from_the_settings(self):
        s = liquidity.scenarios([{"name": "Mild", "runoff_multiplier": 0.5, "haircut_add": 2,
                                  "contingent_available": 80, "months": 2}])
        self.assertEqual((s[0].name, s[0].runoff_multiplier, s[0].haircut_add, s[0].contingent_available, s[0].months),
                         ("Mild", 0.5, 0.02, 0.8, 2))

    def test_the_stresses_sheet_and_secured_column_survive_the_workbook(self):
        import json
        with open(os.path.join(ROOT, "examples", "sample-cu", "assumptions.json"), encoding="utf-8") as h:
            raw = json.load(h)
        raw["liquidity"]["contingent"][0]["secured"] = True
        raw["liquidity_stresses"] = [{"name": "Mild", "runoff_multiplier": 0.5}]
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "a.xlsx")
            xlsx.write_workbook(path, settings.to_workbook(raw))
            a = model.parse_assumptions(settings.load(path))
            self.assertIn(raw["liquidity"]["contingent"][0]["name"], a.secured)
            self.assertEqual([s.name for s in a.stresses], ["Mild"])
        finally:
            shutil.rmtree(tmp)


class Concentration(unittest.TestCase):

    def test_uninsured_and_the_largest_members(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "depositors.csv"), "w") as h:
                h.write("member_id,balance\n")
                h.write("A,1000000\nB,300000\nC,250000\n")
                for i in range(97):
                    h.write("m%d,10000\n" % i)
            c = liquidity.concentration(book(), tmp)
            self.assertEqual(c["uninsured"], 750000 + 50000)
            self.assertEqual(c["over_limit"], 2)
            self.assertAlmostEqual(c["top10_share"], (1.55e6 + 7 * 10000) / (1.55e6 + 97 * 10000))
            a = assumptions([])
            a.limits = {"uninsured_shares_max": 20.0}
            r = results.compute(book(), a, "t", folder=tmp, assumption_tests=False)
            limit = next(x for x in r["limits"] if x.key == "uninsured_shares_max")
            self.assertAlmostEqual(limit.value, 100 * 0.8e6 / 2.52e6)
            self.assertEqual(limit.status, "breach")
        finally:
            shutil.rmtree(tmp)

    def test_without_the_file_it_is_not_measured(self):
        r = results.compute(book(), assumptions([]), "t", assumption_tests=False)
        limit = next(x for x in r["limits"] if x.key == "uninsured_shares_max")
        self.assertIsNone(limit.value)
        self.assertEqual(limit.status, "within")


if __name__ == "__main__":
    unittest.main()
