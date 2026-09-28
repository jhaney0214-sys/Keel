"""Keel's accuracy against NCUA call reports, refreshed each quarter.

    python tools/quarterly_scorecard.py

1. Finds every quarter from FIRST to the latest NCUA has published, and
   downloads any call report zip not yet in private/ncua/ (public data from
   ncua.gov; the zips stay in the git-ignored private/ folder).
2. Finds each quarter-end's Treasury par curve: Keel's built-in ones, or
   Treasury's published daily rates (the last business day on or before the
   quarter end), kept in private/ncua/treasury.json.
3. Runs `keel validate` over the whole chain, calibrated on the latest
   quarter with growth from the past year, and writes VALIDATION.md: the
   latest four quarters pooled (a whole year, so seasons even out), each
   quarter, and the size bands. Only aggregate figures; the per-credit-union
   rows, which name real institutions, stay in private/validation/.
4. Appends the rolling-year headline to validation-history.csv, so the
   record shows whether accuracy holds as quarters are added.

It prints what changed. Committing the two files is left to whoever runs it.
"""

import csv
import datetime
import io
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from keel import callreport, validate  # noqa: E402

FIRST = "2024-06"
NCUA = "https://ncua.gov/files/publications/analysis/call-report-data-%s.zip"
TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/%d/all?type=daily_treasury_yield_curve&field_tdr_date_value=%d&page"
                "&_format=csv")
DATA = os.path.join(ROOT, "private", "ncua")
CURVES = os.path.join(DATA, "treasury.json")
OUT = os.path.join(ROOT, "private", "validation")
TENORS = ((1, "1 Mo"), (2, "2 Mo"), (3, "3 Mo"), (4, "4 Mo"), (6, "6 Mo"), (12, "1 Yr"), (24, "2 Yr"), (36, "3 Yr"),
          (60, "5 Yr"), (84, "7 Yr"), (120, "10 Yr"), (240, "20 Yr"), (360, "30 Yr"))


def quarters(today=None):
    today = today or datetime.date.today()
    y, m = int(FIRST[:4]), int(FIRST[5:7])
    out = []
    while datetime.date(y, m, 1) <= today:
        out.append("%04d-%02d" % (y, m))
        y, m = (y + 1, 3) if m == 12 else (y, m + 3)
    return out


def quarter_end(q):
    y, m = int(q[:4]), int(q[5:7])
    return datetime.date(y, m, 30 if m in (6, 9) else 31)


