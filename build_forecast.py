#!/usr/bin/env python3
"""Build the Stage 5 forecast review dashboard.

Fits the multiplicative forecast in `forecast.py` on history before the
approved cutoff, scores it against the Stage 4 benchmark on the same holdout
with the same code path, and renders `forecast_template.html` into
`outputs/04_forecast_review.html`.

    python3 build_forecast.py

Scoring rules are inherited from Stage 4 and not re-litigated here: primary
population is the stores present in `test.csv`, open days only, MAE primary
with WAPE and RMSE alongside. No third-party dependencies.
"""

import argparse
import collections
import csv
import json
import os
import statistics

import baselines as bl
from forecast import MultiplicativeForecast, prepare_features

TEMPLATE = "forecast_template.html"
OUTPUT = "outputs/04_forecast_review.html"
PLACEHOLDER = "/*__DATA__*/"

CUTOFF = "2015-06-14"
# Same windows approved at Stage 4, so the comparison stays like-for-like.
ROLLING_WINDOWS = [
    ("2015-04-27", "2015-06-13"),
    ("2015-03-10", "2015-04-26"),
    ("2015-01-21", "2015-03-09"),
]
FULL_HISTORY_DAYS = 894   # a store with fewer fitting days sat in the panel gap
WORST_N = 10


