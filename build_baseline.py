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

    out["open_inference"] = infer_open(train, test)
    out["bl"] = run_baselines(train, test, cutoff_s)
    return out


ROLLING_WINDOWS = [
    ("2015-04-27", "2015-06-13"),
    ("2015-03-10", "2015-04-26"),
    ("2015-01-21", "2015-03-09"),
]
# Same calendar dates as the final holdout, two years earlier. Reported apart
# from the rolling origins because it sits on far less preceding history.
SEASONAL_WINDOW = ("2013-06-14", "2013-07-31")


def infer_open(train, test):
    """Resolve blank `Open` values in the forecast window, never silently.

    Primary rule: the store opened on a majority of that weekday in history.
    Fallback 1: no store-weekday history -> the chain's modal Open for that
    weekday. Fallback 2: store absent from train -> the same chain-wide modal.

    Returns one record per blank row so every inferred value can be published.
    """
    store_wd = collections.defaultdict(lambda: [0, 0])   # (open, total)
    chain_wd = collections.defaultdict(lambda: [0, 0])
    for r in train:
        is_open = 1 if r["Open"] == "1" else 0
        for bucket in (store_wd[(r["Store"], r["DayOfWeek"])], chain_wd[r["DayOfWeek"]]):
            bucket[0] += is_open
            bucket[1] += 1

    out = []
    for r in test:
        if r["Open"].strip() != "":
            continue
        key = (r["Store"], r["DayOfWeek"])
        if store_wd.get(key, [0, 0])[1] > 0:
            op, tot = store_wd[key]
            basis = "store weekday history"
        elif chain_wd.get(r["DayOfWeek"], [0, 0])[1] > 0:
            op, tot = chain_wd[r["DayOfWeek"]]
            basis = "fallback 1: chain weekday history"
        else:
            op, tot, basis = 0, 1, "fallback 2: no history, assumed closed"
        rate = 100 * op / tot
        out.append({
            "id": r["Id"], "store": int(r["Store"]), "date": r["Date"],
            "dow": int(r["DayOfWeek"]), "open_days": op, "total_days": tot,
            "rate": round(rate, 1), "inferred": 1 if rate >= 50 else 0, "basis": basis,
        })
    return out


def _fit_all(fit_rows):
    """Fit every candidate rule on one slice of history."""
    return {r.key: r.fit(fit_rows) for r in
            (bl.LastWeekday(), bl.RecentAverage(), bl.StoreWeekdayMean())}


def _window(panel, start, end, scope):
    """Fit before `start`, score the window on the primary population."""
    fit_rows = [r for r in panel if r["date"] < start]
    win_rows = [r for r in panel if start <= r["date"] <= end]
    fitted = _fit_all(fit_rows)
    rows = []
    for key, rule in fitted.items():
        s = bl.score(rule, win_rows, stores=scope)
        rows.append({"key": key, "name": s["name"], "mae": round(s["mae"], 1),
                     "wape": round(s["wape"], 2), "rmse": round(s["rmse"], 1), "n": s["n"]})
    rows.sort(key=lambda r: r["mae"])
    return {
        "start": start, "end": end, "fit_end": max(r["date"] for r in fit_rows),
        "fit_days": len({r["date"] for r in fit_rows}),
        "results": rows, "best": rows[0]["key"],
    }


def _summarise(results):
    return [{"key": r["key"], "name": r["name"], "description": r["description"],
             "mae": round(r["mae"], 1), "wape": round(r["wape"], 2),
             "rmse": round(r["rmse"], 1), "n": r["n"]} for r in results]


