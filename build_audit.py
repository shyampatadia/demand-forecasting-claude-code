#!/usr/bin/env python3
"""Build the Stage 1 data audit dashboard.

Profiles the raw CSVs and renders `audit_template.html` into
`outputs/01_data_audit.html`, replacing the `/*__DATA__*/` placeholder with the
profiled statistics as inline JSON.

This script is the source of truth for the dashboard. Edit the template or
this file and re-run it -- do not hand-edit the rendered HTML.

    python3 build_audit.py

No third-party dependencies; the standard library is enough.
"""

import argparse
import collections
import csv
import json
import math
import os
from datetime import date, timedelta

TEMPLATE = "audit_template.html"
OUTPUT = "outputs/01_data_audit.html"
PLACEHOLDER = "/*__DATA__*/"

# Sales histogram: 40 bins spanning 0..HIST_MAX, with the top bin catching
# everything above.
HIST_BINS = 40
HIST_MAX = 25000


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def blanks(rows, name):
    """Blank count per column, preserving column order."""
    return [
        {"file": name, "col": c, "blank": sum(1 for r in rows if r[c].strip() == ""), "n": len(rows)}
        for c in rows[0]
    ]


def key_check(rows, keys):
    counts = collections.Counter(tuple(r[k] for k in keys) for r in rows)
    dupes = sum(1 for v in counts.values() if v > 1)
    return {"keys": " + ".join(keys), "dupe_keys": dupes, "unique": len(counts), "rows": len(rows)}


