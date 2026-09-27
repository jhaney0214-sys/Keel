"""python -m keel run <folder with positions.csv and assumptions.json> [--out DIR] [--name NAME]"""

import argparse
import os
import sys

from keel import model, report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="keel", description="ALM, plan and liquidity from one projection.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="project, measure, reconcile and write the report")
    run.add_argument("folder")
    run.add_argument("--out", default=None, help="report folder (default: <folder>/report)")
    run.add_argument("--name", default="Credit union")
    args = parser.parse_args(argv)

    try:
        positions = model.read_positions(os.path.join(args.folder, "positions.csv"))
        assumptions = model.read_assumptions(os.path.join(args.folder, "assumptions.json"))
        model.check(positions, assumptions)
    except model.InputError as error:
        print("input error: %s" % error, file=sys.stderr)
        return 2
    out = args.out or os.path.join(args.folder, "report")
    result = report.build(positions, assumptions, out, args.name)
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
