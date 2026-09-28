"""The two-page snapshot: same numbers as the report, and no verdict the data cannot carry."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import report, snapshot  # noqa: E402
from keel.__main__ import load  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")


def build(defaults=False, contact="Test Person, test@example.com", limits=None):
    positions, assumptions, _, imported = load(SAMPLE)
    if limits is not None:
        assumptions.limits = dict(limits)
    if defaults:
        assumptions.notes = dict(assumptions.notes, defaults="Behaviour is Keel's defaults.",
                                 source="NCUA 5300 call report, test.", calibration="Calibrated, test.")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "snapshot.html")
        r = snapshot.build(positions, assumptions, path, "Test CU", None, contact, imported)
        with open(path, encoding="utf-8") as handle:
            return r, handle.read()


class Snapshot(unittest.TestCase):

    def test_own_files_shows_ratings_and_contact(self):
        r, page = build()
        self.assertIn("Test CU: rate-risk snapshot", page)
        self.assertIn(report.k(r["nii_base"]["y1"]), page)
        self.assertIn(r["test"]["ratio_rating"] + " risk", page)
        self.assertIn("Test Person, test@example.com", page)
        self.assertNotIn("first look, not an ALM report", page)
        self.assertIn("A second opinion", page)

    def test_default_behaviour_is_never_graded(self):
        """A call-report build rests on default behaviour, so the page gives no NCUA rating and says why."""
        r, page = build(defaults=True)
        self.assertIn("first look, not an ALM report", page)
        self.assertNotIn(r["test"]["ratio_rating"] + " risk", page)
        self.assertNotIn("rates the ratio", page)
        self.assertNotIn("own assumptions", page)
        # The tiles show Keel's own NEV on its defaults; the supervisory figure is only in the text, caveated.
        # Its change is a percentage of a base that can be near zero, and "-269%" once led a page.
        self.assertIn("NEV ratio after +300bp, Keel defaults", page)
        self.assertNotIn("NCUA supervisory NEV change", page)
        self.assertIn("not rated here", page)
        self.assertIn("What your own files would change", page)
        self.assertIn("Keel's defaults, not its own", page)

    def test_the_author_is_the_default_contact(self):
        positions, assumptions, _, imported = load(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.html")
            snapshot.build(positions, assumptions, path, "Test CU", None, imported=imported)
            with open(path, encoding="utf-8") as handle:
                self.assertIn("Jordan Haney, CFA, jhaney0214@gmail.com", handle.read())

    def test_only_the_boards_own_limits_are_shown(self):
        """A typical limit shown as the institution's would be a verdict nobody set."""
        _, page = build(limits={})
        self.assertIn("No policy limits are set", page)
        self.assertNotIn("Loans to shares</td>", page)
        r, page = build(limits={"loans_to_shares_max": 80.0, "nii_decline_300": 15.0})
        self.assertIn("Loans to shares</td>", page)
        self.assertIn("at most 80%", page)
        self.assertNotIn("Borrowings to assets</td>", page)
        self.assertIn("9 more are not set", page)
        status = next(x for x in r["limits"] if x.key == "loans_to_shares_max").status
        self.assertIn(snapshot.report.STATUS[status][2], page)

    def test_limits_on_default_behaviour_are_marked_indicative(self):
        _, page = build(defaults=True, limits={"nev_ratio_min": 6.0})
        self.assertIn("read those statuses as indicative", page)
        self.assertNotIn("own assumptions", page)

    def test_limit_given_on_the_command_line(self):
        from keel.__main__ import main
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "s.html")
            self.assertEqual(main(["snapshot", SAMPLE, "--out", out, "--limit", "loans_to_shares_max=70"]), 0)
            with open(out, encoding="utf-8") as handle:
                self.assertIn("at most 70%", handle.read())
            self.assertEqual(main(["snapshot", SAMPLE, "--out", out, "--limit", "no_such_limit=5"]), 2)
            self.assertEqual(main(["snapshot", SAMPLE, "--out", out, "--limit", "loans_to_shares_max"]), 2)


if __name__ == "__main__":
    unittest.main()
