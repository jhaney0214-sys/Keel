"""Regulatory capital categories and the unrealized-loss view."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import capital, measures, model  # noqa: E402
from keel.curve import Curve, standard_scenarios  # noqa: E402


def setup(institution="credit_union", equity=10e6):
    products = {"cash": model.Product("cash", risk_weight=0.0), "loan": model.Product("loan", charge_off=0.01),
                "bond": model.Product("bond", liquid=True, risk_weight=0.2), "shares": model.Product("shares")}
    a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={}, products=products,
                          scenarios=standard_scenarios(), institution=institution)
    P = model.Position
    book = [P("cash", "cash", "cash", "asset", 10e6, 0.0, "none"),
            P("l", "l", "loan", "asset", 60e6, 0.06, "fixed", term_months=60, amortization="level"),
            P("b", "b", "bond", "asset", 30e6, 0.04, "fixed", term_months=120, amortization="bullet"),
            P("s", "s", "shares", "liability", 100e6 - equity, 0.01, "administered", amortization="nonmaturity")]
    return book, a


class Categories(unittest.TestCase):

    def test_credit_union_bands(self):
        self.assertEqual([capital.cu_category(x) for x in (0.07, 0.065, 0.05, 0.03, 0.01)],
                         ["Well capitalized", "Adequately capitalized", "Undercapitalized",
                          "Significantly undercapitalized", "Critically undercapitalized"])

    def test_a_credit_union_below_500m_has_no_risk_based_ratio(self):
        book, a = setup(equity=6.5e6)
        c = capital.measures_for(book, a, [])
        self.assertEqual([x["measure"] for x in c["rows"]], ["Net worth ratio"])
        self.assertEqual((c["category"], c["rows"][0]["status"]), ("Adequately capitalized", "near"))

    def test_a_bank_is_measured_on_leverage_and_risk_based_ratios(self):
        book, a = setup("bank")
        c = capital.measures_for(book, a, [])
        names = [x["measure"] for x in c["rows"]]
        self.assertIn("Tier 1 leverage ratio", names)
        self.assertIn("Common equity Tier 1 ratio", names)
        cet1 = next(x for x in c["rows"] if x["measure"] == "Common equity Tier 1 ratio")
        self.assertAlmostEqual(cet1["value"], 10e6 / (60e6 + 0.2 * 30e6))


class UnrealizedLosses(unittest.TestCase):

    def test_a_rate_shock_cuts_the_market_value_and_the_ratio(self):
        book, a = setup()
        secs = measures.security_analytics(book, a, {"bond"})
        c = capital.measures_for(book, a, secs)
        lens = c["lens"]
        self.assertLess(lens["market_300"], lens["market"])
        self.assertLess(lens["ratio_300"], lens["ratio_now"])
        self.assertAlmostEqual(lens["unrealized_300"], lens["market_300"] - 30e6)


if __name__ == "__main__":
    unittest.main()
