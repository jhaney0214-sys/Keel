"""python -m keel run <folder with positions.csv and assumptions.json> [--out DIR] [--name NAME]"""

import argparse
import datetime
import os
import sys

from keel import importer, model, report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="keel", description="ALM, plan and liquidity from one projection.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="project, measure, reconcile and write the report")
    run.add_argument("folder")
    run.add_argument("--out", default=None, help="report folder (default: <folder>/report)")
    run.add_argument("--name", default=None, help="default: the first sentence of the assumptions' notes")
    run.add_argument("--quick", action="store_true", help="skip the key-assumption tests (faster)")
    what = sub.add_parser("whatif", help="run a what-if against the base and compare")
    what.add_argument("folder")
    what.add_argument("spec", help="a what-if JSON file")
    what.add_argument("--out", default=None, help="default: <folder>/report/whatif-<file name>")
    conv = sub.add_parser("convert", help="convert a settings file between .json and .xlsx")
    conv.add_argument("source")
    conv.add_argument("target")
    price = sub.add_parser("price", help="RAROC pricing: a deal's life economics and the rate it needs")
    price.add_argument("folder")
    price.add_argument("--product", required=True)
    price.add_argument("--amount", type=float, required=True)
    price.add_argument("--term", type=int, default=0, help="months (0 for a non-maturity deposit)")
    price.add_argument("--rate", type=float, default=None, help="percent; leave out to solve for the hurdle rate")
    price.add_argument("--amortization", default=None, help="level, bullet, balloon or nonmaturity")
    price.add_argument("--amort-months", type=int, default=0, help="balloon amortization period")
    price.add_argument("--fee", type=float, default=0.0, help="upfront fee, percent of the amount")
    for name in ("cpr", "runoff", "charge-off", "servicing-cost", "fee-yield", "origination-cost", "risk-weight"):
        price.add_argument("--" + name, type=float, default=None, help="percent; overrides the product's")
    newp = sub.add_parser("newproduct", help="spread analysis of a proposed product, and its effect on the book")
    newp.add_argument("folder")
    newp.add_argument("proposal", help="a proposal JSON file")
    newp.add_argument("--out", default=None, help="default: <folder>/report/newproduct-<file name>.html")
    swp = sub.add_parser("swap", help="an investment purchase or swap: pickup, loss, earn-back, effect on the book")
    swp.add_argument("folder")
    swp.add_argument("trade", help="a trade JSON file")
    swp.add_argument("--out", default=None, help="default: <folder>/report/swap-<file name>.html")
    spc = sub.add_parser("special", help="deposit pricing: a certificate special's marginal cost, and rate moves")
    spc.add_argument("folder")
    spc.add_argument("spec", help="a special JSON file")
    spc.add_argument("--out", default=None, help="default: <folder>/report/special-<file name>.html")
    qry = sub.add_parser("query", help="ad hoc report: group, filter and total any table")
    qry.add_argument("folder")
    qry.add_argument("spec", nargs="?", help="a saved query JSON file (or use the options)")
    qry.add_argument("--table", default="positions")
    qry.add_argument("--by", default="", help="comma-separated fields")
    qry.add_argument("--measure", action="append", default=None, help='e.g. "sum balance", "wavg rate balance"')
    qry.add_argument("--where", action="append", default=None, help='e.g. "side = asset"')
    qry.add_argument("--sort", default=None)
    qry.add_argument("--limit", type=int, default=None)
    qry.add_argument("--out", default=None, help="write .csv or .xlsx")
    cr = sub.add_parser("callreport", help="build a folder for any credit union from NCUA's public call report data")
    cr.add_argument("zip", help="an NCUA quarterly call report zip, e.g. call-report-data-2026-06.zip")
    cr.add_argument("--search", default=None, help="list credit unions whose name, city or charter matches")
    cr.add_argument("--cu", default=None, help="the charter number (CU_NUMBER) to build")
    cr.add_argument("--out", default=None, help="default: examples/cu-<charter>")
    cr.add_argument("--curve", default=None, help="a JSON file of {tenor months: rate percent}, when Keel does "
                                                  "not have the cycle date's Treasury curve")
    cr.add_argument("--run", action="store_true", help="run the report straight after building the folder")
    cr.add_argument("--prior", default=None, help="the previous quarter's zip, same year: calibrate interest on "
                                                  "the latest quarter")
    cr.add_argument("--year-ago", default=None, help="the zip from four quarters earlier: set loan and share growth "
                                                     "from this credit union's year and its peer group's")
    cmp_ = sub.add_parser("compare", help="a second opinion: Keel beside another ALM model's figures on the same book")
    cmp_.add_argument("folder")
    cmp_.add_argument("other", nargs="?", help="the other model's figures (measure, scenario, value)")
    cmp_.add_argument("--template", default=None, help="write a blank template to this path instead")
    cmp_.add_argument("--name", default="The other model", help="what to call the other model on the page")
    cmp_.add_argument("--out", default=None, help="default: <folder>/report/second-opinion.html")
    ini = sub.add_parser("init", help="a starter folder with every input file, ready to fill with your own data")
    ini.add_argument("folder")
    ini.add_argument("--bank", action="store_true", help="a bank rather than a credit union")
    val = sub.add_parser("validate", help="score Keel's one-quarter forecasts against NCUA call reports")
    val.add_argument("zips", nargs="+", help="two or more consecutive quarterly call report zips, oldest first")
    val.add_argument("--out", default=os.path.join("private", "validation"),
                     help="where the per-credit-union rows and the summary go (keep it git-ignored)")
    val.add_argument("--base-case", default="flat", help="flat, forward or forecast")
    val.add_argument("--ytd", action="store_true", help="calibrate on the year to date, not the latest quarter")
    srv = sub.add_parser("serve", help="what-if, pricing, new-product and explore pages, on this computer only")
    srv.add_argument("folder")
    srv.add_argument("--port", type=int, default=8750)
    args = parser.parse_args(argv)
    if args.command == "whatif":
        return run_whatif(args)
    if args.command == "convert":
        from keel import settings
        try:
            settings.convert(args.source, args.target)
        except model.InputError as error:
            print("input error: %s" % error, file=sys.stderr)
            return 2
        print("%s -> %s" % (args.source, args.target))
        return 0
    if args.command == "compare":
        from keel import compare, results as results_module
        if not args.template and not args.other:
            print("give the other model's figures, or --template <path> for a blank one", file=sys.stderr)
            return 2
        try:
            positions, assumptions, _, imported = load(args.folder)
            r = results_module.compute(positions, assumptions, os.path.basename(os.path.abspath(args.folder)),
                                       imported, None, assumption_tests=False)
            if args.template:
                n = compare.write_template(args.template, positions, assumptions, r)
                print("template with %d figures -> %s (fill in the value column from the other model's report)"
                      % (n, args.template))
                return 0
            rows, notes = compare.compare(positions, assumptions, r, compare.read_other(args.other))
        except model.InputError as error:
            print("input error: %s" % error, file=sys.stderr)
            return 2
        out = args.out or os.path.join(args.folder, "report", "second-opinion.html")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        from keel import terms
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(terms.translate(compare.page(os.path.basename(os.path.abspath(args.folder)), rows, notes,
                                                      report.STYLE, args.name), assumptions))
        judged = [x for x in rows if x["within"] is not None]
        print("second opinion -> %s: %d of %d figures within tolerance" % (
            out, sum(1 for x in judged if x["within"]), len(judged)))
        for key, text in notes:
            print("  look at: %s" % key.replace("_", " "))
        return 0
    if args.command == "init":
        from keel import starter
        try:
            starter.write(args.folder, bank=args.bank)
        except model.InputError as error:
            print("input error: %s" % error, file=sys.stderr)
            return 2
        print("starter folder -> %s" % args.folder)
        print("Run it now:  python -m keel run \"%s\"   then read START-HERE.txt in it." % args.folder)
        return 0
    if args.command == "validate":
        from keel import validate
        if len(args.zips) < 2:
            print("give at least two call report zips, oldest first", file=sys.stderr)
            return 2
        try:
            result = validate.run_chain(args.zips, base_case=args.base_case, latest=not args.ytd)
        except model.InputError as error:
            print("input error: %s" % error, file=sys.stderr)
            return 2
        validate.write(result, args.out)
        with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8") as handle:
            handle.write(validate.chain_text(result) + "\n")
        print(validate.chain_text(result))
        print("rows and summary -> %s" % args.out)
        return 0
    if args.command in ("price", "newproduct", "swap", "special", "query", "callreport"):
        try:
            return {"price": run_price, "newproduct": run_newproduct, "swap": run_swap, "special": run_special,
                    "query": run_query,
                    "callreport": run_callreport}[args.command](args)
        except model.InputError as error:
            print("input error: %s" % error, file=sys.stderr)
            return 2
    if args.command == "serve":
        from keel import serve
        serve.serve(args.folder, args.port)
        return 0

    out = args.out or os.path.join(args.folder, "report")
    try:
        positions, assumptions, _, imported = load(args.folder)
        if imported is not None:
            os.makedirs(out, exist_ok=True)
            importer.write_positions(positions, os.path.join(out, "positions_imported.csv"))
    except model.InputError as error:
        print("input error: %s" % error, file=sys.stderr)
        return 2
    name = args.name or assumptions.notes.get("about", "Credit union").split(".")[0]
    result = report.build(positions, assumptions, out, name, imported, args.folder,
                          assumption_tests=False if args.quick else None)
    failed = [c for c in result["checks"] if not c.passed]
    print("report -> %s  (every table: results.xlsx)" % os.path.join(out, "report.html"))
    if assumptions.institution == "bank":
        eve = next(x for x in result["limits"] if x.key == "nev_ratio_min")
        risk = "EVE ratio after the worst +/-300bp %.2f%%" % eve.value
    else:
        risk = "NEV ratio after +300bp %.2f%% (%s)" % (100 * result["test"]["post_shock_ratio"],
                                                     result["test"]["ratio_rating"])
    print("year-one NII %s; %s; reconciliation %d of %d passed" % (
        "{:,.0f}".format(result["nii_year1"]), risk, len(result["checks"]) - len(failed), len(result["checks"])))
    marks = {"within": "ok", "near": "NEAR", "breach": "BREACH"}
    flagged = [x for x in result["limits"] if x.status != "within"]
    from keel import terms
    print(terms.translate("limits: %d of %d within%s" % (
        len(result["limits"]) - len(flagged), len(result["limits"]),
        "".join("; %s %s" % (marks[x.status], x.label) for x in flagged)), assumptions))
    for c in failed:
        print("FAILED: %s (%s)" % (c.name, c.detail), file=sys.stderr)
    return 1 if failed else 0


