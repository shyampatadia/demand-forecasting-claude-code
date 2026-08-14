#!/usr/bin/env python3
"""Naive baselines for store-day demand forecasting.

Reusable module. Every baseline here is fitted on history strictly before a
cutoff date and then predicts the whole horizon from that standing start --
the same position a planner is in on the morning of the cutoff.

That constraint is the point of this file. A "same weekday last week" rule is
trivial to write in a way that reads last week's actuals *from inside the
forecast window*; on a 48-day horizon those actuals do not exist yet, so such a
rule cannot be run in production and its score is meaningless. `LastWeekday`
below anchors on the last trading occurrence before the cutoff.
`LeakyLastWeekday` implements the tempting version deliberately, and exists
only to quantify how much the leak flatters the number.

Usage:

    from baselines import LastWeekday, RecentAverage, StoreWeekdayMean, score

    bl = LastWeekday().fit(fit_rows)
    result = score(bl, holdout_rows)
    print(result["mae"])

A row is a dict with at least: Store, Date, DayOfWeek, Sales, Open.
No third-party dependencies.
"""

import collections
from datetime import date, timedelta

__all__ = [
    "LastWeekday", "RecentAverage", "StoreWeekdayMean", "LeakyLastWeekday",
    "score", "mae", "wape", "rmse", "prepare",
]

RECENT_WINDOW = 28  # trading days -- four weeks, so every weekday is represented


def prepare(rows):
    """Coerce raw CSV dicts into the compact form the baselines expect."""
    return [
        {
            "store": r["Store"],
            "date": r["Date"],
            "dow": int(r["DayOfWeek"]),
            "sales": float(r["Sales"]),
            "open": r["Open"] == "1",
        }
        for r in rows
    ]


def _trading(rows):
    """Days the till actually rang. Closed days carry no demand signal."""
    return [r for r in rows if r["open"] and r["sales"] > 0]


class Baseline:
    """Fit on history before the cutoff; predict any future store-day."""

    key = "base"
    name = "Baseline"
    description = ""

    def fit(self, fit_rows):
        raise NotImplementedError

    def predict_one(self, row):
        raise NotImplementedError

    def _fit_fallbacks(self, fit_rows):
        """Per-store and chain-wide means, used when a finer key is missing.

        Sundays are the reason this matters: most stores never trade on one, so
        a (store, weekday) lookup has no entry for the Sundays that do appear in
        a holdout window.
        """
        trading = _trading(fit_rows)
        by_store = collections.defaultdict(list)
        for r in trading:
            by_store[r["store"]].append(r["sales"])
        self._store_mean = {s: sum(v) / len(v) for s, v in by_store.items()}
        self._global_mean = sum(r["sales"] for r in trading) / len(trading)

    def _fallback(self, row):
        return self._store_mean.get(row["store"], self._global_mean)


class LastWeekday(Baseline):
    """Same weekday, most recent trading occurrence before the cutoff.

    The honest form of "same weekday last week" for a multi-week horizon: the
    anchor is fixed at the cutoff and reused across the whole window, because
    nothing inside the window is observable when the forecast is made.
    """

    key = "last_weekday"
    name = "Same weekday, last week"
    description = ("Each store's takings on the most recent trading occurrence of that "
                   "weekday before the cutoff, held flat across the horizon.")

    def fit(self, fit_rows):
        self._fit_fallbacks(fit_rows)
        latest = {}
        for r in sorted(_trading(fit_rows), key=lambda r: r["date"]):
            latest[(r["store"], r["dow"])] = r["sales"]
        self._table = latest
        return self

    def predict_one(self, row):
        v = self._table.get((row["store"], row["dow"]))
        return v if v is not None else self._fallback(row)


class RecentAverage(Baseline):
    """Mean of each store's last N trading days before the cutoff."""

    key = "recent_avg"
    name = f"Recent {RECENT_WINDOW}-day average"
    description = (f"Mean takings over each store's last {RECENT_WINDOW} trading days before "
                   "the cutoff, applied flat to every day of the horizon. Carries no weekday shape.")

    def __init__(self, window=RECENT_WINDOW):
        self.window = window

    def fit(self, fit_rows):
        self._fit_fallbacks(fit_rows)
        by_store = collections.defaultdict(list)
        for r in sorted(_trading(fit_rows), key=lambda r: r["date"]):
            by_store[r["store"]].append(r["sales"])
        self._table = {
            s: sum(v[-self.window:]) / len(v[-self.window:]) for s, v in by_store.items()
        }
        return self

    def predict_one(self, row):
        v = self._table.get(row["store"])
        return v if v is not None else self._fallback(row)


