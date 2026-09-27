"""Excel inputs: the settings workbook, and any data file as .xlsx.

The hard case is a workbook Excel itself saved, which Keel's own writer never
produces: text in the shared-strings table, rich-text runs, formulas with a
cached value, dates as day counts, and rows and cells with gaps. So one test
builds a workbook in exactly that shape by hand.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import engine, measures, model, settings, tables, xlsx  # noqa: E402

SAMPLE = os.path.join(ROOT, "examples", "sample-cu")
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def excel_style_workbook(path):
    """A workbook shaped the way Excel saves one."""
    shared = ["loan_id", "maturity_date", "balance", "note", "L1", "Plain ", "L2"]
    sst = "".join("<si><t>%s</t></si>" % s for s in shared)
    sst += "<si><r><t>rich </t></r><r><rPr><b/></rPr><t>text</t></r></si>"      # index 7
    sheet = ('<worksheet xmlns="%s"><sheetData>'
             '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
             '<c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>'
             '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2" s="3"><v>46203</v></c>'
             '<c r="C2"><f>1000*12.5</f><v>12500</v></c><c r="D2" t="s"><v>7</v></c></row>'
             '<row r="4"><c r="A4" t="s"><v>6</v></c><c r="C4"><v>250.75</v></c>'
             '<c r="D4" t="str"><f>"cached"</f><v>cached</v></c></row>'
             '</sheetData></worksheet>' % MAIN)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml",
                   '<workbook xmlns="%s" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships"><sheets><sheet name="Loans" sheetId="1" r:id="rId1"/></sheets></workbook>' % MAIN)
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="worksheet" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s">%s</sst>' % (MAIN, sst))
        z.writestr("xl/worksheets/sheet1.xml", sheet)


class Reader(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_a_workbook_as_excel_saves_it(self):
        path = os.path.join(self.tmp, "loans.xlsx")
        excel_style_workbook(path)
        rows = tables.read_table(path)
        self.assertEqual(rows, [
            {"loan_id": "L1", "maturity_date": "2026-06-30", "balance": "12500", "note": "rich text"},
            {"loan_id": "L2", "maturity_date": "", "balance": "250.75", "note": "cached"}])

    def test_writing_then_reading_keeps_every_value(self):
        path = os.path.join(self.tmp, "t.xlsx")
        sheet = [["name", "amount", "flag", "gap", "text"],
                 ["Credit & <Union> \"one\"", 1234.5, True, None, "Ünïcode ✓"],
                 ["second", 7, False, None, "  spaced  "]]
        xlsx.write_workbook(path, {"Sheet one": sheet})
        back = xlsx.read_workbook(path)["Sheet one"]
        self.assertEqual(back[1][:3], ["Credit & <Union> \"one\"", 1234.5, True])
        self.assertEqual(back[1][4], "Ünïcode ✓")
        self.assertEqual(back[2][4], "  spaced  ")

    def test_an_excel_date_and_a_typed_date_read_the_same(self):
        self.assertEqual(xlsx.excel_date(46203), "2026-06-30")
        self.assertEqual(xlsx.excel_date("6/30/2026"), "2026-06-30")
        self.assertEqual(xlsx.excel_date("2026-06-30"), "2026-06-30")

    def test_an_old_xls_is_refused_plainly(self):
        path = os.path.join(self.tmp, "old.xlsx")
        with open(path, "wb") as handle:
            handle.write(b"\xd0\xcf\x11\xe0 not a zip")
        with self.assertRaisesRegex(model.InputError, "not an .xlsx workbook"):
            tables.read_table(path)

    def test_both_a_csv_and_a_workbook_is_refused_not_guessed(self):
        for ext in (".csv", ".xlsx"):
            open(os.path.join(self.tmp, "loans" + ext), "w").close()
        with self.assertRaisesRegex(model.InputError, "keep one"):
            tables.find(self.tmp, "loans")


class SettingsWorkbook(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_json_to_workbook_and_back_reads_the_same(self):
        path = os.path.join(self.tmp, "assumptions.xlsx")
        settings.convert(os.path.join(SAMPLE, "assumptions.json"), path)
        a = model.read_assumptions(os.path.join(SAMPLE, "assumptions.json"))
        b = model.read_assumptions(path)
        self.assertEqual(a.products, b.products)
        self.assertEqual((a.curve.tenors, a.curve.rates), (b.curve.tenors, b.curve.rates))
        self.assertEqual([(s.name, s.shock_bp, s.ramp_months) for s in a.scenarios],
                         [(s.name, s.shock_bp, s.ramp_months) for s in b.scenarios])
        self.assertEqual((a.as_of, a.fee_income, a.stress_months, a.contingent),
                         (b.as_of, b.fee_income, b.stress_months, b.contingent))

    def test_the_same_book_gives_the_same_answer_from_either_file(self):
        xlsx_path = os.path.join(self.tmp, "assumptions.xlsx")
        settings.convert(os.path.join(SAMPLE, "assumptions.json"), xlsx_path)
        positions = model.read_positions(os.path.join(SAMPLE, "positions.csv"))
        results = []
        for path in (os.path.join(SAMPLE, "assumptions.json"), xlsx_path):
            a = model.read_assumptions(path)
            run = engine.going_concern(positions, a, a.scenarios[0])
            results.append(measures.income_statement(measures.year(run, 1))["net_interest_income"])
        self.assertEqual(results[0], results[1])

    def test_a_shaped_scenario_is_written_and_read_as_text(self):
        raw = read_json(os.path.join(SAMPLE, "assumptions.json"))
        raw["extra_scenarios"] = [{"name": "flattener", "shape": {"1": 200, "24": 100, "120": 0}}]
        sheets = settings.to_workbook(raw)
        self.assertIn(["flattener", None, None, "1:200, 24:100, 120:0", None], sheets["Scenarios"])
        path = os.path.join(self.tmp, "a.xlsx")
        xlsx.write_workbook(path, sheets)
        self.assertEqual(settings.load(path)["extra_scenarios"][0]["shape"], {"1": 200.0, "24": 100.0, "120": 0.0})

    def test_a_mistyped_setting_names_its_sheet_and_cell(self):
        raw = read_json(os.path.join(SAMPLE, "assumptions.json"))
        sheets = settings.to_workbook(raw)
        row = next(r for r in sheets["Products"][1:] if r[0] == "new_auto")
        row[sheets["Products"][0].index("cpr")] = "eighteen"
        path = os.path.join(self.tmp, "a.xlsx")
        xlsx.write_workbook(path, sheets)
        with self.assertRaisesRegex(model.InputError, "Products, new_auto, cpr: 'eighteen' is not a number"):
            settings.load(path)

    def test_an_unknown_product_column_is_refused(self):
        raw = read_json(os.path.join(SAMPLE, "assumptions.json"))
        sheets = settings.to_workbook(raw)
        sheets["Products"][0].append("prepay_speed")
        path = os.path.join(self.tmp, "a.xlsx")
        xlsx.write_workbook(path, sheets)
        with self.assertRaisesRegex(model.InputError, "unknown column 'prepay_speed'"):
            settings.load(path)


class DataWorkbooks(unittest.TestCase):

    def test_positions_from_a_workbook_match_the_csv(self):
        tmp = tempfile.mkdtemp()
        try:
            rows = tables.read_table(os.path.join(SAMPLE, "positions.csv"))
            head = list(rows[0])
            sheet = [head] + [[r[h] for h in head] for r in rows]
            path = os.path.join(tmp, "positions.xlsx")
            xlsx.write_workbook(path, {"positions": sheet})
            a = model.read_positions(os.path.join(SAMPLE, "positions.csv"))
            b = model.read_positions(path)
            self.assertEqual([(p.id, p.balance, p.rate, p.term_months) for p in a],
                             [(p.id, p.balance, p.rate, p.term_months) for p in b])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