def load(folder, out=None):
    """(positions, assumptions, raw assumptions JSON, imported or None)."""
    from keel import settings, tables
    raw = settings.load(settings.find(folder))
    assumptions = model.parse_assumptions(raw)
    imported = None
    if os.path.isdir(os.path.join(folder, "data")):
        # Core-system files: import, pool and tie them to the GL first.
        imported = importer.import_folder(folder, datetime.date.fromisoformat(assumptions.as_of))
        positions = imported.positions
    else:
        positions = model.read_positions(tables.find(folder, "positions"))
    model.check(positions, assumptions)
    return positions, assumptions, raw, imported


def run_whatif(args):
    import json
    from keel import whatif
    try:
        positions, assumptions, raw, imported = load(args.folder)
        with open(args.spec, encoding="utf-8") as handle:
            spec = json.load(handle)
        changed, changed_assumptions, notes = whatif.apply(positions, raw, spec)
    except (model.InputError, KeyError, ValueError) as error:
        print("input error: %s" % error, file=sys.stderr)
        return 2
    title = spec.get("name", os.path.splitext(os.path.basename(args.spec))[0])
    out = args.out or os.path.join(args.folder, "report", "whatif-" + os.path.splitext(os.path.basename(args.spec))[0])
    before = whatif.key_measures(positions, assumptions)
    after = whatif.key_measures(changed, changed_assumptions)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "comparison.html"), "w", encoding="utf-8") as handle:
        handle.write(whatif.comparison(title, notes, before, after))
    report.build(changed, changed_assumptions, out, "What-if: " + title)
    print("comparison -> %s" % os.path.join(out, "comparison.html"))
    for (label, b, kind), (_, a, _) in zip(before, after):
        print("  %-48s %14s -> %-14s %s" % (label, whatif._fmt(b, kind), whatif._fmt(a, kind),
                                            whatif._delta(b, a, kind)))
    for note in notes:
        if note.startswith("WARNING"):
            print(note, file=sys.stderr)
    return 0


