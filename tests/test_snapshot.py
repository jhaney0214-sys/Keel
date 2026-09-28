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


def build(defaults=False, contact="Test Person, test@example.com"):
    positions, assumptions, _, imported = load(SAMPLE)
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
        self.assertIn("indicative", page)
        self.assertIn("not rated here", page)
        self.assertIn("What your own files would change", page)
        self.assertIn("Keel's defaults, not its own", page)

    def test_placeholder_contact_by_default(self):
        _, page = build(contact="[Your name], [contact]")
        self.assertIn("[Your name], [contact]", page)


if __name__ == "__main__":
    unittest.main()
