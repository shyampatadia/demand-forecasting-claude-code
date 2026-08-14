#!/usr/bin/env python3
"""Build the Stage 2 demand patterns dashboard.

Aggregates `train.csv` (joined to `store.csv`) into the demand summaries shown
in `outputs/02_demand_patterns.html`, rendering `patterns_template.html` with the
figures inlined as JSON.

This script is the source of truth for that dashboard. Edit the template or
this file and re-run it -- do not hand-edit the rendered HTML.

    python3 build_patterns.py

Stage 2 is descriptive only: it describes demand as it has been, and fits no
model. No third-party dependencies.
"""

import argparse
import collections
import csv
import json
import math
import os
from datetime import date

TEMPLATE = "patterns_template.html"
OUTPUT = "outputs/02_demand_patterns.html"
PLACEHOLDER = "/*__DATA__*/"

HIST_BINS = 44
HIST_MAX = 22000
TOP_N = 10
# Stores need a reasonable trading history before a volatility ranking is fair.
MIN_TRADING_DAYS = 300
# A day counts as chain-wide only if at least this share of the panel traded.
FULL_PANEL_SHARE = 0.5

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def profile(data_dir):
    train = read(os.path.join(data_dir, "train.csv"))
    stores = read(os.path.join(data_dir, "store.csv"))
    attrs = {r["Store"]: r for r in stores}

    out = {}

    # Coerce once; every aggregate below reads these fields.
    rows = [
        {
            "store": r["Store"],
            "date": r["Date"],
            "dow": int(r["DayOfWeek"]),
            "sales": float(r["Sales"]),
            "open": r["Open"] == "1",
            "promo": r["Promo"] == "1",
            "holiday": r["StateHoliday"],
            "school": r["SchoolHoliday"] == "1",
        }
        for r in train
    ]
    # "Trading" means the till actually rang: open, with non-zero takings.
    trading = [r for r in rows if r["open"] and r["sales"] > 0]

    n = len(rows)
    n_open = sum(1 for r in rows if r["open"])
    n_zero = sum(1 for r in rows if r["sales"] == 0)

    out["kpi"] = {
        "total_sales": sum(r["sales"] for r in rows),
        "stores": len({r["store"] for r in rows}),
        "rows": n,
        "trading_rows": len(trading),
        "promo_share": 100 * sum(1 for r in trading if r["promo"]) / len(trading),
        "closed_share": 100 * (n - n_open) / n,
        "zero_share": 100 * n_zero / n,
        "zero_open": sum(1 for r in rows if r["open"] and r["sales"] == 0),
        "zero_closed": sum(1 for r in rows if not r["open"] and r["sales"] == 0),
        "mean_basket": mean([r["sales"] for r in trading]),
        "days": len({r["date"] for r in rows}),
    }

    # ---- daily series -------------------------------------------------
    # Sales per *trading store*, not the raw daily total: 180 stores drop out
    # of the panel for 184 days, and a raw total would read that absence as a
    # demand collapse.
    by_date = collections.defaultdict(list)
    for r in trading:
        by_date[r["date"]].append(r["sales"])
    dates = sorted(by_date)
    per_store = [mean(by_date[d]) for d in dates]
    counts = [len(by_date[d]) for d in dates]

    roll = []
    for i in range(len(per_store)):
        lo = max(0, i - 6)
        roll.append(round(mean(per_store[lo : i + 1]), 1))

    # A per-store average taken over 17 trading stores is not comparable with one
    # taken over 1,115. Public holidays leave a handful of stores open and send
    # the raw average to the top of the chart, so the headline peak is drawn only
    # from days where a normal-sized panel actually traded.
    panel_max = max(counts)
    full = [i for i, c in enumerate(counts) if c >= FULL_PANEL_SHARE * panel_max]
    peak_i = max(full, key=lambda i: per_store[i])
    out["daily"] = {
        "start": dates[0],
        "end": dates[-1],
        "avg": [round(v, 1) for v in per_store],
        "roll7": roll,
        "open_stores": counts,
        "panel_max": panel_max,
        "sparse_days": len(counts) - len(full),
        "median": round(sorted(per_store)[len(per_store) // 2], 1),
        "peak_full": {
            "date": dates[peak_i],
            "avg": round(per_store[peak_i], 1),
            "stores": counts[peak_i],
        },
    }

    # ---- weekday ------------------------------------------------------
    wk = []
    for d in range(1, 8):
        day_rows = [r for r in rows if r["dow"] == d]
        day_trading = [r for r in day_rows if r["open"] and r["sales"] > 0]
        wk.append(
            {
                "dow": d,
                "name": WEEKDAYS[d - 1],
                "avg": round(mean([r["sales"] for r in day_trading]), 1),
                "open_rate": round(100 * len(day_trading) / len(day_rows), 1),
                "n": len(day_rows),
                "trading": len(day_trading),
            }
        )
    out["weekday"] = wk

    # ---- distribution -------------------------------------------------
    sales = sorted(r["sales"] for r in trading)
    bins = [0] * HIST_BINS
    for s in sales:
        bins[min(int(s / HIST_MAX * HIST_BINS), HIST_BINS - 1)] += 1
    q = lambda p: sales[int(p * (len(sales) - 1))]
    out["dist"] = {
        "bins": bins,
        "max": HIST_MAX,
        "median": q(0.5),
        "p10": q(0.10),
        "p90": q(0.90),
        "p99": q(0.99),
        "mean": round(mean(sales), 1),
        "min": sales[0],
        "hi": sales[-1],
    }

    # ---- open / closed ------------------------------------------------
    out["openclosed"] = {
        "open": n_open,
        "closed": n - n_open,
        "zero_total": n_zero,
        "zero_open": out["kpi"]["zero_open"],
        "zero_closed": out["kpi"]["zero_closed"],
        "closed_nonzero": sum(1 for r in rows if not r["open"] and r["sales"] > 0),
        "n": n,
    }
    # Closures are overwhelmingly a Sunday pattern -- worth showing explicitly.
    closed_by_dow = collections.Counter(r["dow"] for r in rows if not r["open"])
    out["closed_by_dow"] = [
        {"name": WEEKDAYS[d - 1], "n": closed_by_dow.get(d, 0)} for d in range(1, 8)
    ]

    # ---- promo / holiday / school -------------------------------------
    def split(pred):
        yes = [r["sales"] for r in trading if pred(r)]
        no = [r["sales"] for r in trading if not pred(r)]
        return {
            "yes_avg": round(mean(yes), 1),
            "no_avg": round(mean(no), 1),
            "yes_n": len(yes),
            "no_n": len(no),
            "uplift": round(100 * (mean(yes) / mean(no) - 1), 1) if no and yes else 0,
        }

    out["promo"] = split(lambda r: r["promo"])
    out["school"] = split(lambda r: r["school"])

    hol_names = {"0": "Normal day", "a": "Public holiday", "b": "Easter", "c": "Christmas"}
    hol = []
    for c in ["0", "a", "b", "c"]:
        all_rows = [r for r in rows if r["holiday"] == c]
        trd = [r for r in all_rows if r["open"] and r["sales"] > 0]
        if not all_rows:
            continue
        hol.append(
            {
                "code": c,
                "name": hol_names[c],
                "n": len(all_rows),
                "open_rate": round(100 * len(trd) / len(all_rows), 1),
                "avg": round(mean([r["sales"] for r in trd]), 1) if trd else 0,
            }
        )
    out["holiday"] = hol

    # ---- store type & assortment --------------------------------------
    type_names = {"a": "Type a", "b": "Type b", "c": "Type c", "d": "Type d"}
    # The source files never say what the assortment levels mean, so they keep
    # their raw codes rather than borrowing names from outside the data.
    asrt_names = {"a": "Assortment a", "b": "Assortment b", "c": "Assortment c"}

    def by_attr(field, names):
        buckets = collections.defaultdict(list)
        store_ids = collections.defaultdict(set)
        for r in trading:
            k = attrs[r["store"]][field]
            buckets[k].append(r["sales"])
            store_ids[k].add(r["store"])
        total = sum(sum(v) for v in buckets.values())
        return [
            {
                "key": k,
                "name": names.get(k, k),
                "avg": round(mean(buckets[k]), 1),
                "stores": len(store_ids[k]),
                "share": round(100 * sum(buckets[k]) / total, 1),
            }
            for k in sorted(buckets)
        ]

    out["storetype"] = by_attr("StoreType", type_names)
    out["assortment"] = by_attr("Assortment", asrt_names)

    # ---- per-store league tables --------------------------------------
    per_store_sales = collections.defaultdict(list)
    for r in trading:
        per_store_sales[r["store"]].append(r["sales"])

    stats = []
    for s, v in per_store_sales.items():
        m = mean(v)
        sd = stdev(v)
        stats.append(
            {
                "store": int(s),
                "avg": round(m, 1),
                "sd": round(sd, 1),
                "cv": round(100 * sd / m, 1) if m else 0,
                "days": len(v),
                "type": attrs[s]["StoreType"],
                "assort": attrs[s]["Assortment"],
            }
        )

    out["top_stores"] = sorted(stats, key=lambda x: -x["avg"])[:TOP_N]
    eligible = [x for x in stats if x["days"] >= MIN_TRADING_DAYS]
    out["volatile"] = sorted(eligible, key=lambda x: -x["cv"])[:TOP_N]
    out["steady"] = sorted(eligible, key=lambda x: x["cv"])[:3]
    out["chain"] = {
        "avg": round(mean([x["avg"] for x in stats]), 1),
        "cv": round(mean([x["cv"] for x in eligible]), 1),
        "n": len(stats),
        "min_days": MIN_TRADING_DAYS,
    }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=".")
    ap.add_argument("--template", default=TEMPLATE)
    ap.add_argument("--out", default=OUTPUT)
    ap.add_argument("--dump", action="store_true", help="print the figures and exit")
    args = ap.parse_args()

    stats = profile(args.data_dir)

    if args.dump:
        printable = {k: v for k, v in stats.items() if k != "daily"}
        print(json.dumps(printable, indent=1)[:4200])
        return

    template = open(args.template).read()
    if PLACEHOLDER not in template:
        raise SystemExit(f"{args.template} has no {PLACEHOLDER} placeholder")
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    payload = json.dumps(stats, separators=(",", ":"))
    open(args.out, "w").write(template.replace(PLACEHOLDER, payload))

    k = stats["kpi"]
    print(f"{args.out}: {os.path.getsize(args.out):,} bytes  ({len(payload):,} bytes of data)")
    print(f"  {k['rows']:,} rows, {k['trading_rows']:,} trading days, {k['stores']:,} stores")
    print(f"  total sales {k['total_sales']:,.0f}, mean trading day {k['mean_basket']:,.0f}")
    print(f"  promo share {k['promo_share']:.1f}%, closed {k['closed_share']:.1f}%, zero {k['zero_share']:.1f}%")


if __name__ == "__main__":
    main()