def run_callreport(args):
    import json
    from keel import callreport
    report_data = callreport.CallReport(args.zip)
    if args.search or not args.cu:
        matches = report_data.search(args.search or "")
        for cu, name, city, state, assets in matches[:40]:
            print("%8s  %-36s %-16s %2s  $%sM" % (cu, name, city, state, "{:,.0f}".format(assets / 1e6)))
        print("(%d of %d credit unions in the %s cycle; build one with --cu <number>)" % (
            len(matches), len(report_data.names), report_data.as_of))
        return 0
    curve = None
    if args.curve:
        with open(args.curve, encoding="utf-8") as handle:
            curve = {float(k): float(v) for k, v in json.load(handle).items()}
    prior = callreport.CallReport(args.prior) if args.prior else None
    year_ago = callreport.CallReport(args.year_ago) if args.year_ago else None
    rows, raw, ties = callreport.build(report_data, args.cu, curve, prior=prior, year_ago=year_ago)
    out = args.out or os.path.join("examples", "cu-%s" % args.cu)
    callreport.write(out, rows, raw, callreport.peers(report_data, args.cu))
    print("%s -> %s (%d positions)" % (report_data.name(args.cu), out, len(rows)))
    for key, (reported, built) in ties.items():
        print("  %-14s reported %16s   built %16s" % (key, "{:,.0f}".format(reported), "{:,.0f}".format(built)))
    print("Behaviour and terms are Keel's defaults; see the notes in %s." % os.path.join(out, "assumptions.json"))
    if args.run:
        return main(["run", out])
    return 0