def read(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _pair(model, baseline, rows, scope):
    """Score a model and the benchmark over the same rows, the same way."""
    return bl.score(model, rows, stores=scope), bl.score(baseline, rows, stores=scope)


def _fit_both(fit_rows):
    return MultiplicativeForecast().fit(fit_rows), bl.StoreWeekdayMean().fit(fit_rows)


def profile(data_dir):
    train = read(os.path.join(data_dir, "train.csv"))
    test = read(os.path.join(data_dir, "test.csv"))
    attrs = {r["Store"]: r for r in read(os.path.join(data_dir, "store.csv"))}
    scope = {r["Store"] for r in test}

    rows = prepare_features(train)
    fit_rows = [r for r in rows if r["date"] < CUTOFF]
    hold_rows = [r for r in rows if r["date"] >= CUTOFF]

    model, base = _fit_both(fit_rows)
    fc, bs = _pair(model, base, hold_rows, scope)

    scored = [r for r in hold_rows if r["open"] and r["store"] in scope]
    hold_mean = sum(r["sales"] for r in scored) / len(scored)

    out = {
        "cutoff": CUTOFF,
        "window": {"start": min(r["date"] for r in hold_rows),
                   "end": max(r["date"] for r in hold_rows)},
        "scope": {"stores": len(scope), "rows": fc["n"]},
        "hold_mean": round(hold_mean, 1),
        "headline": {
            "forecast": {"name": model.name, "mae": round(fc["mae"], 1),
                         "wape": round(fc["wape"], 2), "rmse": round(fc["rmse"], 1)},
            "baseline": {"name": base.name, "mae": round(bs["mae"], 1),
                         "wape": round(bs["wape"], 2), "rmse": round(bs["rmse"], 1)},
        },
        "factors": model.factor_summary(),
    }

    # ---- rolling origins, both models refitted per window -----------------
    rolling = []
    for start, end in ROLLING_WINDOWS:
        f_rows = [r for r in rows if r["date"] < start]
        w_rows = [r for r in rows if start <= r["date"] <= end]
        m2, b2 = _fit_both(f_rows)
        f2, s2 = _pair(m2, b2, w_rows, scope)
        rolling.append({
            "start": start, "end": end,
            "forecast": round(f2["mae"], 1), "baseline": round(s2["mae"], 1),
            "forecast_wape": round(f2["wape"], 2), "baseline_wape": round(s2["wape"], 2),
            "n": f2["n"],
        })
    out["rolling"] = rolling

    # ---- per-store win / loss --------------------------------------------
    fm, bm = fc["per_store_mae"], bs["per_store_mae"]
    store_avg = collections.defaultdict(list)
    for r in scored:
        store_avg[r["store"]].append(r["sales"])
    store_avg = {s: sum(v) / len(v) for s, v in store_avg.items()}
    fit_days = collections.Counter(r["store"] for r in fit_rows)

    # Historical variability, to test whether it explains where the model struggles.
    hist_cv = {}
    per_store_hist = collections.defaultdict(list)
    for r in fit_rows:
        if r["open"] and r["sales"] > 0:
            per_store_hist[r["store"]].append(r["sales"])
    for s, v in per_store_hist.items():
        if len(v) > 2:
            hist_cv[s] = 100 * statistics.pstdev(v) / statistics.mean(v)

    per_store = []
    for s in fm:
        per_store.append({
            "store": int(s), "fc": round(fm[s]), "bl": round(bm[s]),
            "avg": round(store_avg[s]),
            "rel": round(100 * fm[s] / store_avg[s], 1),
            "gain": round(100 * (1 - fm[s] / bm[s]), 1),
            "cv": round(hist_cv.get(s, 0), 1),
            "gap": 1 if fit_days[s] < FULL_HISTORY_DAYS else 0,
            "type": attrs[s]["StoreType"], "assort": attrs[s]["Assortment"],
        })
    per_store.sort(key=lambda d: d["gain"])
    wins = [d for d in per_store if d["gain"] > 0]

    gains = sorted(d["gain"] for d in per_store)
    q = lambda p: gains[int(p * (len(gains) - 1))]
    out["winloss"] = {
        "total": len(per_store), "wins": len(wins), "losses": len(per_store) - len(wins),
        "median_gain": round(q(0.5), 1), "p10": round(q(0.10), 1), "p90": round(q(0.90), 1),
        "points": [[d["bl"], d["fc"]] for d in per_store],
        "gain_hist": _histogram([d["gain"] for d in per_store], -30, 80, 22),
        "losers": [d for d in per_store if d["gain"] <= 0],
    }

    # ---- where the model is weakest, and whether anything explains it -----
    weakest = sorted(per_store, key=lambda d: -d["rel"])[:WORST_N]
    chain_rel = statistics.median(d["rel"] for d in per_store)
    gap_rel = statistics.median([d["rel"] for d in per_store if d["gap"]])
    full_rel = statistics.median([d["rel"] for d in per_store if not d["gap"]])
    out["weakest"] = {
        "rows": weakest,
        "chain_rel": round(chain_rel, 1),
        "weak_cv": round(statistics.median(d["cv"] for d in weakest), 1),
        "chain_cv": round(statistics.median(d["cv"] for d in per_store), 1),
        "cv_corr": round(_corr([d["cv"] for d in per_store], [d["rel"] for d in per_store]), 3),
        "gap_rel": round(gap_rel, 1), "full_rel": round(full_rel, 1),
        "gap_stores": sum(1 for d in per_store if d["gap"]),
    }

    # ---- worked examples: typical, one-off spike, level break -------------
    # Chosen for the failure modes actually observed, not just the bottom of a
    # sort: a representative store, a one-off demand event, and a level break.
    typical = min(per_store, key=lambda d: abs(d["rel"] - chain_rel))
    biggest_miss = max(per_store, key=lambda d: d["fc"])
    recent_mean = {}
    for s, v in per_store_hist.items():
        tail = sorted((r for r in fit_rows if r["store"] == s and r["open"] and r["sales"] > 0),
                      key=lambda r: r["date"])[-90:]
        if tail:
            recent_mean[s] = statistics.mean(r["sales"] for r in tail)
    # The level-break example is drawn from the stores the forecast actually
    # loses on, so the three panels read as one story about failure modes.
    candidates = [d for d in per_store
                  if d["gain"] <= 0 and d["store"] != biggest_miss["store"]
                  and str(d["store"]) in recent_mean]
    shift = max(candidates or per_store,
                key=lambda d: abs(1 - store_avg[str(d["store"])] / recent_mean[str(d["store"])]))
    ratio = store_avg[str(shift["store"])] / recent_mean[str(shift["store"])]
    direction = "below" if ratio < 1 else "above"
    anchored = "too high" if ratio < 1 else "too low"
    # Describe the biggest miss from what the window actually contains rather
    # than labelling it generically.
    miss_series = _series(str(biggest_miss["store"]), "", "", hold_rows, model, base, fit_rows)
    if miss_series["closure_days"] >= 7:
        miss_label = "Clearance then closure"
        miss_note = (f"Traded up to {miss_series['peak']:,.0f} on {miss_series['peak_date']}, then closed for "
                     f"{miss_series['closure_days']} straight days from {miss_series['closure_from']}. "
                     f"The closure itself is handled; the surge before it is not.")
    else:
        miss_label = "One-off demand event"
        miss_note = (f"Peaked at {miss_series['peak']:,.0f} on {miss_series['peak_date']} against a much lower norm. "
                     f"Nothing in the calendar signals it in advance.")
    picks = [
        (typical["store"], "Typical store", "Error close to the chain median."),
        (biggest_miss["store"], miss_label, miss_note),
        (shift["store"], "Level break",
         f"Traded {abs(1 - ratio) * 100:.0f}% {direction} its recent norm through the holdout, "
         f"leaving the recent-level factor anchored {anchored}."),
    ]
    # ---- limitations panel inputs ---------------------------------------
    inner_start, inner_end = ROLLING_WINDOWS[0]
    i_fit = [r for r in rows if r["date"] < inner_start]
    i_win = [r for r in rows if inner_start <= r["date"] <= inner_end]
    from forecast import DEFAULT_FACTORS
    with_hol = MultiplicativeForecast(factors=DEFAULT_FACTORS + ["hol"])
    try:
        hol_mae = round(bl.score(with_hol.fit(i_fit), i_win, stores=scope)["mae"], 1)
    except (KeyError, ValueError):
        hol_mae = None
    base_mae = round(bl.score(MultiplicativeForecast().fit(i_fit), i_win, stores=scope)["mae"], 1)
    out["limits"] = {
        "review_20": sum(1 for d in per_store if d["rel"] >= 20),
        "review_25": sum(1 for d in per_store if d["rel"] >= 25),
        "loser_ids": [d["store"] for d in out["winloss"]["losers"]],
        "inner": {"start": inner_start, "end": inner_end,
                  "without_holiday": base_mae, "with_holiday": hol_mae},
        "watchlist": [{"store": d["store"], "rel": d["rel"], "fc": d["fc"]}
                      for d in sorted(per_store, key=lambda d: -d["rel"])[:5]],
    }

    out["examples"] = [
        _series(str(store), label, note, hold_rows, model, base, fit_rows)
        for store, label, note in picks
    ]
    return out


def _series(store, label, note, hold_rows, model, base, fit_rows):
    rows = sorted((r for r in hold_rows if r["store"] == store), key=lambda r: r["date"])
    recent = [r["sales"] for r in
              sorted((r for r in fit_rows if r["store"] == store and r["open"] and r["sales"] > 0),
                     key=lambda r: r["date"])[-90:]]
    open_rows = [r for r in rows if r["open"]]
    # Longest contiguous closure inside the window, and the peak trading day.
    longest, run = [], []
    for r in rows:
        if not r["open"]:
            run.append(r["date"])
            if len(run) > len(longest):
                longest = list(run)
        else:
            run = []
    peak = max(open_rows, key=lambda r: r["sales"])
    return {
        "closure_days": len(longest),
        "closure_from": longest[0] if longest else None,
        "closure_to": longest[-1] if longest else None,
        "peak": peak["sales"], "peak_date": peak["date"],
        "store": int(store), "label": label, "note": note,
        "dates": [r["date"] for r in rows],
        "actual": [r["sales"] for r in rows],
        "open": [1 if r["open"] else 0 for r in rows],
        "forecast": [round(0.0 if not r["open"] else model.predict_one(r), 1) for r in rows],
        "baseline": [round(0.0 if not r["open"] else base.predict_one(r), 1) for r in rows],
        "fc_mae": round(sum(abs(r["sales"] - model.predict_one(r)) for r in open_rows) / len(open_rows)),
        "bl_mae": round(sum(abs(r["sales"] - base.predict_one(r)) for r in open_rows) / len(open_rows)),
        "recent_mean": round(statistics.mean(recent)) if recent else 0,
        "hold_mean": round(statistics.mean([r["sales"] for r in open_rows])),
    }


def _histogram(values, lo, hi, bins):
    counts = [0] * bins
    width = (hi - lo) / bins
    for v in values:
        idx = int((min(max(v, lo), hi - 1e-9) - lo) / width)
        counts[min(max(idx, 0), bins - 1)] += 1
    return {"lo": lo, "hi": hi, "bins": counts}


def _corr(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
    return cov / den if den else 0.0


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
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    payload = json.dumps(stats, separators=(",", ":"), default=float)
    open(args.out, "w").write(template.replace(PLACEHOLDER, payload))

    h, w = stats["headline"], stats["winloss"]
    print(f"{args.out}: {os.path.getsize(args.out):,} bytes")
    print(f"  holdout {stats['window']['start']} to {stats['window']['end']}, "
          f"{stats['scope']['stores']} stores, {stats['scope']['rows']:,} open rows")
    print(f"  baseline MAE {h['baseline']['mae']:>8,.1f}  WAPE {h['baseline']['wape']:.2f}%")
    print(f"  forecast MAE {h['forecast']['mae']:>8,.1f}  WAPE {h['forecast']['wape']:.2f}%"
          f"   ({100*(1-h['forecast']['mae']/h['baseline']['mae']):.1f}% better)")
    print(f"  beats benchmark at {w['wins']}/{w['total']} stores")
    for r in stats["rolling"]:
        print(f"    rolling {r['start']}→{r['end']}  forecast {r['forecast']:>7,.1f} "
              f"vs baseline {r['baseline']:>7,.1f}")


if __name__ == "__main__":
    main()
