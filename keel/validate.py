"""Out-of-sample validation against NCUA's public call reports.

    python -m keel validate call-report-data-2026-03.zip call-report-data-2026-06.zip --out private/validation

Every credit union in both cycles is built from the earlier cycle exactly as
`keel callreport` builds it (balances its own, rates calibrated to its
reported income, behaviour Keel's defaults), run forward through the plan
for the months between the cycles, and set against what it reported in the
later cycle:

    net interest income for the quarter   the later year-to-date less the earlier
    total assets, loans, shares           month-end balances
    net worth                             the change over the quarter

Each forecast is scored against a naive one: the last quarter's net
interest income and net income repeated, and balances unchanged. A model that cannot beat
"nothing changes" over one quarter is adding nothing but noise; one that
beats it, and has little bias, has earned its defaults.

Per-credit-union rows are written to `<out>/rows.csv`; they describe real,
named institutions on Keel's default behaviour, so the output folder belongs
in the git-ignored `private/`. The summary is aggregate.
"""

import csv
import os
import statistics

from keel import callreport, engine, model
from keel.model import InputError

MEASURES = ("nii", "assets", "loans", "shares", "net_worth_change")
BANDS = (("under $10M", 0, 10e6), ("$10M to $100M", 10e6, 100e6), ("$100M to $1B", 100e6, 1e9),
         ("$1B and over", 1e9, float("inf")))
LOAN_PRODUCTS = {p for p, *_ in callreport.LOANS} | {"other_loans"}
SHARE_PRODUCTS = {"regular_shares", "share_drafts", "money_market", "ira_shares", "certificates"}


def positions_from(rows):
    out = []
    for r in rows:
        out.append(model.Position(
            id=r["id"], name=r["name"], product=r["product"], side=r["side"], balance=float(r["balance"]),
            rate=float(r.get("rate") or 0) / 100.0, rate_type=r["rate_type"], index=r.get("index", ""),
            margin=float(r.get("margin") or 0) / 100.0, reset_months=int(r.get("reset_months") or 0),
            term_months=int(r.get("term_months") or 0), amortization=r["amortization"],
            amort_months=int(r.get("amort_months") or 0)))
    return out


def _nii_ytd(report, cu):
    return report.get(cu, "interest_income") - report.get(cu, "interest_expense")


def forecast(earlier, cu, months, base_case="flat", prior=None, year_ago=None):
    """Keel's forecast for `cu` `months` ahead, from the earlier cycle
    (calibrated on its latest quarter when `prior` is given, with growth
    from the past year when `year_ago` is)."""
    rows, raw, _ = callreport.build(earlier, cu, prior=prior, year_ago=year_ago)
    raw["base_case"] = base_case
    a = model.parse_assumptions(raw)
    positions = positions_from(rows)
    model.check(positions, a)
    run = engine.going_concern(positions, a, a.scenarios[0], months=months)
    _, _, _, opening_equity = engine.opening(positions)
    end = run[months - 1]
    return {"nii": sum(m.nii for m in run[:months]), "assets": end.assets,
            "loans": sum(v for k, v in end.balances.items() if k in LOAN_PRODUCTS),
            "shares": sum(v for k, v in end.balances.items() if k in SHARE_PRODUCTS),
            "net_worth_change": end.equity - opening_equity}


