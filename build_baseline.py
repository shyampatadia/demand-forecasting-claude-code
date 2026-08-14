#!/usr/bin/env python3
"""Build the Stage 3 baseline dashboard.

At this stage the page carries the **validation design** only: the split
timeline, why a random split is invalid here, the metric choice, the rule for
closed and zero-sales days, and the exclusion of `Customers`. No model is
fitted and no forecast is produced.

Renders `baseline_template.html` into `03_baseline_dashboard.html`.

    python3 build_baseline.py

The holdout length is derived from the real forecast horizon in `test.csv`
rather than hardcoded, so the design stays tied to the data. No third-party
dependencies.
"""

import argparse
import collections
import csv
import json
import os
from datetime import date, timedelta

TEMPLATE = "baseline_template.html"
OUTPUT = "03_baseline_dashboard.html"
PLACEHOLDER = "/*__DATA__*/"


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def window_stats(rows):
    n = len(rows)
    open_rows = [r for r in rows if r["Open"] == "1"]
    return {
        "rows": n,
        "days": len({r["Date"] for r in rows}),
        "stores": len({r["Store"] for r in rows}),
        "open": len(open_rows),
        "closed": n - len(open_rows),
        "zero": sum(1 for r in rows if float(r["Sales"]) == 0),
        "open_zero": sum(1 for r in open_rows if float(r["Sales"]) == 0),
        "scored": sum(1 for r in open_rows if float(r["Sales"]) > 0),
    }


def profile(data_dir):
    train = read(os.path.join(data_dir, "train.csv"))
    test = read(os.path.join(data_dir, "test.csv"))

    train_dates = sorted({r["Date"] for r in train})
    test_dates = sorted({r["Date"] for r in test})

    # The holdout must be the same length as the real forecast horizon, so it is
    # measured off test.csv rather than picked.
    horizon = len(test_dates)
    cutoff = date.fromisoformat(train_dates[-1]) - timedelta(days=horizon - 1)
    cutoff_s = cutoff.isoformat()

    fit_rows = [r for r in train if r["Date"] < cutoff_s]
    hold_rows = [r for r in train if r["Date"] >= cutoff_s]

    out = {
        "horizon": horizon,
        "train": {"min": train_dates[0], "max": train_dates[-1], "days": len(train_dates)},
        "test": {"min": test_dates[0], "max": test_dates[-1], "days": horizon,
                 "rows": len(test), "stores": len({r["Store"] for r in test})},
        "cutoff": cutoff_s,
        "fit": dict(window_stats(fit_rows),
                    min=min(r["Date"] for r in fit_rows), max=max(r["Date"] for r in fit_rows)),
        "hold": dict(window_stats(hold_rows),
                     min=min(r["Date"] for r in hold_rows), max=max(r["Date"] for r in hold_rows)),
    }

    # Sales spread inside the proposed holdout -- this is what a metric has to
    # cope with, and it is why a percentage-based metric is fragile here.
    hold_sales = sorted(float(r["Sales"]) for r in hold_rows
                        if r["Open"] == "1" and float(r["Sales"]) > 0)
    q = lambda p: hold_sales[int(p * (len(hold_sales) - 1))]
    out["hold_sales"] = {
        "n": len(hold_sales), "min": hold_sales[0], "p10": q(0.10), "median": q(0.5),
        "p90": q(0.90), "max": hold_sales[-1],
        "mean": round(sum(hold_sales) / len(hold_sales), 1),
    }

    # test.Open blanks land inside the forecast window and need a stated rule.
    out["test_open_blank"] = sum(1 for r in test if r["Open"].strip() == "")

    # Whole-train counts, for the closed-day rule panel.
    out["all"] = window_stats(train)
    out["all"]["closed_by_sunday"] = sum(
        1 for r in train if r["Open"] == "0" and int(r["DayOfWeek"]) == 7
    )
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=".")
    ap.add_argument("--template", default=TEMPLATE)
    ap.add_argument("--out", default=OUTPUT)
    args = ap.parse_args()

    stats = profile(args.data_dir)

    template = open(args.template).read()
    if PLACEHOLDER not in template:
        raise SystemExit(f"{args.template} has no {PLACEHOLDER} placeholder")
    payload = json.dumps(stats, separators=(",", ":"))
    open(args.out, "w").write(template.replace(PLACEHOLDER, payload))

    print(f"{args.out}: {os.path.getsize(args.out):,} bytes")
    print(f"  horizon {stats['horizon']} days (from test.csv)")
    print(f"  proposed cutoff {stats['cutoff']}  -- NOT selected, awaiting sign-off")
    print(f"  fit    {stats['fit']['min']} to {stats['fit']['max']}  {stats['fit']['rows']:,} rows")
    print(f"  holdout{stats['hold']['min']} to {stats['hold']['max']}  {stats['hold']['rows']:,} rows"
          f"  ({stats['hold']['scored']:,} scored under the proposed rule)")


if __name__ == "__main__":
    main()