def run_baselines(train, test, cutoff):
    """Score the naive rules on the approved holdout and the rolling origins.

    The primary population is the stores that appear in the live forecast
    window; scoring the rest would measure a task nobody has to perform. All
    stores are still reported, as a secondary population diagnostic.
    """
    panel = bl.prepare(train)
    scope = {r["Store"] for r in test}
    fit_rows = [r for r in panel if r["date"] < cutoff]
    hold_rows = [r for r in panel if r["date"] >= cutoff]

    in_scope_open = [r for r in hold_rows if r["open"] and r["store"] in scope]
    hold_mean = sum(r["sales"] for r in in_scope_open) / len(in_scope_open)

    fitted = _fit_all(fit_rows)

    primary, allday, allstores = [], [], []
    for key, rule in fitted.items():
        primary.append(bl.score(rule, hold_rows, stores=scope))
        allday.append(bl.score(rule, hold_rows, stores=scope, score_closed=True))
        allstores.append(bl.score(rule, hold_rows))
    primary.sort(key=lambda r: r["mae"])
    best_key = primary[0]["key"]
    best_rule = fitted[best_key]

    order = {r["key"]: i for i, r in enumerate(primary)}
    allday.sort(key=lambda r: order[r["key"]])
    allstores.sort(key=lambda r: order[r["key"]])

    # Isolated: reads actuals from inside the window, so it is not a candidate.
    leak = bl.score(bl.LeakyLastWeekday().fit(fit_rows).observe(hold_rows),
                    hold_rows, stores=scope)

    # Store-level MAE spread, on the primary population.
    store_avg = collections.defaultdict(list)
    for r in in_scope_open:
        store_avg[r["store"]].append(r["sales"])
    store_avg = {s: sum(v) / len(v) for s, v in store_avg.items()}
    per_store = primary[0]["per_store_mae"]
    pairs = sorted(((store_avg[s], per_store[s], int(s)) for s in per_store), key=lambda t: t[0])
    maes = sorted(m for _, m, _ in pairs)
    q = lambda p: maes[int(p * (len(maes) - 1))]
    highest = max(pairs, key=lambda t: t[1])
    lowest = min(pairs, key=lambda t: t[1])

    by_dow = []
    for d in range(1, 8):
        rows_d = [r for r in in_scope_open if r["dow"] == d]
        if not rows_d:
            by_dow.append({"name": WEEKDAYS[d - 1], "mae": 0, "n": 0})
            continue
        e = [abs(r["sales"] - best_rule.predict_one(r)) for r in rows_d]
        by_dow.append({"name": WEEKDAYS[d - 1], "mae": round(sum(e) / len(e), 1), "n": len(rows_d)})

    # Worked example: the in-scope store closest to the median store average.
    median_avg = sorted(store_avg.values())[len(store_avg) // 2]
    example_store = min(store_avg, key=lambda s: abs(store_avg[s] - median_avg))
    ex_rows = sorted((r for r in hold_rows if r["store"] == example_store), key=lambda r: r["date"])
    example = {
        "store": int(example_store), "avg": round(store_avg[example_store], 1),
        "dates": [r["date"] for r in ex_rows],
        "actual": [r["sales"] for r in ex_rows],
        "open": [1 if r["open"] else 0 for r in ex_rows],
        "preds": {k: [round(0.0 if not r["open"] else fitted[k].predict_one(r), 1) for r in ex_rows]
                  for k in fitted},
        "mae": {k: round(sum(abs(r["sales"] - fitted[k].predict_one(r)) for r in ex_rows if r["open"])
                         / sum(1 for r in ex_rows if r["open"]), 1) for k in fitted},
    }

    rolling = [_window(panel, a, b, scope) for a, b in ROLLING_WINDOWS]
    seasonal = _window(panel, SEASONAL_WINDOW[0], SEASONAL_WINDOW[1], scope)

    return {
        "scope": {
            "primary_stores": len(scope), "all_stores": len({r["store"] for r in panel}),
            "primary_rows": primary[0]["n"], "all_rows": allstores[0]["n"],
            "dropped_rows": allstores[0]["n"] - primary[0]["n"],
        },
        "primary": _summarise(primary),
        "allday": _summarise(allday),
        "allstores": _summarise(allstores),
        "best": best_key,
        "hold_mean": round(hold_mean, 1),
        "leak": {"name": leak["name"], "mae": round(leak["mae"], 1),
                 "wape": round(leak["wape"], 2)},
        "store_dist": {
            "median": round(q(0.5)), "p10": round(q(0.10)), "p90": round(q(0.90)),
            "highest_mae": {"store": highest[2], "mae": round(highest[1]), "avg": round(highest[0])},
            "lowest_mae": {"store": lowest[2], "mae": round(lowest[1]), "avg": round(lowest[0])},
            "n": len(pairs),
            "points": [[round(a), round(m)] for a, m, _ in pairs],
        },
        "by_dow": by_dow,
        "example": example,
        "rolling": rolling,
        "seasonal": seasonal,
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
    b = stats["bl"]
    print(f"  primary population {b['scope']['primary_stores']} stores, "
          f"{b['scope']['primary_rows']:,} open rows "
          f"({b['scope']['dropped_rows']:,} out-of-scope rows excluded)")
    for r in b["primary"]:
        print(f"    {r['name']:<28} MAE {r['mae']:>8,.1f}  WAPE {r['wape']:>5.2f}%  RMSE {r['rmse']:>8,.1f}")
    print(f"  benchmark: {b['best']}")
    for w in b["rolling"]:
        top = w["results"][0]
        print(f"    rolling {w['start']}→{w['end']}  best={w['best']} MAE {top['mae']:,.1f}")
    sw = b["seasonal"]
    print(f"    seasonal {sw['start']}→{sw['end']}  best={sw['best']} "
          f"MAE {sw['results'][0]['mae']:,.1f}  (fit history {sw['fit_days']} days)")
    print(f"  fit    {stats['fit']['min']} to {stats['fit']['max']}  {stats['fit']['rows']:,} rows")
    print(f"  holdout{stats['hold']['min']} to {stats['hold']['max']}  {stats['hold']['rows']:,} rows"
          f"  ({stats['hold']['scored']:,} scored)")


if __name__ == "__main__":
    main()
