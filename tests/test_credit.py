"""Credit scenarios and the CECL estimate, held to answers worked by hand."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import credit, model  # noqa: E402
from keel.curve import Curve, standard_scenarios  # noqa: E402


def setup(charge_off=0.12):
    products = {"cash": model.Product("cash"), "loan": model.Product("loan", charge_off=charge_off, new_term=24),
                "shares": model.Product("shares", runoff=0.1), "allowance": model.Product("allowance")}
    a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={}, products=products,
                          scenarios=standard_scenarios(), credit_scenarios=credit.scenarios(None))
    P = model.Position
    book = [P("cash", "cash", "cash", "asset", 50000.0, 0.0, "none"),
            P("l", "l", "loan", "asset", 100000.0, 0.06, "fixed", term_months=24, amortization="bullet"),
            P("al", "al", "allowance", "asset", -15000.0, 0.0, "none"),
            P("s", "s", "shares", "liability", 120000.0, 0.01, "administered", amortization="nonmaturity")]
    return book, a


class Scenarios(unittest.TestCase):

    def test_the_multiplier_holds_then_phases_back(self):
        s = credit.CreditScenario("x", 3.0, 12, 12)
        self.assertEqual([s.factor(m) for m in (1, 12)], [3.0, 3.0])
        self.assertAlmostEqual(s.factor(18), 2.0)
        self.assertEqual(s.factor(25), 1.0)

    def test_defaults_and_a_baseline_is_always_there(self):
        self.assertEqual([s.name for s in credit.scenarios(None)],
                         ["Baseline", "Moderate recession", "Severe recession"])
        self.assertEqual([s.name for s in credit.scenarios([{"name": "Shock", "multiplier": 4, "months": 6}])],
                         ["Baseline", "Shock"])


class CECL(unittest.TestCase):

    def test_lifetime_loss_on_a_bullet_worked_by_hand(self):
        """1% a month on the balance still there; in month 24 the loan repays in full
        first, so losses run over 23 months: 1 - 0.99^23 of it."""
        book, a = setup()
        rows, total = credit.lifetime(book, a)
        self.assertAlmostEqual(total, 100000.0 * (1 - 0.99 ** 23), places=6)
        doubled = credit.CreditScenario("x2", 2.0, 24, 0)
        _, stressed = credit.lifetime(book, a, doubled)
        self.assertAlmostEqual(stressed, 100000.0 * (1 - 0.98 ** 23), places=6)

    def test_the_booked_allowance_and_the_scenarios(self):
        book, a = setup()
        r = credit.run(book, a, a.credit_scenarios)
        self.assertEqual(r["booked"], 15000.0)
        base, moderate, severe = r["scenarios"]
        self.assertEqual(base["allowance_build"], 0.0)
        self.assertGreater(severe["allowance_build"], moderate["allowance_build"])
        self.assertLess(severe["net_income_2y"], moderate["net_income_2y"])
        self.assertLess(severe["net_worth_after_build"], base["net_worth_after_build"])


if __name__ == "__main__":
    unittest.main()