def run(earlier_path, later_path, limit=None, base_case="flat", min_assets=0.0, prior_path=None,
        year_ago_path=None):
    earlier, later = callreport.CallReport(earlier_path), callreport.CallReport(later_path)
    prior = callreport.CallReport(prior_path) if prior_path else None
    year_ago = callreport.CallReport(year_ago_path) if year_ago_path else None
    latest = prior is not None and prior.as_of[:4] == earlier.as_of[:4]
    months = later.months - earlier.months if later.as_of[:4] == earlier.as_of[:4] else later.months
    if months <= 0:
        raise InputError("the second call report must be later than the first")
    if later.as_of[:4] != earlier.as_of[:4] and later.months != months:
        raise InputError("across a year end, compare the fourth quarter with the next first quarter only")
    same_year = later.as_of[:4] == earlier.as_of[:4]
    days = later.days - earlier.days if same_year else later.days
    both = sorted(set(earlier.data) & set(later.data), key=lambda c: -earlier.get(c, "assets"))
    rows, skipped = [], {}
    for cu in both:
        assets = earlier.get(cu, "assets")
        if assets <= max(min_assets, 0.0) or later.get(cu, "assets") <= 0:
            skipped["no assets"] = skipped.get("no assets", 0) + 1
            continue
        try:
            f = forecast(earlier, cu, months, base_case, prior if latest else None, year_ago)
        except (InputError, ValueError, ZeroDivisionError) as error:
            key = type(error).__name__
            skipped[key] = skipped.get(key, 0) + 1
            continue
        # The naive forecast repeats the latest quarter the reports can show.
        if latest and cu in prior.data:
            prior_nii = (_nii_ytd(earlier, cu) - _nii_ytd(prior, cu)) * days / float(earlier.days - prior.days)
            prior_ni = (earlier.get(cu, "net_income") - prior.get(cu, "net_income")) * days / float(
                earlier.days - prior.days)
        else:
            prior_nii = _nii_ytd(earlier, cu) * days / float(earlier.days)
            prior_ni = earlier.get(cu, "net_income") * days / float(earlier.days)
        # The plan's months are equal twelfths of a year; interest accrues by
        # the day. Keel's quarter is set to the quarter's actual days.
        f["nii"] *= days / (365.0 * months / 12.0)
        actual_nii = _nii_ytd(later, cu) - (_nii_ytd(earlier, cu) if same_year else 0.0)
        actual = {"nii": actual_nii, "assets": later.get(cu, "assets"), "loans": later.get(cu, "loans"),
                  "shares": later.get(cu, "shares"),
                  "net_worth_change": later.get(cu, "net_worth") - earlier.get(cu, "net_worth")}
        naive = {"nii": prior_nii, "assets": assets, "loans": earlier.get(cu, "loans"),
                 "shares": earlier.get(cu, "shares"), "net_worth_change": prior_ni}
        row = {"cu": cu, "name": earlier.name(cu), "assets": assets}
        for m in MEASURES:
            row[m + "_actual"], row[m + "_keel"], row[m + "_naive"] = actual[m], f[m], naive[m]
        rows.append(row)
        if limit and len(rows) >= limit:
            break
    return {"from": earlier.as_of, "to": later.as_of, "months": months, "base_case": base_case, "rows": rows,
            "skipped": skipped, "summary": summarize(rows)}


def _errors(rows, measure, which):
    out = []
    for r in rows:
        actual = r[measure + "_actual"]
        if measure == "net_worth_change":
            # A change can be near zero, so it is scored against assets, in basis points.
            out.append(10000.0 * (r[measure + "_" + which] - actual) / r["assets"])
        elif actual > 0:
            out.append(100.0 * (r[measure + "_" + which] - actual) / actual)
    return out


def summarize(rows):
    """{band: {measure: {which: stats}}} with band "all" for everything."""
    out = {}
    for label, lo, hi in (("all", 0, float("inf")),) + BANDS:
        group = [r for r in rows if lo <= r["assets"] < hi]
        if not group:
            continue
        band = out[label] = {"count": len(group)}
        for m in MEASURES:
            band[m] = {}
            for which in ("keel", "naive"):
                e = _errors(group, m, which)
                if not e:
                    continue
                absolute = sorted(abs(x) for x in e)
                band[m][which] = {"median_abs": statistics.median(absolute), "bias": statistics.median(e),
                                  "p90_abs": absolute[int(0.9 * (len(absolute) - 1))],
                                  "within": sum(1 for x in absolute if x <= (25 if m == "net_worth_change" else 2))
                                  / float(len(absolute))}
            k, n = _errors(group, m, "keel"), _errors(group, m, "naive")
            if k and n:
                band[m]["keel_closer"] = sum(1 for a, b in zip(k, n) if abs(a) < abs(b)) / float(len(k))
    return out


