"""Rate paths, budget drivers, assumption tests, run history and the call
report loader.

The rate path has worked answers (a flat curve's forwards are the curve;
a forecast interpolates exactly as specified); drivers are held to doing
exactly what they say (a volume originates that amount, a balance lands on
that balance); the call report loader is held to tying every credit
union's totals and reported income, on a small zip built in NCUA's own
format so the test needs no download.
"""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import (callreport, engine, history, measures, model, results, sensitivity, settings,  # noqa: E402
                  validate, xlsx)
from keel.curve import Curve, RatePath, Scenario  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")


def sample_raw():
    with open(os.path.join(SAMPLE, "assumptions.json"), encoding="utf-8") as handle:
        return json.load(handle)


def sample_positions():
    return model.read_positions(os.path.join(SAMPLE, "positions.csv"))


def nii(positions, a, name="base", months=12):
    s = next(x for x in a.scenarios if x.name == name)
    return sum(m.nii for m in engine.going_concern(positions, a, s, months=months))


class RatePaths(unittest.TestCase):

    def test_a_flat_curves_forwards_are_the_curve(self):
        path = RatePath("forward", Curve({"1": 4.0, "360": 4.0}))
        for month, tenor in ((1, 1), (12, 12), (36, 120)):
            self.assertAlmostEqual(path.move_bp(month, tenor), 0.0, places=9)

    def test_a_forward_rate_worked_by_hand(self):
        curve = Curve({"12": 3.0, "24": 4.0})
        f = ((1.04 ** 2) / 1.03) - 1.0          # one-year rate, one year forward
        self.assertAlmostEqual(RatePath("forward", curve).rate(12, 12), 100 * f, places=9)

    def test_a_forecast_interpolates_across_months_and_tenors_then_holds(self):
        curve = Curve({"1": 4.0, "120": 4.0})
        path = RatePath("forecast", curve, [(6, 1, 3.0), (6, 120, 4.0), (12, 1, 2.0), (12, 120, 4.0)])
        self.assertAlmostEqual(path.rate(3, 1), 3.5)           # halfway from today to month 6
        self.assertAlmostEqual(path.rate(6, 60.5), 3.5)        # halfway along the tenors at month 6
        self.assertAlmostEqual(path.rate(9, 1), 2.5)
        self.assertAlmostEqual(path.rate(40, 1), 2.0)          # held after the last forecast month

    def test_the_path_moves_the_plan_but_not_nev_and_flat_is_unchanged(self):
        positions = sample_positions()
        raw = sample_raw()
        flat = model.parse_assumptions(raw)
        raw["base_case"] = "forecast"
        raw["rate_forecast"] = [{"month": 6, "tenor_months": 1, "rate": 2.0}]
        moved = model.parse_assumptions(raw)
        self.assertNotAlmostEqual(nii(positions, flat), nii(positions, moved), places=0)
        self.assertAlmostEqual(nii(positions, flat), nii(positions, moved, "rates unchanged"), places=4)
        self.assertEqual(measures.nev(positions, flat, flat.scenarios[0]).nev,
                         measures.nev(positions, moved, moved.scenarios[0]).nev)


