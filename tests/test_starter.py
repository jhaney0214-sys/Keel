"""keel init: a starter folder that runs, ties and balances as written."""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import importer, model, results, starter  # noqa: E402
from keel.__main__ import load  # noqa: E402


class Starter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_the_example_ledger_balances(self):
        self.assertAlmostEqual(sum(v for _, _, v in starter.ledger()), 0.0, places=2)
        equity = starter.ledger()[-1][2]
        self.assertLess(equity, 0)                     # a credit, as a trial balance prints it

    def test_the_folder_runs_and_every_file_ties(self):
        for bank in (False, True):
            folder = starter.write(os.path.join(self.tmp, "bank" if bank else "cu"), bank=bank)
            positions, a, _, imported = load(folder)
            self.assertEqual(a.institution, "bank" if bank else "credit_union")
            self.assertTrue(all(t.ties for t in imported.ties), [(t.line, t.difference) for t in imported.ties])
            r = results.compute(positions, a, "starter", imported, folder, assumption_tests=False)
            self.assertTrue(all(c.passed for c in r["checks"]), [c.name for c in r["checks"] if not c.passed])
            self.assertTrue(all(x.status == "within" for x in r["limits"]))
            self.assertTrue(os.path.isfile(os.path.join(folder, "START-HERE.txt")))

    def test_it_will_not_write_over_a_folder(self):
        folder = os.path.join(self.tmp, "cu")
        starter.write(folder)
        with self.assertRaises(model.InputError):
            starter.write(folder)

    def edit(self, folder, name, old, new):
        path = os.path.join(folder, "data", name)
        text = open(path, encoding="utf-8").read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def test_an_unmapped_code_is_named_not_a_crash(self):
        folder = starter.write(os.path.join(self.tmp, "cu"))
        self.edit(folder, "shares.csv", "MMA,", "MONEYMKT,")
        with self.assertRaisesRegex(model.InputError, "shares.csv line 5: code 'MONEYMKT' is not on the shares"):
            load(folder)

    def test_a_cores_own_investment_codes(self):
        folder = starter.write(os.path.join(self.tmp, "cu"))
        # A pool under the core's own code, caught by *, amortizes because it has a WAM.
        self.edit(folder, "investments.csv", "S3002,MBS,", "S3002,FNMA POOL,")
        from keel import xlsx
        path = os.path.join(folder, "data", "product_map.xlsx")
        book = xlsx.read_workbook(path)
        book["investments"].append(["*", "mortgage_securities"])
        xlsx.write_workbook(path, book)
        positions, _, _, _ = load(folder)
        pool = next(p for p in positions if p.id == "S3002")
        self.assertEqual((pool.product, pool.amortization, pool.term_months), ("mortgage_securities", "level", 240))

    def test_an_amortization_column_decides(self):
        folder = starter.write(os.path.join(self.tmp, "cu"))
        path = os.path.join(folder, "data", "investments.csv")
        lines = open(path, encoding="utf-8").read().splitlines()

        def write(first_kind):
            rows = [lines[0] + ",amortization", lines[1] + "," + first_kind, lines[2] + ","]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(chr(10).join(rows) + chr(10))

        write("level")                    # the Treasury note, told to amortize
        positions, _, _, _ = load(folder)
        self.assertEqual(next(p for p in positions if p.id == "S3001").amortization, "level")
        write("sideways")
        with self.assertRaisesRegex(model.InputError, "amortization must be one of"):
            load(folder)

    def test_ledger_accounts_are_mappable(self):
        gl = {"4100": -300.0, "4110": -200.0, "5000": 90.0}
        self.assertEqual(importer._ledger(gl, "41*"), 500.0)
        self.assertEqual(importer._ledger(gl, "4100, 5000"), 390.0)
        tie = importer._ties(gl, [{"current_balance": "500"}], [], [], [], [], {"loans": "41*"})[0]
        self.assertTrue(tie.ties)
        self.assertEqual(tie.line, "Loans (41*)")


if __name__ == "__main__":
    unittest.main()