def run_price(args):
    from keel import pricing
    positions, assumptions, _, _ = load(args.folder)
    pct = lambda v: None if v is None else v / 100.0  # noqa: E731
    deal = pricing.Deal(product=args.product, amount=args.amount, term_months=args.term, rate=pct(args.rate),
                        side=pricing.side_of(args.product, positions), amortization=args.amortization,
                        amort_months=args.amort_months, upfront_fee=args.fee / 100.0, cpr=pct(args.cpr),
                        runoff=pct(args.runoff), charge_off=pct(args.charge_off),
                        servicing_cost=pct(args.servicing_cost), fee_yield=pct(args.fee_yield),
                        origination_cost=pct(args.origination_cost), risk_weight=pct(args.risk_weight))
    print(pricing.text(pricing.quote(deal, assumptions)))
    return 0


def run_special(args):
    import json
    from keel import deposits, depositpricing, terms
    positions, assumptions, _, _ = load(args.folder)
    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)
    result = {"special": depositpricing.special(positions, assumptions, spec),
              "moves": depositpricing.rate_moves(positions, assumptions, deposits.study(args.folder, assumptions),
                                                 wholesale_spread=float(spec.get("wholesale_spread", 0.15)))}
    stem = os.path.splitext(os.path.basename(args.spec))[0]
    out = args.out or os.path.join(args.folder, "report", "special-%s.html" % stem)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(terms.translate(depositpricing.page(result, report.STYLE), assumptions))
    s = result["special"]
    print("special -> %s" % out)
    if s["new_money"] <= 0:
        print("%s: $%s raised, all from renewals of maturing certificates: no new money, and $%s a year more "
              "to keep money already here" % (s["name"], "{:,.0f}".format(s["volume"]),
                                               "{:,.0f}".format(s["incremental_cost"])))
        return 0
    print("%s: $%s raised, $%s new money; marginal cost %s against wholesale %.2f%%%s" % (
        s["name"], "{:,.0f}".format(s["volume"]), "{:,.0f}".format(s["new_money"]),
        "n/a" if s["marginal"] is None else "%.2f%%" % (100 * s["marginal"]), 100 * s["wholesale"],
        "" if s["breakeven_new_share"] is None else "; beats borrowing above %.0f%% new money" % (
            100 * s["breakeven_new_share"]) if s["breakeven_new_share"] <= 1 else "; no new-money share beats borrowing"))
    return 0


