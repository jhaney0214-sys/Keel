"""Profitability by account, member and branch.

One bullet loan and one certificate on a flat curve are worked by hand;
members, the whale curve and branches are held to their definitions; and on
mid-cu the loan and certificate accounts add up to their products.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import accounts, model, profitability, query  # noqa: E402
from keel.__main__ import load  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402


def book():
    products = {"loan": model.Product("loan", fee_yield=0.001, servicing_cost=0.002, charge_off=0.005,
                                      risk_weight=1.0, account_cost=150.0),
                "cd": model.Product("cd", account_cost=25.0)}
    a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={}, products=products,
                          scenarios=[Scenario("base", 0)], target_capital=0.10)
    positions = [model.Position(id="L", name="L", product="loan", side="asset", balance=150000.0, rate=0.07,
                                rate_type="fixed", term_months=24, amortization="bullet"),
                 model.Position(id="C", name="C", product="cd", side="liability", balance=40000.0, rate=0.045,
                                rate_type="fixed", term_months=12, amortization="bullet")]
    detail = [
        {"kind": "loan", "id": "l1", "product": "loan", "position": "L", "balance": 100000.0, "rate": 0.06,
         "member_id": "A", "branch": "North"},
        {"kind": "loan", "id": "l2", "product": "loan", "position": "L", "balance": 50000.0, "rate": 0.09,
         "member_id": "B", "branch": "South", "days_delinquent": 75},
        {"kind": "certificate", "id": "c1", "product": "cd", "position": "C", "balance": 30000.0, "rate": 0.045,
         "member_id": "A", "branch": "South"},
        {"kind": "certificate", "id": "c2", "product": "cd", "position": "C", "balance": 10000.0, "rate": 0.045,
         "member_id": "", "branch": ""},
    ]
    return positions, a, detail


class ByHand(unittest.TestCase):
    def setUp(self):
        self.positions, self.a, detail = book()
        lines, _, totals = profitability.product_lines(self.positions, self.a)
        self.r = accounts.run(self.positions, self.a, detail, lines, totals)
        self.rows = {x["id"]: x for x in self.r["rows"]}

    def test_a_loan_worked_by_hand(self):
        x = self.rows["l1"]
        # 6% on $100k over a 4% FTP; capital $10k credited at 4%; fees 0.1%,
        # servicing 0.2%, expected loss 0.5%, and $150 for the account.
        want = 6000 - 4000 + 400 + 100 - 200 - 500 - 150
        self.assertAlmostEqual(x["net"], want, places=6)

    def test_a_certificate_earns_its_ftp_credit_less_its_rate(self):
        self.assertAlmostEqual(self.rows["c1"]["net"], 30000 * (0.04 - 0.045) - 25, places=6)

    def test_accounts_add_up_to_their_products(self):
        self.assertTrue(self.r["check"].passed, self.r["check"].detail)

    def test_members_roll_up_and_rank(self):
        members = {m["member_id"]: m for m in self.r["members"]}
        self.assertEqual(set(members), {"A", "B"})             # the certificate with no member is left out
        self.assertEqual(self.r["totals"]["unassigned"], 1)
        self.assertAlmostEqual(members["A"]["net"], self.rows["l1"]["net"] + self.rows["c1"]["net"], places=6)
        self.assertEqual(members["A"]["relationship"], "borrower and saver")
        self.assertEqual(members["B"]["relationship"], "borrower only")
        self.assertEqual(members["A"]["branch"], "North")        # most of A's balance is the loan
        self.assertTrue(members["B"]["delinquent"])
        self.assertGreaterEqual(self.r["members"][0]["net"], self.r["members"][-1]["net"])

    def test_the_whale_curve_ends_at_the_whole(self):
        self.assertEqual(self.r["whale"][0], (0.0, 0.0))
        self.assertAlmostEqual(self.r["whale"][-1][1], 1.0)

    def test_break_even_balance(self):
        p = next(x for x in self.r["products"] if x["product"] == "loan")
        per_dollar = (p["net"] + p["account_cost"]) / p["balance"]
        self.assertAlmostEqual(p["breakeven_balance"], 150.0 / per_dollar)
        cd = next(x for x in self.r["products"] if x["product"] == "cd")
        self.assertIsNone(cd["breakeven_balance"])                 # pays over FTP: loses at any size

    def test_no_detail_no_view(self):
        self.assertIsNone(accounts.run(self.positions, self.a, []))


class Sample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = os.path.join(ROOT, "examples", "mid-cu")
        cls.positions, cls.a, _, cls.imported = load(cls.folder)

    def test_the_sample_carries_members_and_branches(self):
        kinds = {x["kind"] for x in self.imported.accounts}
        self.assertEqual(kinds, {"loan", "certificate", "share"})
        self.assertTrue(all(x["member_id"] for x in self.imported.accounts))

    def test_member_shares_add_up_to_the_share_tiers(self):
        shares = sum(x["balance"] for x in self.imported.accounts if x["kind"] == "share")
        book = sum(p.balance for p in self.positions if p.id.startswith("share"))
        self.assertAlmostEqual(shares, book, places=0)

    def test_accounts_tie_to_products_and_query(self):
        lines, _, totals = profitability.product_lines(self.positions, self.a)
        r = accounts.run(self.positions, self.a, self.imported.accounts, lines, totals)
        self.assertTrue(r["check"].passed, r["check"].detail)
        data = query.Tables(self.positions, self.a, self.folder, self.imported)
        self.assertIn("members", data.names())
        out = query.run({"name": "t", "table": "members", "by": ["relationship"], "measures": ["count", "sum net"]},
                        data)
        self.assertEqual(sum(row[1] for row in out["rows"]), r["totals"]["members"])


if __name__ == "__main__":
    unittest.main()