def fetch(url, path):
    request = urllib.request.Request(url, headers={"User-Agent": "Keel quarterly scorecard"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    if path.endswith(".zip") and not body.startswith(b"PK"):
        return False                         # a "not found" page served as 200
    with open(path, "wb") as handle:
        handle.write(body)
    return True


def zips():
    """Paths of the consecutive quarters available, oldest first."""
    os.makedirs(DATA, exist_ok=True)
    found = []
    for q in quarters():
        path = os.path.join(DATA, "call-report-data-%s.zip" % q)
        if not os.path.isfile(path):
            if quarter_end(q) > datetime.date.today():
                break
            print("downloading %s" % (NCUA % q))
            if not fetch(NCUA % q, path):
                print("  not published yet")
                break
        found.append(path)
    return found


def curves(needed):
    """Every needed quarter-end curve into callreport.TREASURY, fetching missing ones."""
    cached = {}
    if os.path.isfile(CURVES):
        with open(CURVES, encoding="utf-8") as handle:
            cached = {k: {int(t): r for t, r in v.items()} for k, v in json.load(handle).items()}
    by_year = {}
    for date in needed:
        if date in callreport.TREASURY or date in cached:
            continue
        year = int(date[:4])
        if year not in by_year:
            print("fetching Treasury's %d daily par curves" % year)
            request = urllib.request.Request(TREASURY_CSV % (year, year), headers={"User-Agent": "Keel"})
            with urllib.request.urlopen(request, timeout=120) as response:
                rows = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
            by_year[year] = {datetime.datetime.strptime(r["Date"], "%m/%d/%Y").date(): r for r in rows}
        end = datetime.date.fromisoformat(date)
        day = max(d for d in by_year[year] if d <= end)
        row = by_year[year][day]
        cached[date] = {t: float(row[c]) for t, c in TENORS if row.get(c)}
        print("  %s: Treasury's curve for %s" % (date, day))
    with open(CURVES, "w", encoding="utf-8") as handle:
        json.dump(cached, handle, indent=1, sort_keys=True)
    callreport.TREASURY.update(cached)


def band_table(summary, measure):
    rows = []
    for band, s in summary.items():
        k, n = s[measure].get("keel"), s[measure].get("naive")
        if k and n:
            rows.append("| %s | %s | %.2f | %+.2f | %.2f | %.0f%% |" % (
                "All credit unions" if band == "all" else band, "{:,}".format(s["count"]), k["median_abs"], k["bias"],
                n["median_abs"], 100 * s[measure]["keel_closer"]))
    return rows


def markdown(result, recent):
    pooled = validate.summarize([r for p in recent for r in p["rows"]])
    first, last = recent[0]["from"], recent[-1]["to"]
    lines = ["# How accurate is Keel?", "",
             "Every quarter, Keel builds every federally insured credit union from NCUA's public call report "
             "(balances its own; rates calibrated to its reported income; behaviour Keel's defaults), forecasts "
             "three months, and is scored against the next quarter's report and against a naive forecast: the "
             "latest quarter's NII and net income repeated, balances unchanged. Generated by "
             "`tools/quarterly_scorecard.py` on %s; method in METHODOLOGY.md." % datetime.date.today().isoformat(),
             "",
             "## The latest four quarters (%s to %s)" % (first, last), "",
             "Median absolute error and median signed error (bias), in percent of the actual; net worth change in "
             "basis points of assets. \"Keel closer\" is the share of credit unions where Keel's error is smaller.",
             "", "| Measure | Keel | Keel bias | Naive | Keel closer |", "|---|---|---|---|---|"]
    a = pooled["all"]
    for m in validate.MEASURES:
        k, n = a[m]["keel"], a[m]["naive"]
        lines.append("| %s | %.2f | %+.2f | %.2f | %.0f%% |" % (validate.LABELS[m], k["median_abs"], k["bias"],
                                                             n["median_abs"], 100 * a[m]["keel_closer"]))
    lines += ["", "%s credit-union quarters." % "{:,}".format(a["count"]), "",
              "## Quarterly NII by size", "", "| Assets | Credit-union quarters | Keel | Keel bias | Naive | "
              "Keel closer |", "|---|---|---|---|---|---|"] + band_table(pooled, "nii")
    lines += ["", "## Each quarter", "", "| Quarter | Credit unions | NII: Keel | NII: naive | NII: Keel closer | "
              "Loans: Keel | Shares: Keel |", "|---|---|---|---|---|---|---|"]
    for p in result["pairs"]:
        s = p["summary"]["all"]
        lines.append("| %s to %s | %s | %.2f | %.2f | %.0f%% | %.2f | %.2f |" % (
            p["from"], p["to"], "{:,}".format(s["count"]), s["nii"]["keel"]["median_abs"],
            s["nii"]["naive"]["median_abs"], 100 * s["nii"]["keel_closer"], s["loans"]["keel"]["median_abs"],
            s["shares"]["keel"]["median_abs"]))
    lines += ["", "Quarters before %s are calibrated without a year-ago report, so their growth is Keel's "
              "defaults; the latest-four table uses only quarters with the full method." % result["pairs"][4]["from"]
              if len(result["pairs"]) > 4 else "", "",
              "## What it does not show", ""]
    weak = [validate.LABELS[m].lower() for m in validate.MEASURES if a[m]["keel_closer"] < 0.52]
    if weak:
        lines.append("- One quarter ahead, %s %s forecast no better than by \"nothing changes\" (Keel closer for "
                     "under 52%% of credit unions); Keel's value there is in the scenarios, not the base forecast."
                     % (" and ".join(weak), "is" if len(weak) == 1 else "are"))
    lines += [
              "- Investment income moves with rates inside the quarter, which a forecast made at the start cannot "
              "know; most of the remaining NII error is there.",
              "- Every credit union here runs on Keel's default behaviour. A credit union's own files and deposit "
              "study should do better; none has yet been compared side by side with a production ALM model."]
    return "\n".join(lines) + "\n", pooled


def main():
    paths = zips()
    if len(paths) < 6:
        print("need at least six consecutive quarters; have %d" % len(paths))
        return 1
    curves([callreport.CallReport(p).as_of for p in paths[:-1]])
    result = validate.run_chain(paths)
    validate.write(result, OUT)
    with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as handle:
        handle.write(validate.chain_text(result) + "\n")
    recent = result["pairs"][-4:]
    text, pooled = markdown(result, recent)
    with open(os.path.join(ROOT, "VALIDATION.md"), "w", encoding="utf-8") as handle:
        handle.write(text)
    history = os.path.join(ROOT, "validation-history.csv")
    head = ["run_date", "through", "credit_union_quarters", "nii_keel", "nii_bias", "nii_naive", "nii_keel_closer",
            "loans_keel", "shares_keel", "assets_keel", "net_worth_bp_keel"]
    a = pooled["all"]
    row = [datetime.date.today().isoformat(), recent[-1]["to"], a["count"],
           round(a["nii"]["keel"]["median_abs"], 2), round(a["nii"]["keel"]["bias"], 2),
           round(a["nii"]["naive"]["median_abs"], 2), round(a["nii"]["keel_closer"], 3),
           round(a["loans"]["keel"]["median_abs"], 2), round(a["shares"]["keel"]["median_abs"], 2),
           round(a["assets"]["keel"]["median_abs"], 2), round(a["net_worth_change"]["keel"]["median_abs"], 2)]
    old = []
    if os.path.isfile(history):
        with open(history, encoding="utf-8") as handle:
            old = [r for r in csv.reader(handle)][1:]
    old = [r for r in old if r[1] != row[1]] + [[str(x) for x in row]]   # one row per quarter scored through
    with open(history, "w", encoding="utf-8", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(head)
        w.writerows(old)
    print("scored through %s: NII %.2f%% (naive %.2f%%), Keel closer %.0f%%; VALIDATION.md and "
          "validation-history.csv written" % (recent[-1]["to"], a["nii"]["keel"]["median_abs"],
                                              a["nii"]["naive"]["median_abs"], 100 * a["nii"]["keel_closer"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