def run_swap(args):
    import json
    from keel import swap, terms
    positions, assumptions, _, _ = load(args.folder)
    with open(args.trade, encoding="utf-8") as handle:
        spec = json.load(handle)
    result = swap.analyse(positions, assumptions, spec)
    stem = os.path.splitext(os.path.basename(args.trade))[0]
    out = args.out or os.path.join(args.folder, "report", "swap-%s.html" % stem)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(terms.translate(swap.page(result, report.STYLE), assumptions))
    t, s = result["trade"], result["summary"]
    print("trade -> %s" % out)
    if t["sold"]:
        print("realized %s %s; yield pickup %s a year; earn-back %s (base), %s (+300), %s (-300)" % (
            "loss" if t["realized"] < 0 else "gain", "{:,.0f}".format(abs(t["realized"])),
            "{:,.0f}".format(s["pickup"]), *(swap._months(result["runs"][n]["earn_back"]) for n in swap.SCENARIOS)))
    else:
        print("bought %s from cash; pickup over the short rate %s a year; year-one NII change %s (base), %s (+300), "
              "%s (-300)" % ("{:,.0f}".format(t["spent"]), "{:,.0f}".format(s["pickup"]),
                             *("{:+,.0f}".format(result["runs"][n]["years"][0]["change"]) for n in swap.SCENARIOS)))
    return 0


def run_newproduct(args):
    import json
    from keel import newproduct, terms
    positions, assumptions, raw, _ = load(args.folder)
    with open(args.proposal, encoding="utf-8") as handle:
        proposal = json.load(handle)
    result = newproduct.analyse(positions, assumptions, raw, proposal)
    stem = os.path.splitext(os.path.basename(args.proposal))[0]
    out = args.out or os.path.join(args.folder, "report", "newproduct-%s.html" % stem)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(terms.translate(newproduct.page(result, report.STYLE), assumptions))
    e, q = result["quote"]["at"], result["quote"]
    print("new product -> %s" % out)
    if e["side"] == "asset":
        print("one deal at %.2f%%: spread %.2f%% over FTP, RAROC %s (hurdle %.0f%%); hurdle rate %s; break-even %s" % (
            100 * e["rate"], 100 * e["spread"], "n/a" if e["raroc"] is None else "%.1f%%" % (100 * e["raroc"]),
            100 * q["hurdle"], "n/a" if q["hurdle_rate"] is None else "%.2f%%" % (100 * q["hurdle_rate"]),
            "n/a" if q["breakeven_rate"] is None else "%.2f%%" % (100 * q["breakeven_rate"])))
    else:
        print("one account at %.2f%%: FTP credit %.2f%%, %.2f%% after costs; highest rate that covers costs %s" % (
            100 * e["rate"], 100 * e["ftp"], 100 * e["pre_tax"],
            "n/a" if q["breakeven_rate"] is None else "%.2f%%" % (100 * q["breakeven_rate"])))
    for y in result["path"]:
        print("  year %d: average balance %s, net %s" % (y["year"], "{:,.0f}".format(y["average"]),
                                                         "{:,.0f}".format(y["net"])))
    return 0


def run_query(args):
    import json
    from keel import query
    if args.spec:
        with open(args.spec, encoding="utf-8") as handle:
            spec = json.load(handle)
    else:
        spec = {"table": args.table, "by": [b.strip() for b in args.by.split(",") if b.strip()],
                "measures": args.measure or ["count", "sum balance"], "where": args.where or [],
                "sort": args.sort, "limit": args.limit}
    positions, assumptions, _, imported = load(args.folder)
    result = query.run(spec, query.Tables(positions, assumptions, args.folder, imported))
    if args.out:
        query.write(result, args.out)
        print("%d rows -> %s" % (len(result["rows"]), args.out))
        return 0
    rows = [result["columns"]] + [[query.fmt(v) for v in row] for row in result["rows"]]
    if result["total"]:
        rows.append([query.fmt(v) for v in result["total"]])
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    for n, row in enumerate(rows):
        print("  ".join(str(v).rjust(w) if n and i >= len(spec.get("by") or []) else str(v).ljust(w)
                        for i, (v, w) in enumerate(zip(row, widths))))
    print("(%s of %s rows of %s)" % ("{:,}".format(result["matched"]), "{:,}".format(result["of"]), result["table"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
