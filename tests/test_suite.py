"""FTP, product capital, RAROC pricing, new products, the budget, ad hoc
queries and bank mode.

Where an answer can be worked by hand it is: a bullet loan on a flat curve
has a known FTP, spread, capital credit and RAROC. Everything else is held
to an identity the numbers must satisfy (spreads plus treasury are NII;
volume plus rate is the variance; a tax of t takes t of pre-tax income).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import (budget, engine, measures, model, newproduct, pricing, profitability, query,  # noqa: E402
                  report, results, terms)
from keel.__main__ import load  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")


def flat(products, rate=5.0, **kw):
    base = dict(as_of="2026-06-30", curve=Curve({"1": rate, "360": rate}), indexes={}, products=products,
                scenarios=[Scenario("base", 0), Scenario("+300", 300), Scenario("-300", -300)])
    base.update(kw)
    return model.Assumptions(**base)


def pos(**kw):
    base = dict(id="x", name="x", product="loan", side="asset", balance=100000.0, rate=0.07,
                rate_type="fixed", term_months=24, amortization="bullet")
    base.update(kw)
    return model.Position(**base)


class FTP(unittest.TestCase):

    def test_a_bullet_is_funded_at_the_curve_at_its_maturity(self):
        a = flat({"loan": model.Product("loan")})
        a.curve = Curve({"1": 3.0, "24": 4.0, "360": 5.0})
        rates = profitability.ftp_rates([pos()], a)
        self.assertAlmostEqual(rates["x"], 0.04, places=10)

    def test_an_amortizing_loan_is_funded_shorter_than_its_term(self):
        a = flat({"loan": model.Product("loan")})
        a.curve = Curve({"1": 3.0, "360": 6.0})
        bullet = profitability.ftp_rates([pos(term_months=120)], a)["x"]
        level = profitability.ftp_rates([pos(term_months=120, amortization="level")], a)["x"]
        self.assertLess(level, bullet)

    def test_spreads_and_treasury_add_up_to_nii_on_a_real_book(self):
        positions, a, _, _ = load(SAMPLE)
        lines, _, totals = profitability.product_lines(positions, a)
        self.assertTrue(profitability.check_ftp(lines, totals).passed)

    def test_the_risk_weight_rule_and_the_allowance(self):
        a = flat({"loan": model.Product("loan"), "bond": model.Product("bond", liquid=True),
                  "cash": model.Product("cash"), "allowance": model.Product("allowance")})
        book = [pos(), pos(id="b", product="bond"), pos(id="c", product="cash", rate_type="none", amortization="none"),
                pos(id="al", product="allowance", balance=-5000.0, rate_type="none", amortization="none")]
        self.assertAlmostEqual(profitability.rwa(book, a), 100000 * 1.0 + 100000 * 0.2)


class Pricing(unittest.TestCase):

    def setUp(self):
        self.a = flat({"loan": model.Product("loan", risk_weight=1.0)}, target_capital=0.10, hurdle_rate=0.15)

    def test_a_bullet_loan_worked_by_hand(self):
        """7% on a flat 5% curve: spread 2%, capital 10% credited at 5% = 0.5%, net 2.5%, RAROC 25%."""
        e = pricing.economics(pricing.Deal("loan", 100000, 24, rate=0.07, amortization="bullet"), self.a)
        self.assertAlmostEqual(e["ftp"], 0.05, places=9)
        self.assertAlmostEqual(e["spread"], 0.02, places=9)
        self.assertAlmostEqual(e["capital_credit"], 0.005, places=9)
        self.assertAlmostEqual(e["raroc"], 0.25, places=9)

    def test_the_solved_rates_hit_their_targets(self):
        deal = pricing.Deal("loan", 30000, 60, amortization="level", servicing_cost=0.006, charge_off=0.01,
                            origination_cost=0.01)
        q = pricing.quote(deal, self.a)
        self.assertAlmostEqual(pricing.economics(deal, self.a, q["hurdle_rate"])["raroc"], 0.15, places=6)
        self.assertAlmostEqual(pricing.economics(deal, self.a, q["breakeven_rate"])["pre_tax"], 0.0, places=8)
        self.assertGreater(q["hurdle_rate"], q["breakeven_rate"])

    def test_tax_takes_its_share(self):
        taxed = flat({"loan": model.Product("loan", risk_weight=1.0)}, target_capital=0.10, tax_rate=0.21)
        e = pricing.economics(pricing.Deal("loan", 100000, 24, rate=0.07, amortization="bullet"), taxed)
        self.assertAlmostEqual(e["net"], e["pre_tax"] * 0.79, places=12)

    def test_a_deposit_covers_its_costs_below_ftp(self):
        a = flat({"cd": model.Product("cd", servicing_cost=0.002)})
        q = pricing.quote(pricing.Deal("cd", 25000, 12, side="liability", rate=0.045, amortization="bullet"), a)
        self.assertIsNone(q["at"]["raroc"])
        self.assertAlmostEqual(q["breakeven_rate"], 0.05 - 0.002, places=6)


class NewProduct(unittest.TestCase):

    def setUp(self):
        self.positions, self.a, self.raw, _ = load(SAMPLE)

    def test_a_launched_product_is_in_the_book_and_the_settings(self):
        proposal = {"product": "green_auto", "like": "new_auto", "rate": 5.5, "term_months": 60,
                    "launch_balance": 1000000, "growth": 10}
        book, a, notes, deal = newproduct.build(self.positions, self.raw, proposal)
        self.assertIn("green_auto", a.products)
        self.assertEqual(sum(p.balance for p in book if p.product == "green_auto"), 1000000)
        self.assertAlmostEqual(a.products["green_auto"].growth, 0.10)

    def test_an_existing_key_or_unknown_likeness_is_refused(self):
        with self.assertRaises(model.InputError):
            newproduct.build(self.positions, self.raw, {"product": "new_auto", "rate": 5, "launch_balance": 1})
        with self.assertRaises(model.InputError):
            newproduct.build(self.positions, self.raw, {"product": "x", "like": "nothing", "rate": 5,
                                                        "launch_balance": 1})

    def test_a_deposit_that_pays_over_ftp_loses_money_in_the_plan(self):
        proposal = {"product": "cd_special", "like": "certificates", "side": "liability", "rate": 6.5,
                    "term_months": 12, "amortization": "bullet", "launch_balance": 5000000}
        r = newproduct.analyse(self.positions, self.a, self.raw, proposal)
        self.assertLess(r["path"][0]["spread"], 0)
        self.assertIsNone(r["path"][0]["raroc"])


class Budget(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.positions, cls.a, _, _ = load(SAMPLE)
        cls.months = engine.going_concern(cls.positions, cls.a, cls.a.scenarios[0])
        cls.b = budget.build(cls.positions, cls.a, cls.months)

    def test_the_budget_is_the_plan(self):
        self.assertTrue(budget.check_budget(self.b, self.months).passed)
        self.assertEqual(self.b["labels"][0], "2026-07")
        self.assertEqual(len(self.b["labels"]), 12)

    def test_actuals_equal_to_budget_have_no_variance(self):
        actuals = {(self.b["labels"][t], p["product"]): (p["average"][t], p["interest"][t])
                   for p in self.b["products"] for t in range(2)}
        v = budget.variance(self.b, actuals)
        self.assertEqual(v["months"], 2)
        for row in v["products"]:
            self.assertAlmostEqual(row["nii_variance"], 0.0, places=6)

    def test_volume_and_rate_make_up_the_variance(self):
        p = next(x for x in self.b["products"] if x["side"] == "liability")
        m = self.b["labels"][0]
        v = budget.variance(self.b, {(m, p["product"]): (p["average"][0] * 1.1, p["interest"][0] * 1.2)})
        row = v["products"][0]
        self.assertAlmostEqual(row["volume"] + row["rate"], row["nii_variance"], places=6)
        self.assertLess(row["nii_variance"], 0)          # paying more on a liability costs NII
        self.assertAlmostEqual(row["volume"], -p["interest"][0] * 0.1, places=4)

    def test_an_actual_outside_the_year_is_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "actuals.csv"), "w") as handle:
                handle.write("month,line,average_balance,amount\n2031-01,fee_income,,5\n")
            with self.assertRaisesRegex(model.InputError, "not in the budget year"):
                budget.read_actuals(tmp, self.b["labels"], [])
        finally:
            shutil.rmtree(tmp)


class Query(unittest.TestCase):

    rows = [{"product": "auto", "side": "asset", "balance": 100.0, "rate": 5.0},
            {"product": "auto", "side": "asset", "balance": 300.0, "rate": 7.0},
            {"product": "cd", "side": "liability", "balance": 200.0, "rate": 4.0}]

    def test_group_sum_and_weighted_average(self):
        q = query.run({"by": ["product"], "measures": ["count", "sum balance", "wavg rate balance"],
                       "where": ["side = asset"], "table": "t"}, {"t": self.rows})
        self.assertEqual(q["rows"], [["auto", 2, 400.0, 6.5]])
        self.assertEqual(q["total"], ["Total", 2, 400.0, 6.5])

    def test_filters(self):
        run = lambda where: query.run({"table": "t", "measures": ["count"], "where": where}, {"t": self.rows})["matched"]  # noqa: E731
        self.assertEqual(run(["balance > 100"]), 2)
        self.assertEqual(run(["balance >= 100", "rate < 6"]), 2)
        self.assertEqual(run(["product in auto,x"]), 2)
        self.assertEqual(run(["product contains C"]), 1)
        self.assertEqual(run(["side != asset"]), 1)

    def test_an_unknown_field_names_the_ones_there_are(self):
        with self.assertRaisesRegex(model.InputError, "no field 'bal'.*balance"):
            query.run({"table": "t", "measures": ["sum bal"]}, {"t": self.rows})

    def test_the_book_table_carries_ftp_and_capital(self):
        positions, a, _, _ = load(SAMPLE)
        q = query.run({"table": "positions", "measures": ["sum balance", "sum rwa"], "where": ["side = asset"]},
                      query.Tables(positions, a))
        self.assertAlmostEqual(q["rows"][0][1], profitability.rwa(positions, a), places=2)


class Bank(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SAMPLE, "assumptions.json"), encoding="utf-8") as handle:
            raw = json.load(handle)
        raw["institution"] = "bank"
        raw["notes"] = {}          # the sample's own notes are written about a credit union
        cls.a = model.parse_assumptions(raw)
        cls.positions = model.read_positions(os.path.join(SAMPLE, "positions.csv"))

    def test_a_bank_pays_tax_by_default_and_still_balances(self):
        self.assertAlmostEqual(self.a.tax_rate, 0.21)
        run = engine.going_concern(self.positions, self.a, self.a.scenarios[0])
        m = run[0]
        self.assertAlmostEqual(m.income_tax, 0.21 * (m.net_income + m.income_tax), places=6)
        checks = measures.reconcile(self.positions, self.a, {"base": run})
        self.assertTrue(checks[0].passed, checks[0].detail)

    def test_a_banks_report_speaks_bank_and_skips_ncuas_tests(self):
        tmp = tempfile.mkdtemp()
        try:
            report.build(self.positions, self.a, tmp, "Test Bank")
            with open(os.path.join(tmp, "report.html"), encoding="utf-8") as handle:
                page = handle.read()
            self.assertNotIn("NCUA", page)
            self.assertNotIn("741.12", page)
            self.assertIn("EVE", page)
            self.assertIn("Loans to deposits", page)
            self.assertIn("Income tax", page)
        finally:
            shutil.rmtree(tmp)

    def test_translation_leaves_code_alone(self):
        self.assertEqual(terms.translate("<code>regular_shares</code> shares", self.a),
                         "<code>regular_shares</code> deposits")


class Serve(unittest.TestCase):

    def test_the_pages_answer(self):
        import http.server
        import threading
        import urllib.parse
        import urllib.request
        from keel import serve
        tmp = tempfile.mkdtemp()
        folder = os.path.join(tmp, "cu")
        shutil.copytree(SAMPLE, folder, ignore=shutil.ignore_patterns("report"))
        server = serve.Server(folder)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve.handler_for(server))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d" % httpd.server_address[1]
            get = lambda path: urllib.request.urlopen(base + path).read().decode("utf-8")  # noqa: E731
            self.assertIn("RAROC", get("/pricing?product=new_auto&amount=30000&term=60&rate=6.5"))
            self.assertIn("Hurdle rate", get("/pricing?product=new_auto&amount=30000&term=60"))
            fields = {"run": "1", "name": "By product", "table": "positions", "by": "product",
                      "measures": "count\nsum balance"}
            self.assertIn("Download CSV", get("/explore?" + urllib.parse.urlencode(fields)))
            self.assertTrue(get("/explore.csv?" + urllib.parse.urlencode(fields)).startswith("product,count"))
            body = urllib.parse.urlencode(fields).encode()
            urllib.request.urlopen(base + "/explore/save", body).read()
            self.assertTrue(os.path.isfile(os.path.join(folder, "queries", "by-product.json")))
            body = urllib.parse.urlencode({"name": "Green", "product": "green_auto", "like": "new_auto",
                                           "side": "asset", "rate": "5.5", "term_months": "60",
                                           "amortization": "level", "launch_balance": "1000000"}).encode()
            self.assertIn("The institution, with and without it",
                          urllib.request.urlopen(base + "/newproduct", body).read().decode("utf-8"))
            self.assertIn("Sell (deepest loss first", get("/trade"))
            sid = server.securities()[0]["id"]
            product = server.securities()[0]["product"]
            body = urllib.parse.urlencode({"name": "Swap", "sell_" + sid: "1", "product": product, "yield": "5",
                                           "term_months": "36"}).encode()
            self.assertIn("Yield pickup", urllib.request.urlopen(base + "/trade", body).read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
