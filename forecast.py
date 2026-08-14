#!/usr/bin/env python3
"""A simple, reusable store-day demand forecast.

The model is a multiplicative factor model: it starts from the winning naive
baseline -- each store's average takings for that weekday -- and scales it by
factors learned for promotions, the calendar, and the store's recent level.

    prediction = base[store, weekday]
                 x promo[store, promo_flag]
                 x school[school_flag]
                 x day_of_month[dom]
                 x month[month]
                 x recent_level[store]

Each factor is estimated in turn against what the model already predicts, so a
factor only ever explains what the previous ones left behind. Factors are
shrunk toward 1.0 so that a store-promotion cell with few observations cannot
swing the forecast on noise.

Why this shape, rather than something heavier:

* Every input is knowable on a future date. `Promo`, `SchoolHoliday` and the
  calendar all appear in `test.csv`; the recent-level factor is computed from
  history strictly before the cutoff. `Customers` is absent by construction --
  it is train-only, and using it would require forecasting footfall first.
* Each factor reads as a sentence a planner can check: "this store trades 39%
  above its own weekday norm when a promotion is on."
* It fits in seconds on one core with no third-party dependencies, so it can be
  refitted per window during rolling-origin validation.

Feature selection was done on an inner window (2015-04-27 to 2015-06-13) so
that the final holdout was scored once, with the configuration already fixed.

Usage:

    from forecast import MultiplicativeForecast, prepare_features
    rows = prepare_features(csv_rows)
    model = MultiplicativeForecast().fit([r for r in rows if r["date"] < cutoff])
    model.predict_one(row)

Scores with `baselines.score`, so the forecast and the baselines are measured
by exactly the same code path. No third-party dependencies.
"""

import collections

__all__ = ["MultiplicativeForecast", "prepare_features", "DEFAULT_FACTORS"]

# Order matters: each factor explains the residual the previous ones left.
# `recent` goes last so it absorbs level drift rather than promotion effects.
DEFAULT_FACTORS = ["promo_store", "school", "dom", "month", "recent"]

SHRINKAGE = 20      # pseudo-observations pulling each factor toward 1.0
RECENT_WINDOW = 28  # trading days used for the store's recent level


def prepare_features(rows):
    """Coerce raw CSV dicts into rows carrying only forecast-time-safe fields.

    Deliberately does not carry `Customers`: it exists in train.csv, correlates
    strongly with Sales, and does not exist for any future date.
    """
    out = []
    for r in rows:
        date = r["Date"]
        out.append({
            "store": r["Store"],
            "date": date,
            "dow": int(r["DayOfWeek"]),
            "sales": float(r["Sales"]) if r.get("Sales", "") != "" else 0.0,
            "open": r["Open"] == "1",
            "promo": r["Promo"] == "1",
            "school": r["SchoolHoliday"] == "1",
            "month": int(date[5:7]),
            "dom": int(date[8:10]),
            "hol": r.get("StateHoliday", "0"),
        })
    return out


_KEYFN = {
    "promo": lambda r: r["promo"],
    "hol": lambda r: r.get("hol", "0"),
    "promo_store": lambda r: (r["store"], r["promo"]),
    "school": lambda r: r["school"],
    "dom": lambda r: r["dom"],
    "month": lambda r: r["month"],
}


class MultiplicativeForecast:
    """Store-weekday base scaled by promotion, calendar and recent-level factors."""

    key = "multiplicative"
    name = "Multiplicative factor forecast"
    description = ("Store-weekday average scaled by promotion, school-holiday, "
                   "day-of-month, month and recent-level factors.")

    def __init__(self, factors=None, shrinkage=SHRINKAGE, recent_window=RECENT_WINDOW):
        self.factors = list(DEFAULT_FACTORS if factors is None else factors)
        self.shrinkage = shrinkage
        self.recent_window = recent_window
        self._tables = []

    # -- fitting ---------------------------------------------------------
    def fit(self, fit_rows):
        trading = [r for r in fit_rows if r["open"] and r["sales"] > 0]
        if not trading:
            raise ValueError("no trading rows to fit on")

        by_key = collections.defaultdict(list)
        by_store = collections.defaultdict(list)
        for r in trading:
            by_key[(r["store"], r["dow"])].append(r["sales"])
            by_store[r["store"]].append(r["sales"])
        self._base = {k: sum(v) / len(v) for k, v in by_key.items()}
        self._store_mean = {s: sum(v) / len(v) for s, v in by_store.items()}
        self._global_mean = sum(r["sales"] for r in trading) / len(trading)

        self._tables = []
        for name in self.factors:
            if name == "recent":
                self._tables.append((name, self._fit_recent(trading), _store_key))
            else:
                keyfn = _KEYFN[name]
                acc = collections.defaultdict(list)
                for r in trading:
                    acc[keyfn(r)].append(r["sales"] / self.predict_one(r))
                self._tables.append((name, self._shrink(acc), keyfn))
        return self

    def _shrink(self, acc):
        """Mean ratio per cell, pulled toward 1.0 by `shrinkage` pseudo-counts."""
        k = self.shrinkage
        return {key: (sum(v) + k) / (len(v) + k) for key, v in acc.items()}

    def _fit_recent(self, trading):
        """Per-store level drift, from the last `recent_window` trading days."""
        per = collections.defaultdict(list)
        for r in sorted(trading, key=lambda r: r["date"]):
            per[r["store"]].append(r["sales"] / self.predict_one(r))
        k = self.shrinkage
        out = {}
        for s, ratios in per.items():
            tail = ratios[-self.recent_window:]
            out[s] = (sum(tail) + k) / (len(tail) + k)
        return out

    # -- prediction ------------------------------------------------------
    def predict_one(self, row):
        """Predicted takings for one store-day. Closed days are the caller's job."""
        pred = self._base.get((row["store"], row["dow"]))
        if pred is None:
            pred = self._store_mean.get(row["store"], self._global_mean)
        for _name, table, keyfn in self._tables:
            pred *= table.get(keyfn(row), 1.0)
        return pred

    # -- inspection ------------------------------------------------------
    def factor_summary(self):
        """Human-readable factor values, for publishing what the model learned."""
        out = {}
        def spread(values):
            v = sorted(values)
            return {"cells": len(v), "median": v[len(v) // 2],
                    "p10": v[int(0.10 * (len(v) - 1))], "p90": v[int(0.90 * (len(v) - 1))]}

        for name, table, _keyfn in self._tables:
            if name == "promo_store":
                # Split by the promo flag -- pooling both halves would average
                # the promotion effect away against its own baseline.
                out[name] = {
                    "on": spread([v for (_s, p), v in table.items() if p]),
                    "off": spread([v for (_s, p), v in table.items() if not p]),
                }
            elif name == "recent":
                out[name] = spread(table.values())
            else:
                out[name] = {str(k): v for k, v in sorted(table.items(), key=lambda kv: str(kv[0]))}
        return out


def _store_key(row):
    return row["store"]
