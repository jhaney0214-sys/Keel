"""Policy limits, charts, the report and its Excel twin.

The report, the workbook and the what-if page each show the limits; these
tests hold them to one answer.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from xml.etree import ElementTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import charts, export, model, report, results, settings, whatif, xlsx  # noqa: E402
from keel.__main__ import load  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")


def sample_raw():
    with open(os.path.join(SAMPLE, "assumptions.json"), encoding="utf-8") as handle:
        return json.load(handle)


class Limits(unittest.TestCase):

    def test_status_bands(self):
        self.assertEqual(results.evaluate("k", "max", 10.0, 8.0, 10.0), "within")
        self.assertEqual(results.evaluate("k", "max", 10.0, 9.5, 10.0), "near")
        self.assertEqual(results.evaluate("k", "max", 10.0, 10.5, 10.0), "breach")
        self.assertEqual(results.evaluate("k", "min", 6.0, 8.0, 10.0), "within")
        self.assertEqual(results.evaluate("k", "min", 6.0, 6.3, 10.0), "near")
        self.assertEqual(results.evaluate("k", "min", 6.0, 5.9, 10.0), "breach")

    def test_liquidity_that_outlasts_the_year_passes(self):
        """Survival past the measured year was once scored as exactly 12 months, and read as near."""
        self.assertIsNone(results.survival_value(None))
        self.assertEqual(results.evaluate("survival_months_min", "min", 12.0, None, 10.0), "within")

    def test_a_limit_not_set_is_labelled_a_default(self):
        positions, a, _, imported = load(SAMPLE)
        a.limits = {"loans_to_shares_max": 70.0}
        r = results.compute(positions, a, "t", imported)
        by = {x.key: x for x in r["limits"]}
        self.assertFalse(by["loans_to_shares_max"].default)
        self.assertEqual(by["loans_to_shares_max"].limit, 70.0)
        self.assertTrue(by["nii_decline_300"].default)
        self.assertEqual(len(r["limits"]), len(model.LIMITS))

    def test_an_unknown_limit_is_refused(self):
        raw = sample_raw()
        raw["limits"] = {"loan_to_share": 90}
        with self.assertRaises(model.InputError):
            model.parse_assumptions(raw)

    def test_limits_survive_the_workbook(self):
        tmp = tempfile.mkdtemp()
        try:
            raw = sample_raw()
            raw["limits"] = {"nev_decline_300": 35.0, "warning_band": 5.0}
            path = os.path.join(tmp, "a.xlsx")
            xlsx.write_workbook(path, settings.to_workbook(raw))
            self.assertEqual(settings.load(path)["limits"], {"nev_decline_300": 35.0, "warning_band": 5.0})
        finally:
            shutil.rmtree(tmp)


class Charts(unittest.TestCase):

    def parse(self, svg):
        return ElementTree.fromstring(svg)          # well-formed, or this raises

    def test_diverging_bars_mark_each_side_and_the_limit(self):
        svg = charts.diverging_bars([("+300", 2.0, "up"), ("-300", -6.0, "down <&>")], lambda v: "%g" % v,
                                    "NII", limit=15.0, both_sides=False)
        self.parse(svg)
        self.assertEqual(svg.count("class=\"c-pos\""), 1)
        self.assertEqual(svg.count("class=\"c-neg\""), 1)
        self.assertEqual(svg.count("class=\"c-limit\""), 1)
        self.assertIn("down &lt;&amp;&gt;", svg)

    def test_columns_and_line_are_well_formed(self):
        self.parse(charts.columns([("a", 3.0, "a"), ("b", -1.0, "b")], str, "gap", signed=True))
        self.parse(charts.line([(1, 5.0, "x"), (2, 4.0, "y")], str, "liquidity", reference=0.0))

    def test_axis_ticks_are_round(self):
        self.assertEqual(charts._ticks(0, 9.3, 4), [0, 2.5, 5.0, 7.5, 10.0])


class Report(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.positions, cls.a, _, imported = load(SAMPLE)
        cls.result = report.build(cls.positions, cls.a, cls.tmp, "Sample", imported)
        with open(os.path.join(cls.tmp, "report.html"), encoding="utf-8") as handle:
            cls.html = handle.read()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_the_page_carries_findings_limits_and_charts(self):
        for text in ("id='summary'", "Policy limits", "<svg class=\"chart\"", "Net interest margin",
                     "24 months vs base", "results.xlsx", "@media print"):
            self.assertIn(text, self.html)
        self.assertEqual(self.html.count("<svg class=\"chart\""), 7)

    def test_every_status_shows_a_word_not_only_a_colour(self):
        for x in self.result["limits"]:
            self.assertIn(report.STATUS[x.status][2], self.html)

    def test_the_workbook_holds_the_same_numbers(self):
        book = xlsx.read_workbook(os.path.join(self.tmp, "results.xlsx"))
        r = self.result["results"]
        nii = {row[0]: row[1] for row in book["NII"][1:]}
        self.assertAlmostEqual(nii["base"], r["nii_base"]["y1"], places=2)
        limits = {row[0]: row[5] for row in book["Limits"][1:]}
        self.assertEqual(limits, {x.label: x.status for x in r["limits"]})
        self.assertEqual(len(book["Monthly base"]) - 1, self.a.horizon_months)

    def test_the_what_if_page_measures_limits_as_the_report_does(self):
        rows = whatif.key_measures(self.positions, self.a)
        from_whatif = {x.key: x.value for _, x, kind in rows if kind == "limit"}
        from_report = {x.key: x.value for x in self.result["limits"]}
        self.assertEqual(from_whatif.keys(), from_report.keys())
        for key in from_report:
            if from_report[key] is None:
                self.assertIsNone(from_whatif[key])
            else:
                self.assertAlmostEqual(from_whatif[key], from_report[key], places=6, msg=key)

    def test_a_change_that_rounds_to_zero_has_no_sign(self):
        self.assertEqual(report.signed(-0.00001), "0.0%")
        self.assertEqual(report.signed(0.012), "+1.2%")


if __name__ == "__main__":
    unittest.main()
