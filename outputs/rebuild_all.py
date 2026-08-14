#!/usr/bin/env python3
"""Rebuild every dashboard in this folder from the raw CSVs.

Runs the four stage build scripts in order and reports what each produced.
Works from any working directory: paths are resolved relative to this file, so

    python3 outputs/rebuild_all.py
    python3 rebuild_all.py          # from inside outputs/

both do the same thing.

The stage scripts live in the repository root alongside the templates they
render; this is a runner, not a copy of their logic. Edit a template or a build
script and re-run this -- never hand-edit the rendered HTML.

    --data-dir DIR   where train.csv, test.csv and store.csv live
                     (default: the repository root)
    --list           show the stages and exit without building

Exit code is non-zero if any stage fails. No third-party dependencies.
"""

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# (build script, dashboard it produces, one-line description)
STAGES = [
    ("build_audit.py", "01_data_audit.html",
     "Data audit -- grain, coverage, missing values, target, leakage"),
    ("build_patterns.py", "02_demand_patterns.html",
     "Demand patterns -- when demand arrives and what moves with it"),
    ("build_baseline.py", "03_baseline_dashboard.html",
     "Validation design and naive baselines -- the benchmark to beat"),
    ("build_forecast.py", "04_forecast_review.html",
     "Forecast review -- the model, scored against that benchmark"),
]

REQUIRED_CSVS = ("train.csv", "test.csv", "store.csv")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=ROOT,
                    help="directory holding train.csv, test.csv and store.csv")
    ap.add_argument("--list", action="store_true", help="list the stages and exit")
    args = ap.parse_args()

    if args.list:
        for script, out, desc in STAGES:
            print(f"{script:<20} -> outputs/{out:<28} {desc}")
        return 0

    data_dir = os.path.abspath(args.data_dir)
    missing = [c for c in REQUIRED_CSVS if not os.path.exists(os.path.join(data_dir, c))]
    if missing:
        print(f"error: {', '.join(missing)} not found in {data_dir}", file=sys.stderr)
        print("       pass --data-dir to point at the raw CSVs", file=sys.stderr)
        return 1

    print(f"data:    {data_dir}")
    print(f"outputs: {HERE}\n")

    failed = []
    started = time.time()
    for script, out, desc in STAGES:
        print(f"[{script}] {desc}")
        t0 = time.time()
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, script),
             "--data-dir", data_dir,
             "--out", os.path.join(HERE, out)],
            cwd=ROOT, capture_output=True, text=True,
        )
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")
        if result.returncode != 0:
            failed.append(script)
            print(f"  FAILED ({result.returncode})")
            for line in result.stderr.strip().splitlines()[-12:]:
                print(f"  ! {line}", file=sys.stderr)
        else:
            print(f"  done in {time.time() - t0:.1f}s")
        print()

    total = time.time() - started
    if failed:
        print(f"{len(failed)} of {len(STAGES)} stages failed: {', '.join(failed)}")
        return 1
    print(f"all {len(STAGES)} dashboards rebuilt in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
