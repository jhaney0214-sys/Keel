"""Any input table, from a .csv or an .xlsx, as the same rows.

Credit unions hand over data files as CSV and settings as Excel, and some
export everything to Excel. Every reader in Keel goes through `read_table`,
so a file can be either, and the rows come back as text exactly as a CSV
would have given them: a column ending in `_date` as an ISO date whichever
way it was stored, and a whole number without a trailing `.0`.
"""

import csv
import os

from keel import xlsx
from keel.model import InputError

EXTENSIONS = (".csv", ".xlsx")


def find(folder, stem, required=True):
    """The path of `stem`.csv or `stem`.xlsx in `folder`. Both at once is
    refused rather than guessed between."""
    found = [os.path.join(folder, stem + ext) for ext in EXTENSIONS if os.path.isfile(os.path.join(folder, stem + ext))]
    if len(found) > 1:
        raise InputError("both %s.csv and %s.xlsx are in %s; keep one" % (stem, stem, folder))
    if not found:
        if required:
            raise InputError("missing %s (.csv or .xlsx) in %s" % (stem, folder))
        return None
    return found[0]


#: Tables already read, by (path, sheet, size, modified time): a large core
#: export is read by the importer and again by a saved query, and parsing a
#: 100,000-row workbook takes seconds. A changed file is read afresh.
_READ = {}
_READ_KEEP = 8


def read_table(path, sheet=None):
    """[dict of str] from a CSV, or from the first sheet (or `sheet`) of a
    workbook. Each call returns its own copies of the rows."""
    try:
        stat = os.stat(path)
        key = (os.path.abspath(path), sheet, stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = None
    if key is None or key not in _READ:
        rows = _read_table(path, sheet)
        if key is None:
            return rows
        if len(_READ) >= _READ_KEEP:
            _READ.pop(next(iter(_READ)))
        _READ[key] = rows
    return [dict(row) for row in _READ[key]]


def _read_table(path, sheet=None):
    if path.lower().endswith(".xlsx"):
        try:
            book = xlsx.read_workbook(path)
        except xlsx.WorkbookError as error:
            raise InputError(str(error))
        if not book:
            raise InputError("%s has no sheets" % path)
        rows = book[sheet] if sheet else next(iter(book.values()))
        out = []
        for row in xlsx.table(rows):
            out.append({k: xlsx.excel_date(v) if k.endswith("_date") else xlsx.as_text(v)
                        for k, v in row.items()})
        return out
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(handle)]
