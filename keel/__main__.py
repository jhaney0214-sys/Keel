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
    what = sub.add_parser("whatif", help="run a what-if against the base and compare")
    what.add_argument("folder")
    what.add_argument("spec", help="a what-if JSON file")
    what.add_argument("--out", default=None, help="default: <folder>/report/whatif-<file name>")
    args = parser.parse_args(argv)
    if args.command == "whatif":
        return run_whatif(args)

    out = args.out or os.path.join(args.folder, "report")
    imported = None
    try:
        assumptions = model.read_assumptions(os.path.join(args.folder, "assumptions.json"))
        if os.path.isdir(os.path.join(args.folder, "data")):
            # Core-system files: import, pool and tie them to the GL first.
            imported = importer.import_folder(args.folder, datetime.date.fromisoformat(assumptions.as_of))
            positions = imported.positions
            os.makedirs(out, exist_ok=True)
            importer.write_positions(positions, os.path.join(out, "positions_imported.csv"))
        else:
            positions = model.read_positions(os.path.join(args.folder, "positions.csv"))
        model.check(positions, assumptions)
    except model.InputError as error:
        print("input error: %s" % error, file=sys.stderr)
        return 2
    name = args.name or assumptions.notes.get("about", "Credit union").split(".")[0]
    result = report.build(positions, assumptions, out, name, imported)
    failed = [c for c in result["checks"] if not c.passed]
    print("report -> %s" % os.path.join(out, "report.html"))
    print("year-one NII %s; NEV ratio after +300bp %.2f%% (%s); reconciliation %d of %d passed" % (
        "{:,.0f}".format(result["nii_year1"]), 100 * result["test"]["post_shock_ratio"],
        result["test"]["ratio_rating"], len(result["checks"]) - len(failed), len(result["checks"])))
    for c in failed:
        print("FAILED: %s (%s)" % (c.name, c.detail), file=sys.stderr)
    return 1 if failed else 0


def load(folder, out=None):
    """(positions, assumptions, raw assumptions JSON, imported or None)."""
    import json
    with open(os.path.join(folder, "assumptions.json"), encoding="utf-8") as handle:
        raw = json.load(handle)
    assumptions = model.parse_assumptions(raw)
    imported = None
    if os.path.isdir(os.path.join(folder, "data")):
        imported = importer.import_folder(folder, datetime.date.fromisoformat(assumptions.as_of))
        positions = imported.positions
    else:
        positions = model.read_positions(os.path.join(folder, "positions.csv"))
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


if __name__ == "__main__":
    sys.exit(main())
