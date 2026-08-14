#!/usr/bin/env python3
"""Build the baseline dashboard.

Fits the naive baselines in `baselines.py` on history before the approved
cutoff, scores them on the holdout, and renders both those results and the
validation design they were measured under: the split timeline, why a random
split is invalid here, the metric, the closed-day rule, and the exclusion of
`Customers`. No model is fitted -- these are naive rules, and they exist to
give any later model a number it has to beat.

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

import baselines as bl

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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

    # Sales spread inside the holdout -- this is what a metric has to
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

    out["bl"] = run_baselines(train, cutoff_s)
    return out


def run_baselines(train, cutoff):
    """Fit the naive baselines on history before the cutoff and score them."""
    panel = bl.prepare(train)
    fit_rows = [r for r in panel if r["date"] < cutoff]
    hold_rows = [r for r in panel if r["date"] >= cutoff]

    scored = [r for r in hold_rows if r["open"]]
    hold_mean = sum(r["sales"] for r in scored) / len(scored)

    rules = [bl.LastWeekday(), bl.RecentAverage(), bl.StoreWeekdayMean()]
    results = []
    fitted = {}
    for rule in rules:
        rule.fit(fit_rows)
        fitted[rule.key] = rule
        s = bl.score(rule, hold_rows)
        results.append({
            "key": s["key"], "name": s["name"], "description": s["description"],
            "mae": round(s["mae"], 1), "rmse": round(s["rmse"], 1), "n": s["n"],
            "mae_pct": round(100 * s["mae"] / hold_mean, 1),
            "_per_store": s["per_store_mae"],
        })

    best = min(results, key=lambda r: r["mae"])
    best_rule = fitted[best["key"]]

    # The tempting leaky variant, measured rather than assumed.
    leak = bl.score(bl.LeakyLastWeekday().fit(fit_rows).observe(hold_rows), hold_rows)

    # What scoring the closed days would do to the headline number.
    with_closed = bl.score(best_rule, hold_rows, score_closed=True)

    # Per-store view for the best rule: does error track store size?
    store_avg = collections.defaultdict(list)
    for r in scored:
        store_avg[r["store"]].append(r["sales"])
    store_avg = {s: sum(v) / len(v) for s, v in store_avg.items()}
    per_store = best["_per_store"]
    pairs = sorted(((store_avg[s], per_store[s], int(s)) for s in per_store), key=lambda t: t[0])
    maes = sorted(m for _, m, _ in pairs)
    q = lambda p: maes[int(p * (len(maes) - 1))]
    worst = max(pairs, key=lambda t: t[1])
    sharpest = min(pairs, key=lambda t: t[1])

    # Where the error sits across the week.
    by_dow = []
    for d in range(1, 8):
        rows_d = [r for r in scored if r["dow"] == d]
        if not rows_d:
            by_dow.append({"name": WEEKDAYS[d - 1], "mae": 0, "n": 0})
            continue
        e = [abs(r["sales"] - best_rule.predict_one(r)) for r in rows_d]
        by_dow.append({"name": WEEKDAYS[d - 1], "mae": round(sum(e) / len(e), 1), "n": len(rows_d)})

    # A representative store for the worked example: closest to the median of
    # store averages, so the chart shows a typical site rather than a giant.
    median_avg = sorted(store_avg.values())[len(store_avg) // 2]
    example_store = min(store_avg, key=lambda s: abs(store_avg[s] - median_avg))
    ex_rows = sorted((r for r in hold_rows if r["store"] == example_store), key=lambda r: r["date"])
    example = {
        "store": int(example_store),
        "avg": round(store_avg[example_store], 1),
        "dates": [r["date"] for r in ex_rows],
        "actual": [r["sales"] for r in ex_rows],
        "open": [1 if r["open"] else 0 for r in ex_rows],
        "preds": {
            k: [round(0.0 if not r["open"] else fitted[k].predict_one(r), 1) for r in ex_rows]
            for k in fitted
        },
        "mae": {
            k: round(sum(abs(r["sales"] - fitted[k].predict_one(r)) for r in ex_rows if r["open"])
                     / sum(1 for r in ex_rows if r["open"]), 1)
            for k in fitted
        },
    }

    for r in results:
        del r["_per_store"]

    return {
        "results": results,
        "best": best["key"],
        "hold_mean": round(hold_mean, 1),
        "leak": {"name": leak["name"], "mae": round(leak["mae"], 1)},
        "closed_effect": {
            "mae_open": best["mae"],
            "mae_all": round(with_closed["mae"], 1),
            "n_open": best["n"],
            "n_all": with_closed["n"],
        },
        "by_store": [[round(a), round(m)] for a, m, _ in pairs],
        "store_summary": {
            "median": round(q(0.5)), "p10": round(q(0.10)), "p90": round(q(0.90)),
            "worst": {"store": worst[2], "mae": round(worst[1]), "avg": round(worst[0])},
            "sharpest": {"store": sharpest[2], "mae": round(sharpest[1]), "avg": round(sharpest[0])},
            "n": len(pairs),
        },
        "by_dow": by_dow,
        "example": example,
    }


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
    print(f"  approved cutoff {stats['cutoff']}")
    print(f"  fit    {stats['fit']['min']} to {stats['fit']['max']}  {stats['fit']['rows']:,} rows")
    print(f"  holdout{stats['hold']['min']} to {stats['hold']['max']}  {stats['hold']['rows']:,} rows"
          f"  ({stats['hold']['scored']:,} scored)")


if __name__ == "__main__":
    main()