LABELS = {"nii": "Quarter NII", "assets": "Total assets", "loans": "Loans", "shares": "Shares",
          "net_worth_change": "Net worth change"}


def text(result):
    lines = ["Keel against NCUA call reports: built from %s, forecast %d months, scored against %s (base case %s)." % (
        result["from"], result["months"], result["to"], result["base_case"]),
        "%d credit unions scored; skipped: %s." % (
            len(result["rows"]), ", ".join("%s %d" % kv for kv in sorted(result["skipped"].items())) or "none"),
        "Errors in percent of the actual (net worth change: basis points of assets). Median absolute error, "
        "median signed error (bias), and the share of credit unions where Keel is closer than the naive "
        "forecast (last quarter's NII and net income repeated, balances unchanged).", ""]
    for band, s in result["summary"].items():
        lines.append("%s (%d)" % (band, s["count"]))
        lines.append("  %-18s %22s %22s %10s" % ("", "Keel: median | bias", "naive: median | bias", "Keel closer"))
        for m in MEASURES:
            k, n = s[m].get("keel"), s[m].get("naive")
            if not k:
                continue
            lines.append("  %-18s %10.2f | %+8.2f %12.2f | %+8.2f %10.0f%%" % (
                LABELS[m], k["median_abs"], k["bias"], n["median_abs"], n["bias"], 100 * s[m].get("keel_closer", 0)))
        lines.append("")
    return "\n".join(lines)


def write(result, folder):
    os.makedirs(folder, exist_ok=True)
    if result["rows"]:
        with open(os.path.join(folder, "rows.csv"), "w", encoding="utf-8", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=list(result["rows"][0]))
            w.writeheader()
            w.writerows(result["rows"])
    with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as handle:
        handle.write(text(result) + "\n")


def run_chain(paths, base_case="flat", latest=True):
    """Each consecutive pair of call reports, and every pair's rows pooled.
    With `latest`, each build and naive forecast uses the latest quarter the
    reports show, not the year to date."""
    pairs = [run(a, b, base_case=base_case, prior_path=(paths[i - 1] if latest and i > 0 else None),
                 year_ago_path=(paths[i - 4] if latest and i >= 4 else None))
             for i, (a, b) in enumerate(zip(paths, paths[1:]))]
    pooled = []
    for p in pairs:
        pooled += [dict(r, period="%s to %s" % (p["from"], p["to"])) for r in p["rows"]]
    return {"pairs": pairs, "rows": pooled, "summary": summarize(pooled), "base_case": base_case,
            "from": pairs[0]["from"], "to": pairs[-1]["to"], "months": pairs[0]["months"],
            "skipped": {k: sum(p["skipped"].get(k, 0) for p in pairs) for p in pairs for k in p["skipped"]}}


def chain_text(result):
    lines = ["Keel against NCUA call reports, %d quarters from %s to %s (base case %s)." % (
        len(result["pairs"]), result["from"], result["to"], result["base_case"]), "",
        "Each quarter, all credit unions: median absolute error | bias for Keel, then for the naive forecast, "
        "then the share where Keel is closer.", ""]
    for p in result["pairs"]:
        s = p["summary"]["all"]
        lines.append("%s to %s (%d)" % (p["from"], p["to"], s["count"]))
        for m in MEASURES:
            k, n = s[m]["keel"], s[m]["naive"]
            lines.append("  %-18s %6.2f | %+6.2f   %6.2f | %+6.2f   %4.0f%%" % (
                LABELS[m], k["median_abs"], k["bias"], n["median_abs"], n["bias"], 100 * s[m]["keel_closer"]))
        lines.append("")
    lines.append("All quarters pooled:")
    lines.append(text(result).split("\n", 4)[4])
    return "\n".join(lines)
