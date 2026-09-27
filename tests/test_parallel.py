"""Independent passes run across processes give exactly the answers they
give one after another, and the switch and the guard work."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import engine, measures, parallel  # noqa: E402
from keel.__main__ import load  # noqa: E402


class Parallel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.positions, cls.a, _, _ = load(os.path.join(ROOT, "examples", "sample-cu"))

    def tasks(self):
        return ([("keel.engine.going_concern", (self.positions, self.a, s), {"months": 24})
                 for s in self.a.scenarios[:4]]
                + [("keel.measures.nev", (self.positions, self.a, s), {}) for s in self.a.scenarios[:3]])

    def test_workers_give_the_same_answers_as_one_process(self):
        together = parallel.run(self.tasks())
        alone = [engine.going_concern(self.positions, self.a, s, months=24) for s in self.a.scenarios[:4]] + \
                [measures.nev(self.positions, self.a, s) for s in self.a.scenarios[:3]]
        for x, y in zip(together[:4], alone[:4]):
            self.assertEqual([m.nii for m in x], [m.nii for m in y])
            self.assertEqual([m.equity for m in x], [m.equity for m in y])
        for x, y in zip(together[4:], alone[4:]):
            self.assertEqual((x.pv_assets, x.pv_liabilities), (y.pv_assets, y.pv_liabilities))

    def test_the_switch(self):
        saved = os.environ.get("KEEL_WORKERS")
        try:
            os.environ["KEEL_WORKERS"] = "1"
            self.assertEqual(parallel.workers(), 1)
            self.assertEqual(len(parallel.run(self.tasks())), 7)
            os.environ["KEEL_WORKERS"] = "3"
            self.assertEqual(parallel.workers(), 3)
        finally:
            if saved is None:
                os.environ.pop("KEEL_WORKERS", None)
            else:
                os.environ["KEEL_WORKERS"] = saved

    def test_only_keels_own_functions_run(self):
        with self.assertRaises(ValueError):
            parallel.run([("os.system", ("echo no",), {})])


if __name__ == "__main__":
    unittest.main()
