"""The second opinion: Keel beside another model's figures.

A model that agrees with Keel everywhere passes; each planted difference is
caught and pointed at the right place to look.
"""

import csv
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import compare, model, results  # noqa: E402
from keel.__main__ import load  # noqa: E402


class SecondOpinion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.positions, cls.a, _, imported = load(os.path.join(ROOT, "examples", "sample-cu"))
        cls.r = results.compute(cls.positions, cls.a, "sample", imported, None, assumption_tests=False)
        cls.mine = compare.keel_figures(cls.positions, cls.a, cls.r)

    def check(self, changes):
        other = dict(self.mine)
        for key, change in changes.items():
            other[key] = change(other[key])
        return compare.compare(self.positions, self.a, self.r, other)

    def test_the_same_figures_all_agree(self):
        rows, notes = self.check({})
        self.assertTrue(all(x["within"] for x in rows))
        self.assertEqual(notes, [])

    def test_a_different_book_is_named_first(self):
        rows, notes = self.check({("total_assets", ""): lambda v: v * 1.02,
                                  ("nii_year1", "base"): lambda v: v * 1.10})
        self.assertEqual([k for k, _ in notes], ["balances", "nii_base"])

    def test_shock_differences_point_at_behaviour(self):
        rows, notes = self.check({("nii_change_year1", "+300"): lambda v: v + 4,
                                  ("nev_ratio", "+300"): lambda v: v - 2})
        self.assertEqual([k for k, _ in notes], ["nii_shock", "nev_shock"])
        off = [x for x in rows if x["within"] is False]
        self.assertEqual(len(off), 2)
        self.assertAlmostEqual(next(x for x in off if x["measure"] == "nev_ratio")["difference"], 2.0)

    def test_the_template_and_the_reader(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "other.csv")
            n = compare.write_template(path, self.positions, self.a, self.r)
            self.assertEqual(n, len(self.mine))
            with self.assertRaisesRegex(model.InputError, "no figures filled in"):
                compare.read_other(path)
            with open(path, "w", newline="") as handle:
                w = csv.writer(handle)
                w.writerow(["measure", "scenario", "value"])
                w.writerow(["nii_year1", "base", "21,500,000"])
                w.writerow(["nev_ratio", "+300", "4.9%"])
            self.assertEqual(compare.read_other(path), {("nii_year1", "base"): 21.5e6, ("nev_ratio", "+300"): 4.9})
            with open(path, "a", newline="") as handle:
                csv.writer(handle).writerow(["eve_magic", "base", "1"])
            with self.assertRaisesRegex(model.InputError, "unknown measure"):
                compare.read_other(path)
        finally:
            shutil.rmtree(tmp)

    def test_the_page(self):
        rows, notes = self.check({("nev", "base"): lambda v: v * 1.2})
        page = compare.page("sample", rows, notes, "", "Vendor model")
        self.assertIn("Vendor model", page)
        self.assertIn("Base NEV differs", page)


if __name__ == "__main__":
    unittest.main()