def profile(data_dir):
    tr = read(os.path.join(data_dir, "train.csv"))
    te = read(os.path.join(data_dir, "test.csv"))
    st = read(os.path.join(data_dir, "store.csv"))

    out = {}

    out["files"] = [
        {"name": n, "rows": len(rows), "cols": len(rows[0]),
         "bytes": os.path.getsize(os.path.join(data_dir, n)), "role": role}
        for n, rows, role in (
            ("train.csv", tr, "history"),
            ("test.csv", te, "forecast window"),
            ("store.csv", st, "store attributes"),
        )
    ]

    # Column presence across train and test, in first-seen order.
    train_cols, test_cols = list(tr[0]), list(te[0])
    ordered = []
    for c in train_cols + test_cols:
        if c not in ordered:
            ordered.append(c)
    out["colcompare"] = [{"col": c, "train": c in train_cols, "test": c in test_cols} for c in ordered]
    out["storecols"] = list(st[0])

    out["missing"] = blanks(tr, "train.csv") + blanks(te, "test.csv") + blanks(st, "store.csv")

    out["grain"] = {
        "train": key_check(tr, ["Store", "Date"]),
        "test": key_check(te, ["Store", "Date"]),
        "test_id": key_check(te, ["Id"]),
        "store": key_check(st, ["Store"]),
    }

    train_dates = sorted({r["Date"] for r in tr})
    test_dates = sorted({r["Date"] for r in te})
    out["dates"] = {
        "train_min": train_dates[0], "train_max": train_dates[-1], "train_days": len(train_dates),
        "test_min": test_dates[0], "test_max": test_dates[-1], "test_days": len(test_dates),
        "gap_days": (date.fromisoformat(test_dates[0]) - date.fromisoformat(train_dates[-1])).days - 1,
    }

    train_stores = {r["Store"] for r in tr}
    test_stores = {r["Store"] for r in te}
    out["stores"] = {
        "train": len(train_stores), "test": len(test_stores),
        "both": len(train_stores & test_stores),
        "train_only": len(train_stores - test_stores),
        "test_only": len(test_stores - train_stores),
        "store_csv": len(st),
    }

    # Stores reporting per day, for the coverage timeline.
    per_date = collections.Counter(r["Date"] for r in tr)
    out["cov"] = {"start": train_dates[0], "n": [per_date[d] for d in train_dates]}

    # Panel completeness: which stores are short of the full calendar.
    per_store = collections.Counter(r["Store"] for r in tr)
    full = len(train_dates)
    short = {s: c for s, c in per_store.items() if c < full}
    out["panel"] = {
        "expected_days": full,
        "complete_stores": len(train_stores) - len(short),
        "short_stores": len(short),
        "counts": collections.Counter(short.values()).most_common(5),
    }

    open_rows = sum(1 for r in tr if r["Open"] == "1")
    out["openzero"] = {
        "open": open_rows,
        "closed": len(tr) - open_rows,
        "zero_sales": sum(1 for r in tr if float(r["Sales"]) == 0),
        "open_zero": sum(1 for r in tr if r["Open"] == "1" and float(r["Sales"]) == 0),
        "closed_zero": sum(1 for r in tr if r["Open"] == "0" and float(r["Sales"]) == 0),
        "closed_nonzero": sum(1 for r in tr if r["Open"] == "0" and float(r["Sales"]) > 0),
        "n": len(tr),
    }

    # Target distribution, over trading days only.
    traded = [(float(r["Sales"]), float(r["Customers"]))
              for r in tr if r["Open"] == "1" and float(r["Sales"]) > 0]
    sales = sorted(s for s, _ in traded)
    q = lambda p: sales[int(p * (len(sales) - 1))]
    out["sales"] = {
        "n": len(sales), "min": sales[0], "p25": q(0.25), "median": q(0.5),
        "p75": q(0.75), "p95": q(0.95), "max": sales[-1],
        "mean": round(sum(sales) / len(sales), 1),
    }

    bins = [0] * HIST_BINS
    for s in sales:
        bins[min(int(s / HIST_MAX * HIST_BINS), HIST_BINS - 1)] += 1
    out["sales_hist"] = {"bins": bins, "max": HIST_MAX}

    # Sales/Customers correlation -- the leakage exhibit.
    mean_s = sum(s for s, _ in traded) / len(traded)
    mean_c = sum(c for _, c in traded) / len(traded)
    cov = sum((s - mean_s) * (c - mean_c) for s, c in traded)
    var_s = sum((s - mean_s) ** 2 for s, _ in traded)
    var_c = sum((c - mean_c) ** 2 for _, c in traded)
    out["leak"] = {
        "corr": round(cov / math.sqrt(var_s * var_c), 4),
        "cust_mean": round(mean_c, 1),
        "n": len(traded),
    }

    out["holiday"] = {
        "train": dict(collections.Counter(r["StateHoliday"] for r in tr)),
        "test": dict(collections.Counter(r["StateHoliday"] for r in te)),
    }
    out["promo"] = {
        "train": dict(collections.Counter(r["Promo"] for r in tr)),
        "test": dict(collections.Counter(r["Promo"] for r in te)),
    }
    out["store_attrs"] = {
        "StoreType": dict(collections.Counter(r["StoreType"] for r in st)),
        "Assortment": dict(collections.Counter(r["Assortment"] for r in st)),
        "promo2_zero": sum(1 for r in st if r["Promo2"] == "0"),
        "promo2_zero_blank_interval": sum(
            1 for r in st if r["Promo2"] == "0" and r["PromoInterval"].strip() == ""
        ),
    }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=".", help="directory holding the three CSVs")
    ap.add_argument("--template", default=TEMPLATE)
    ap.add_argument("--out", default=OUTPUT)
    args = ap.parse_args()

    stats = profile(args.data_dir)

    template = open(args.template).read()
    if PLACEHOLDER not in template:
        raise SystemExit(f"{args.template} has no {PLACEHOLDER} placeholder")
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    payload = json.dumps(stats, separators=(",", ":"))
    open(args.out, "w").write(template.replace(PLACEHOLDER, payload))

    print(f"{args.out}: {os.path.getsize(args.out):,} bytes  ({len(payload):,} bytes of data)")
    print(f"  train {stats['files'][0]['rows']:,} rows  "
          f"{stats['dates']['train_min']} to {stats['dates']['train_max']}")
    print(f"  test  {stats['files'][1]['rows']:,} rows  "
          f"{stats['dates']['test_min']} to {stats['dates']['test_max']}  "
          f"({stats['stores']['test']} stores x {stats['dates']['test_days']} days)")
    print(f"  target Sales: {stats['sales']['n']:,} trading rows, median {stats['sales']['median']:,.0f}")


if __name__ == "__main__":
    main()
