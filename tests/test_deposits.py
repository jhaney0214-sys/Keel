"""The deposit study: each estimate recovers a behaviour built into data
where the answer is known, and the study never pretends to know decay from
aggregate balances."""

import math
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import deposits, model  # noqa: E402
from keel.curve import Curve, Scenario  # noqa: E402


def months(n, start=(2021, 1)):
    y, m = start
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def market_path(n):
    """Up for the first half, down after, in decimals."""
    half = n // 2
    return [0.001 + 0.05 * min(t, half) / half - 0.02 * max(0, t - half) / (n - half) for t in range(n)]


class Estimates(unittest.TestCase):

    def test_beta_and_lag_are_recovered(self):
        n = 60
        mkt = market_path(n)
        rates = [0.002 + 0.6 * mkt[max(0, t - 2)] for t in range(n)]
        rows = [(m, 1e6, r, k) for m, r, k in zip(months(n), rates, mkt)]
        b = deposits.betas(rows)
        self.assertEqual(b["lag"], 2)
        self.assertAlmostEqual(b["beta"], 0.6, places=6)
        self.assertAlmostEqual(b["up_beta"], 0.6, places=6)

    def test_asymmetric_pricing_shows_in_up_and_down_betas(self):
        n = 60
        mkt = market_path(n)
        rates = [0.002]
        for t in range(1, n):
            change = mkt[t] - mkt[t - 1]
            rates.append(rates[-1] + (0.4 if change > 0 else 0.8) * change)
        b = deposits.betas([(m, 1e6, r, k) for m, r, k in zip(months(n), rates, mkt)])
        self.assertAlmostEqual(b["up_beta"], 0.4, places=6)
        self.assertAlmostEqual(b["down_beta"], 0.8, places=6)

    def test_balance_sensitivity_to_the_spread(self):
        n = 48
        mkt = market_path(n)
        balances = [1e6]
        for t in range(1, n):
            spread_pp = (mkt[t] - 0.001) * 100
            balances.append(balances[-1] * math.exp(0.002 - 0.03 * spread_pp / 12))
        sens, core = deposits.balance_sensitivity([(m, b, 0.001, k) for m, b, k in zip(months(n), balances, mkt)])
        self.assertAlmostEqual(sens["runoff_per_100bp"], 0.03, places=6)
        self.assertAlmostEqual(sens["r2"], 1.0, places=6)
        self.assertLess(core, 1.0)

    def test_decay_from_a_cohort(self):
        keep = (1 - 0.2) ** (1 / 12.0)
        data = {}
        for t, m in enumerate(months(25)):
            data[m] = {"a": 1000 * keep ** t, "b": 3000 * keep ** t}
            if t > 0:
                data[m]["new%d" % t] = 5000.0          # new money must not hide the runoff
        d = deposits.decay(data)
        self.assertAlmostEqual(d["decay"], 0.2, places=6)
        self.assertAlmostEqual(d["average_life_years"], 5.0, places=6)


class Study(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.a = model.Assumptions(as_of="2026-06-30", curve=Curve({"1": 4.0, "360": 4.0}), indexes={},
                                   products={"mm": model.Product("mm", beta=0.30, runoff=0.20)},
                                   scenarios=[Scenario("base", 0)])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, beta):
        n = 40
        mkt = market_path(n)
        with open(os.path.join(self.tmp, "deposit_history.csv"), "w") as handle:
            handle.write("month,product,balance,rate,market_rate\n")
            for t, m in enumerate(months(n)):
                handle.write("%s,mm,%f,%f,%f\n" % (m, 1e6 * (1.001 ** t), 100 * (0.002 + beta * mkt[t]), 100 * mkt[t]))

    def test_no_history_means_no_study(self):
        self.assertIsNone(deposits.study(self.tmp, self.a))

    def test_a_beta_far_from_the_assumption_is_flagged_and_recommended(self):
        self.write(0.6)
        s = deposits.study(self.tmp, self.a)
        row = s["products"][0]
        self.assertTrue(any("beta assumed 30%" in f for f in row["flags"]))
        self.assertIsNone(row["decay"])                 # no accounts: decay is not guessed
        self.assertEqual(deposits.recommended(s)["mm"]["beta"], 60.0)
        self.assertNotIn("runoff", deposits.recommended(s)["mm"])

    def test_a_close_beta_is_not_flagged(self):
        self.write(0.33)
        self.assertEqual(deposits.study(self.tmp, self.a)["products"][0]["flags"], [])


if __name__ == "__main__":
    unittest.main()
