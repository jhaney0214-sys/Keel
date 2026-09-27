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
    args = parser.parse_args(argv)

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


if __name__ == "__main__":
    sys.exit(main())