class Drivers(unittest.TestCase):

    def setUp(self):
        self.products = {"cash": model.Product("cash"),
                         "cd": model.Product("cd", new_term=12, new_amortization="bullet"),
                         "loan": model.Product("loan", new_term=24, new_amortization="bullet"),
                         "mm": model.Product("mm", beta=0.5)}
        self.book = [model.Position("cash", "cash", "cash", "asset", 1e6, 0.0, "none"),
                     model.Position("l", "l", "loan", "asset", 1e6, 0.06, "fixed", term_months=60,
                                    amortization="bullet"),
                     model.Position("c", "c", "cd", "liability", 1e6, 0.04, "fixed", term_months=60,
                                    amortization="bullet"),
                     model.Position("m", "m", "mm", "liability", 5e5, 0.02, "administered",
                                    amortization="nonmaturity")]

    def a(self, drivers, **kw):
        a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={},
                              products=self.products, scenarios=[Scenario("base", 0), Scenario("+100", 100)], **kw)
        a.drivers = model._drivers(drivers, "2026-06-30", self.products)
        return a

    def test_a_volume_originates_exactly_that_amount(self):
        a = self.a([{"product": "loan", "month": 2, "volume": 250000}])
        run = engine.going_concern(self.book, a, a.scenarios[0], months=3)
        self.assertAlmostEqual(run[1].balances["loan"] - run[0].balances["loan"], 250000, places=4)
        self.assertAlmostEqual(run[2].balances["loan"], run[1].balances["loan"], places=4)   # growth 0 after

    def test_a_balance_lands_where_it_says_and_a_month_can_be_a_date(self):
        a = self.a([{"product": "loan", "month": "2026-09", "balance": 1300000}])
        run = engine.going_concern(self.book, a, a.scenarios[0], months=3)
        self.assertAlmostEqual(run[2].balances["loan"], 1300000, places=4)

    def test_a_budgeted_rate_prices_new_business_and_moves_with_a_shock(self):
        a = self.a([{"product": "loan", "month": 1, "volume": 100000, "rate": 7.0}])
        base = engine.going_concern(self.book, a, a.scenarios[0], months=1)
        up = engine.going_concern(self.book, a, a.scenarios[1], months=1)
        # month 1's new loan earns nothing until month 2, so price it directly
        stepper = engine.Stepper(a, a.scenarios[1], drivers=a.drivers)
        template = self.book[1]
        new = engine._originate(template, a.products["loan"], 100000, 1, stepper)
        self.assertAlmostEqual(new.rate, 0.08, places=12)          # 7% budgeted + 100bp shock
        self.assertEqual(len(base), len(up))

    def test_an_offering_rate_on_a_share_moves_by_its_beta(self):
        a = self.a([{"product": "mm", "month": 1, "rate": 3.0}])
        for scenario, expected in ((a.scenarios[0], 0.03), (a.scenarios[1], 0.035)):
            stepper = engine.Stepper(a, scenario, drivers=a.drivers)
            p = self.book[3].copy()
            stepper.reprice(p, 1)
            self.assertAlmostEqual(p.rate, expected, places=12)

    def test_volume_and_balance_together_are_refused(self):
        with self.assertRaises(model.InputError):
            self.a([{"product": "loan", "month": 1, "volume": 1, "balance": 2}])

    def test_itemized_lines_start_grow_and_replace_the_totals(self):
        raw = sample_raw()
        raw["noninterest"] = [{"line": "Salaries", "kind": "expense", "annual": 1200000, "growth": 10},
                              {"line": "New branch", "kind": "expense", "annual": 120000, "start_month": 4},
                              {"line": "Interchange", "kind": "income", "annual": 600000}]
        a = model.parse_assumptions(raw)
        self.assertEqual((a.operating_expense, a.fee_income), (1320000, 600000))
        run = engine.going_concern(sample_positions(), a, a.scenarios[0], months=13)
        self.assertAlmostEqual(run[0].operating_expense, 100000)
        self.assertAlmostEqual(run[3].operating_expense, 110000)
        self.assertAlmostEqual(run[12].operating_expense, 110000 + 10000)
        checks = measures.reconcile(sample_positions(), a, {"base": run})
        self.assertTrue(checks[0].passed)

    def test_the_budget_sheets_survive_the_workbook(self):
        raw = sample_raw()
        raw["base_case"] = "forecast"
        raw["rate_forecast"] = [{"month": 6, "tenor_months": 1, "rate": 3.5}]
        raw["drivers"] = [{"product": "new_auto", "month": "2026-08", "volume": 2000000}]
        raw["noninterest"] = [{"line": "Salaries", "kind": "expense", "annual": 1000000, "growth": 3}]
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "a.xlsx")
            xlsx.write_workbook(path, settings.to_workbook(raw))
            back = model.parse_assumptions(settings.load(path))
            self.assertEqual(back.base_case, "forecast")
            self.assertEqual(back.drivers, {"new_auto": {2: {"volume": 2000000.0}}})
            self.assertEqual(back.operating_expense, 1000000)
        finally:
            shutil.rmtree(tmp)


