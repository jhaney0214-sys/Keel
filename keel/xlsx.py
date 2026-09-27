"""Read and write .xlsx workbooks with the standard library alone.

An .xlsx file is a zip of XML parts. Keel reads what a credit union's
workbooks actually contain (numbers, text in the shared-string table or
inline, booleans, and the cached value of a formula) and ignores everything
else (styles, charts, comments). It writes plain workbooks: text inline,
numbers as numbers, the header row bold and frozen, and columns wide enough to
read. No third-party package is needed, which keeps Keel's promise that there
is nothing to install.

Dates in Excel are day counts from 1899-12-30. A cell's display format is
what makes Excel show one as a date, and reading styles to find that out is
fragile, so the caller says which columns hold dates (`excel_date`).
"""

import datetime
import re
import zipfile
from xml.etree import ElementTree
from xml.sax.saxutils import escape

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
      "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}
EPOCH = datetime.date(1899, 12, 30)


class WorkbookError(ValueError):
    pass


def _column(ref):
    """'C12' -> 2 (zero-based)."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _text(node):
    """All the text in a string item, including rich-text runs."""
    return "".join(t.text or "" for t in node.iter("{%s}t" % NS["m"]))


def read_workbook(path):
    """{sheet name: [row, ...]} with each row a list of cell values: str,
    float, bool or None. Rows keep their gaps, so a blank row is []."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise WorkbookError("%s is not an .xlsx workbook (an old .xls needs saving as .xlsx)" % path)
    with z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [_text(si) for si in root.findall("m:si", NS)]
        book = ElementTree.fromstring(z.read("xl/workbook.xml"))
        rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", NS)}
        out = {}
        for sheet in book.find("m:sheets", NS).findall("m:sheet", NS):
            rid = sheet.get("{%s}id" % NS["r"])
            part = target[rid].lstrip("/")
            part = part if part.startswith("xl/") else "xl/" + part
            with z.open(part) as handle:
                out[sheet.get("name")] = _read_sheet(handle, shared)
        return out


ROW, CELL, VALUE, INLINE = ("{%s}%s" % (NS["m"], t) for t in ("row", "c", "v", "is"))


def _read_sheet(handle, shared):
    """Rows from a worksheet part, streamed: each cell is read as it closes
    and its row released after, so a 100,000-loan sheet never sits in memory
    as a tree. The first version built the tree and took 7.8 seconds on the
    large sample's 105,100 loans; streaming takes under half that."""
    rows, values = [], []
    column = {}
    for _, node in ElementTree.iterparse(handle):
        tag = node.tag
        if tag == CELL:
            ref = node.get("r")
            if ref:
                letters = ref.rstrip("0123456789")
                col = column.get(letters)
                if col is None:
                    col = column[letters] = _column(letters)
                while len(values) < col:
                    values.append(None)
            kind = node.get("t", "n")
            if kind == "inlineStr":
                inline = node.find(INLINE)
                value = _text(inline) if inline is not None else ""
            else:
                v = node.find(VALUE)
                text = v.text if v is not None else None
                if text is None:
                    value = None
                elif kind == "s":
                    value = shared[int(text)]
                elif kind == "b":
                    value = text.strip() == "1"
                elif kind in ("str", "e"):
                    value = text
                else:
                    value = float(text)
            values.append(value)
            node.clear()
        elif tag == ROW:
            index = int(node.get("r", len(rows) + 1)) - 1
            while len(rows) < index:
                rows.append([])
            rows.append(values)
            values = []
            node.clear()
    return rows


def table(rows):
    """Header row and data rows -> [dict]. The header is the first non-empty
    row; blank data rows are skipped; blank header cells are dropped."""
    rows = [r for r in rows]
    while rows and not any(v not in (None, "") for v in rows[0]):
        rows.pop(0)
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(v not in (None, "") for v in r):
            continue
        out.append({h: (r[i] if i < len(r) else None) for i, h in enumerate(header) if h})
    return out


def excel_date(value):
    """An ISO date from a date cell, whether Excel stored it as a day count or
    someone typed it as text."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (EPOCH + datetime.timedelta(days=int(value))).isoformat()
    text = str(value).strip()
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        m, d, y = (int(g) for g in match.groups())
        return datetime.date(y, m, d).isoformat()
    return text[:10]


def as_text(value):
    """A cell as the text a CSV would have held: 12.0 -> '12', True -> 'true'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


# --------------------------------------------------------------- writing

def _ref(col, row):
    name = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        name = chr(65 + rem) + name
    return "%s%d" % (name, row)


def _cell(value, col, row, bold):
    ref = _ref(col, row)
    style = ' s="1"' if bold else ""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return '<c r="%s" t="b"%s><v>%d</v></c>' % (ref, style, int(value))
    if isinstance(value, (int, float)):
        return '<c r="%s"%s><v>%r</v></c>' % (ref, style, value)
    return '<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>' % (
        ref, style, escape(str(value)))


def _sheet_xml(rows):
    widths = {}
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths.get(i, 8), min(60, len(str(v if v is not None else "")) + 2))
    cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (i + 1, i + 1, w)
                   for i, w in sorted(widths.items()))
    body = "".join('<row r="%d">%s</row>' % (n, "".join(_cell(v, i, n, n == 1) for i, v in enumerate(r)))
                   for n, r in enumerate(rows, 1))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="%s"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>%s<sheetData>%s</sheetData>'
            '</worksheet>' % (NS["m"], "<cols>%s</cols>" % cols if cols else "", body))


def write_workbook(path, sheets):
    """`sheets` is an ordered {name: [row, ...]}; the first row is the header."""
    names = list(sheets)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1)
                             for i in range(len(names))) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="%s"><Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>' % NS["rel"])
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>' % (
                       NS["m"], NS["r"], "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (
                           escape(n), i + 1, i + 1) for i, n in enumerate(names))))
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="%s">%s'
                   '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/styles" Target="styles.xml"/></Relationships>' % (
                       NS["rel"], "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
                                          'officeDocument/2006/relationships/worksheet" '
                                          'Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1)
                                          for i in range(len(names))), len(names) + 1))
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="%s">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                   '<fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
                   '</styleSheet>' % NS["m"])
        for i, n in enumerate(names):
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), _sheet_xml(sheets[n]))