class StoreWeekdayMean(Baseline):
    """Each store's mean takings for that weekday across all fitting history."""

    key = "store_weekday"
    name = "Store weekday average"
    description = ("Each store's average takings for that weekday over the whole fitting "
                   "period. The rule a planner would reach for by hand.")

    def fit(self, fit_rows):
        self._fit_fallbacks(fit_rows)
        buckets = collections.defaultdict(list)
        for r in _trading(fit_rows):
            buckets[(r["store"], r["dow"])].append(r["sales"])
        self._table = {k: sum(v) / len(v) for k, v in buckets.items()}
        return self

    def predict_one(self, row):
        v = self._table.get((row["store"], row["dow"]))
        return v if v is not None else self._fallback(row)


class LeakyLastWeekday(Baseline):
    """DIAGNOSTIC ONLY -- reads actuals from inside the forecast window.

    Predicts each holdout day from the same store's takings seven days earlier,
    including when that day also falls inside the holdout. On a 48-day horizon
    those values are not known at prediction time, so this rule cannot be run
    for real. It is here to measure the size of the illusion, never to be
    reported as a baseline.
    """

    key = "leaky"
    name = "Same weekday, in-window actuals"
    description = "Not achievable in production. Included only to size the leak."

    def fit(self, fit_rows):
        self._fit_fallbacks(fit_rows)
        self._seen = {(r["store"], r["date"]): r["sales"] for r in _trading(fit_rows)}
        self._fallback_table = {}
        for r in sorted(_trading(fit_rows), key=lambda r: r["date"]):
            self._fallback_table[(r["store"], r["dow"])] = r["sales"]
        return self

    def observe(self, holdout_rows):
        """Let the rule see holdout actuals -- the leak, made explicit."""
        for r in _trading(holdout_rows):
            self._seen[(r["store"], r["date"])] = r["sales"]
        return self

    def predict_one(self, row):
        prev = (date.fromisoformat(row["date"]) - timedelta(days=7)).isoformat()
        v = self._seen.get((row["store"], prev))
        if v is None:
            v = self._fallback_table.get((row["store"], row["dow"]))
        return v if v is not None else self._fallback(row)


def mae(pairs):
    """Mean absolute error over (actual, predicted) pairs."""
    return sum(abs(a - p) for a, p in pairs) / len(pairs) if pairs else 0.0


def wape(pairs):
    """Weighted absolute percentage error: total error over total actual.

    Scale-relative, so it reads as "we are out by x% of the takings that
    actually happened" -- but unlike MAPE it is a single ratio of sums, so a
    near-zero actual cannot blow it up.
    """
    denom = sum(a for a, _ in pairs)
    return 100 * sum(abs(a - p) for a, p in pairs) / denom if denom else 0.0


def rmse(pairs):
    """Root mean squared error -- a diagnostic for large individual misses."""
    if not pairs:
        return 0.0
    return (sum((a - p) ** 2 for a, p in pairs) / len(pairs)) ** 0.5


def score(baseline, holdout_rows, score_closed=False, stores=None):
    """Score a fitted baseline over a holdout window.

    Closed days are predicted as exactly 0 and, by default, left out of the
    score: `Open` is published ahead of the window, so a closure is a known
    fact rather than a forecast. Scoring them would hand the rule a large block
    of free exact answers -- which lowers the number without improving any
    forecast. Pass score_closed=True to measure that operational total.

    `stores` restricts scoring to a population (e.g. the stores present in the
    live forecast window). Pass None to score every store in the rows given.
    """
    pairs, per_store = [], collections.defaultdict(list)
    for r in holdout_rows:
        if stores is not None and r["store"] not in stores:
            continue
        pred = 0.0 if not r["open"] else baseline.predict_one(r)
        if not r["open"] and not score_closed:
            continue
        pairs.append((r["sales"], pred))
        per_store[r["store"]].append(abs(r["sales"] - pred))

    return {
        "key": baseline.key,
        "name": baseline.name,
        "description": baseline.description,
        "mae": mae(pairs),
        "wape": wape(pairs),
        "rmse": rmse(pairs),
        "n": len(pairs),
        "per_store_mae": {s: sum(v) / len(v) for s, v in per_store.items()},
    }