class AssumptionTests(unittest.TestCase):

    def test_the_baseline_is_the_reports_and_variants_move_one_family(self):
        positions, raw = sample_positions(), sample_raw()
        a = model.parse_assumptions(raw)
        t = sensitivity.run(positions, a)
        r = results.compute(positions, a, "t")
        limit = {x.key: x.value for x in r["limits"]}
        self.assertAlmostEqual(t["baseline"]["nev_decline_300"], limit["nev_decline_300"], places=6)
        self.assertAlmostEqual(t["baseline"]["nii_decline_300"], limit["nii_decline_300"], places=6)
        families = {x["family"] for x in t["rows"]}
        self.assertIn("Deposit betas", families)
        decay = next(x for x in t["rows"] if x["family"] == "Deposit decay")
        self.assertAlmostEqual(decay["values"]["nii_y1"], t["baseline"]["nii_y1"], places=2)
        self.assertNotAlmostEqual(decay["values"]["nev_decline_300"], t["baseline"]["nev_decline_300"], places=3)


class History(unittest.TestCase):

    def test_changes_trend_and_a_perfect_forecast_back_tests_to_zero(self):
        positions, raw = sample_positions(), sample_raw()
        a = model.parse_assumptions(raw)
        june = history.snapshot(results.compute(positions, a, "t"))
        september = copy.deepcopy(june)
        september["as_of"] = "2026-09-30"
        k = 3
        for name, f in june["forecast"]["products"].items():
            september["book"]["balance"][name] = f["balance"][k - 1]
        september["assumptions"]["products"]["money_market"]["beta"] = 70.0
        tmp = tempfile.mkdtemp()
        try:
            history.save(tmp, june)
            review = history.review(tmp, september)
            self.assertEqual([x["as_of"] for x in review["trend"]], ["2026-06-30", "2026-09-30"])
            self.assertIn(("money_market: beta", 55.0, 70.0), review["changes"])
            b = review["backtest"]
            self.assertEqual(b["months"], 3)
            for row in b["products"]:
                self.assertAlmostEqual(row["error"], 0.0, places=6)
        finally:
            shutil.rmtree(tmp)

    def test_a_forecast_that_does_not_reach_today_is_not_back_tested(self):
        snap = {"as_of": "2020-06-30", "forecast": {"months": ["2020-07"]}}
        self.assertIsNone(history.backtest(snap, {"as_of": "2026-06-30"}))


# --------------------------------------------------------------- the call report

def ncua_zip(path, cycle="6/30/2026", ytd=1.0, size=1.0, heloc=0.0):
    """Three credit unions in NCUA's layout: FOICU, two FS220 tables (one
    with a mixed-case header, as FS220N really has), June cycle. `ytd`
    scales the year-to-date income and expense, `size` the balances, so a
    March cycle can be made beside it. `heloc` moves that much of the
    mortgages into variable-rate HELOCs at 8%."""
    foicu = ['"CU_NUMBER","CYCLE_DATE","CU_NAME","CITY","STATE","Peer_Group"']
    fs220 = ['"CU_NUMBER","CYCLE_DATE","ACCT_010","ACCT_AS0009","ACCT_AS0013","ACCT_025B","ACCT_385","ACCT_523",'
             '"ACCT_RL0002","ACCT_563A","ACCT_018","ACCT_657","ACCT_908C","ACCT_908A","ACCT_110","ACCT_120",'
             '"ACCT_380","ACCT_115","ACCT_350","ACCT_117","ACCT_671","ACCT_661A","ACCT_LI0069","ACCT_997",'
             '"ACCT_NV0153","ACCT_860C","ACCT_860A","ACCT_340"' + (',"ACCT_RL0028","ACCT_562A"' if heloc else '')]
    fs220n = ['"CU_Number","CYCLE_DATE","ACCT_AS0048","ACCT_AS0036"']
    cus = (("1", "ALPHA", 100e6, 1.0), ("2", "BETA", 120e6, 1.2), ("3", "GAMMA", 90e6, 0.9))
    for cu, name, assets, f in cus:
        foicu.append('%s,%s 0:00:00,"%s","TOWN","TX",5' % (cu, cycle, name))
        assets *= size
        f_income, f = f * ytd, f * size
        loans, cash, sec = 60e6 * f, 10e6 * f, 25e6 * f
        allowance, other = 0.6e6 * f, assets - (cash + sec + loans - 0.6e6 * f)
        shares, borrowed = 85e6 * f, 3e6 * f
        fi = f_income
        fs220.append(",".join(str(x) for x in (
            cu, cycle + " 0:00:00", assets, cash, sec, loans, 20e6 * f, 525, (40e6 - heloc) * f, 600, shares, 50e6 * f,
            35e6 * f, 30e6 * f, 1.6e6 * fi, 0.5e6 * fi, 0.9e6 * fi, 2.1e6 * fi, 0.95e6 * fi, 0.6e6 * fi,
            1.4e6 * fi, 0.3e6 * fi, shares + borrowed, 10e6 * f, 25e6 * f, borrowed, borrowed, 0.07e6 * fi)
            + ((heloc * f, 800) if heloc else ())))
        fs220n.append("%s,%s 0:00:00,%s,%s" % (cu, cycle, allowance, other))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("FOICU.txt", "\n".join(foicu))
        z.writestr("FS220.txt", "\n".join(fs220))
        z.writestr("FS220N.txt", "\n".join(fs220n))


class CallReport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "call-report-data-2026-06.zip")
        ncua_zip(cls.path)
        cls.report = callreport.CallReport(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_the_cycle_and_its_year_to_date_months(self):
        self.assertEqual((self.report.as_of, self.report.months, self.report.days), ("2026-06-30", 6, 181))
        self.assertAlmostEqual(self.report.annual("1", "net_income"), 0.3e6 * 365 / 181)   # by days, not months

    def test_every_total_and_reported_income_ties(self):
        rows, raw, ties = callreport.build(self.report, "2")
        for key in ("assets", "liabilities"):
            self.assertAlmostEqual(ties[key][0], ties[key][1], delta=0.5)
        for key in ("loan_interest", "dividends", "investment_income"):
            self.assertAlmostEqual(ties[key][1] / ties[key][0], 1.0, places=3)

    def test_the_folder_runs(self):
        rows, raw, ties = callreport.build(self.report, "1")
        folder = os.path.join(self.tmp, "cu-1")
        callreport.write(folder, rows, raw, callreport.peers(self.report, "1"))
        a = model.parse_assumptions(raw)
        positions = model.read_positions(os.path.join(folder, "positions.csv"))
        model.check(positions, a)
        run = engine.going_concern(positions, a, a.scenarios[0])
        self.assertTrue(measures.reconcile(positions, a, {"base": run})[0].passed)

    def test_search_and_peers(self):
        self.assertEqual([x[0] for x in self.report.search("et")], ["2"])       # "BETA"
        p = callreport.peers(self.report, "2")
        self.assertEqual(p["count"], 3)
        worth = next(x for x in p["ratios"] if x["key"] == "net_worth_ratio")
        self.assertAlmostEqual(worth["value"], 10.0)
        self.assertAlmostEqual(worth["percentile"], 50.0)          # three identical ratios: the middle

    def test_the_net_worth_ratio_is_the_filed_one_when_filed(self):
        # NCUA allows average assets in the denominator; 2440 filed 9.39% while
        # net worth over quarter-end assets gave 8.87% (2026-10-01).
        self.report.data["2"]["ACCT_998"] = 1023.0
        try:
            p = callreport.peers(self.report, "2")
        finally:
            del self.report.data["2"]["ACCT_998"]
        worth = next(x for x in p["ratios"] if x["key"] == "net_worth_ratio")
        self.assertAlmostEqual(worth["value"], 10.23)

    def test_an_unknown_credit_union_and_a_non_ncua_zip_are_refused(self):
        with self.assertRaises(model.InputError):
            callreport.build(self.report, "999")
        bad = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("x.txt", "nothing")
        with self.assertRaises(model.InputError):
            callreport.CallReport(bad)


class CallReportPrices(unittest.TestCase):
    """NEV prices on a call-report build: what the call report values, Keel values the same."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "call-report-data-2026-06.zip")
        ncua_zip(cls.path, heloc=15e6)
        cls.report = callreport.CallReport(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def build(self):
        rows, raw, ties = callreport.build(self.report, "1")
        folder = os.path.join(self.tmp, "cu-1")
        callreport.write(folder, rows, raw)
        a = model.parse_assumptions(raw)
        positions = model.read_positions(os.path.join(folder, "positions.csv"))
        return rows, raw, ties, a, positions, measures.nev(positions, a, next(s for s in a.scenarios
                                                                               if s.name == "base"))

    def test_only_fixed_loans_are_scaled_to_the_reported_interest(self):
        """A variable loan earns today's index plus margin: its reported rate stands, the fixed book absorbs the rest."""
        rows, raw, ties, *_ = self.build()
        heloc = next(r for r in rows if r["rate_type"] == "variable")
        self.assertAlmostEqual(heloc["rate"], 8.0, places=3)
        self.assertAlmostEqual(ties["loan_interest"][1] / ties["loan_interest"][0], 1.0, places=3)
        self.assertIn("the fixed-rate ones", raw["notes"]["calibration"])

    def test_variable_loans_and_securities_are_worth_what_the_call_report_says(self):
        """Securities are already at fair value on the call report; a below-market yield must not take the loss again."""
        rows, raw, ties, a, positions, nev = self.build()
        for p in positions:
            if p.rate_type == "variable":
                self.assertAlmostEqual(nev.by_position[p.id] / p.balance, 1.0, places=4)
        held = [p for p in positions if p.product in ("investments", "mortgage_securities")]
        self.assertTrue(held)
        self.assertEqual(len({p.discount_spread for p in held}), 1)          # one spread for the book
        self.assertAlmostEqual(sum(nev.by_position[p.id] for p in held) / sum(p.balance for p in held), 1.0,
                               places=4)
        fixed = next(p for p in positions if p.product == "first_mortgage")
        self.assertIsNone(fixed.discount_spread)                             # nothing observed: the default

    def test_a_position_discount_spread_overrides_its_products(self):
        rows, raw, ties, a, positions, nev = self.build()
        p = next(p for p in positions if p.product == "first_mortgage")
        base = next(s for s in a.scenarios if s.name == "base")
        before = measures.nev([p], a, base).pv_assets
        p.discount_spread = a.products[p.product].discount_spread + 0.01
        self.assertLess(measures.nev([p], a, base).pv_assets, before)


class Validation(unittest.TestCase):
    """A March and a June cycle of the same three credit unions: June's
    year-to-date income is 2.1 times March's and its balances 2% larger."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.march = os.path.join(cls.tmp, "call-report-data-2026-03.zip")
        cls.june = os.path.join(cls.tmp, "call-report-data-2026-06.zip")
        ncua_zip(cls.march, "3/31/2026", ytd=1 / 2.1, size=1 / 1.02)
        ncua_zip(cls.june)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_the_latest_quarter_is_the_difference_at_an_annual_rate(self):
        march, june = callreport.CallReport(self.march), callreport.CallReport(self.june)
        self.assertEqual(march.days, 90)
        expected = (0.3e6 - 0.3e6 / 2.1) * 365 / 91
        self.assertAlmostEqual(june.latest("1", "net_income", march), expected, places=4)

    def test_cash_earns_only_what_the_reported_income_supports(self):
        june = callreport.CallReport(self.june)
        rows, _, ties = callreport.build(june, "1")
        earned, built = ties["investment_income"]
        self.assertAlmostEqual(built / earned, 1.0, places=3)        # the yield is rounded to 3 places
        cash = {r["product"]: r["balance"] for r in rows if r["product"].startswith("cash")}
        self.assertAlmostEqual(sum(cash.values()), 10e6, delta=1)

    def test_a_quarter_is_scored_against_keel_and_the_naive_forecast(self):
        r = validate.run(self.march, self.june)
        self.assertEqual((r["months"], len(r["rows"])), (3, 3))
        row = next(x for x in r["rows"] if x["cu"] == "1")
        self.assertAlmostEqual(row["nii_actual"], (2.1e6 - 0.95e6) * (1 - 1 / 2.1), places=2)
        self.assertAlmostEqual(row["assets_naive"], row["assets"])
        self.assertIn("Quarter NII", validate.text(r))
        s = r["summary"]["all"]["nii"]
        self.assertIn("keel_closer", s)

    def test_growth_blends_the_credit_unions_year_and_its_peers(self):
        march, june = callreport.CallReport(self.march), callreport.CallReport(self.june)
        rates = callreport.growth_rates(june, march, "1")      # the test's "year ago" is March: 2% growth
        self.assertAlmostEqual(rates["loans"], 2.0, places=2)   # own 2%, peers 2%
        rows, raw, _ = callreport.build(june, "1", year_ago=march)
        self.assertAlmostEqual(raw["products"]["new_auto"]["growth"], 2.0, places=2)
        self.assertAlmostEqual(raw["products"]["certificates"]["growth"], 2.0, places=2)
        self.assertIn("peer group", raw["notes"]["calibration"])

    def test_the_chain_needs_later_reports(self):
        with self.assertRaises(model.InputError):
            validate.run(self.june, self.march)


if __name__ == "__main__":
    unittest.main()
