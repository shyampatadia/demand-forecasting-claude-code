# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

Store-level demand forecasting on the Rossmann retail dataset. The repository
currently holds raw data only — no code, notebooks, or pipeline yet.

```
store.csv    1,115 rows    one row per store, static attributes
train.csv    1,017,209 rows    store x day history, includes Sales
test.csv     41,088 rows    store x day future window, no Sales
```

## Operating rules

These are hard rules. Follow them unless the user explicitly overrides one, and
say so out loud when you do.

### 1. Lead with visual HTML dashboards, not long text reports

Analysis output is a rendered page — charts, small-multiples, comparison tables
— published with the `Artifact` tool. Load the `dataviz` skill before writing
any chart code, and `artifact-design` before building the page. Prose belongs
in short callouts next to the visual that motivates it, not in multi-page
write-ups. A wall of terminal text is not a deliverable here.

### 2. Profile files before assuming structure

Never infer schema from a filename, a README, or prior knowledge of the public
Rossmann dataset. Read the header, count the rows, check the date range, count
distinct keys, and count blanks per column before writing logic against a file.
This dataset is not identical to the public version in every respect — see
Confirmed facts below, which was written from actual profiling and still needs
re-checking if the files change.

### 3. No forecasting before target, grain, and split are confirmed

Three things must be agreed with the user, in writing, before any model runs:

- **Target** — what is being predicted, and in what units.
- **Grain** — the row identity of a prediction (e.g. one store, one calendar day).
- **Split** — the exact cutoff date separating fitting data from evaluation data.

If any of the three is unstated, ask. Do not pick a default and proceed.

### 4. Never use random train/test splits

This is time series. Splits are chronological: the validation window sits
strictly after the training window, and it must be the same length as the real
forecast horizon. No `shuffle=True`, no k-fold, no `train_test_split` with a
random seed. Cross-validation, where used, means rolling-origin (walk-forward)
splits that respect time order.

### 5. Never use train-only columns as future predictors

A feature is only usable if its value is knowable at prediction time for the
forecast window. `Customers` is the trap in this dataset: it is in `train.csv`,
it correlates strongly with `Sales`, and it does not exist in `test.csv`. Using
it leaks. The same test applies to any engineered feature — a rolling mean that
peeks past the cutoff is the same error wearing a different hat.

Before adding a feature, state where its value comes from on a future date.

### 6. Every forecast is compared against a simple baseline

A model without a baseline is an unfalsifiable claim. Report at least one naive
benchmark alongside every model result on the same split and the same metric:

- seasonal naive (same store, same weekday, last week), and/or
- per-store historical median for that weekday.

If the model does not beat the baseline, that is the finding. Report it plainly
rather than tuning until the number looks better.

### 7. Explain findings in business language

Lead with what it means for the business, then support it with the metric.
"Forecasts are within about 8% of actual daily revenue per store, roughly half
the error of the current weekday-average rule" — not a bare RMSPE table.
Translate metrics into money, units, or stockout risk wherever possible. Name
the operational decision the number supports.

---

## Confirmed facts

Profiled directly from the files on 2026-08-14. Re-verify if the data changes.

**Grain and coverage**

- `train.csv` — one row per store per calendar day, 2013-01-01 to 2015-07-31
  (942 days), 1,115 stores.
- `test.csv` — same grain, 2015-08-01 to 2015-09-17 (48 days), **856 stores**.
  A complete rectangle: 856 x 48 = 41,088 rows, no gaps.
- The forecast horizon is therefore 48 days ahead, and it begins the day after
  training data ends. There is no gap between the two windows.
- All 856 test stores appear in train. 259 train stores are absent from test
  and are out of forecast scope.

**The panel is not complete**

- 180 stores are missing a contiguous 184-day block, 2014-07-01 to 2014-12-31
  (758 rows instead of 942). One further store is missing a single day.
- These are absent rows, not zero-sales rows. Any code that reshapes to a dense
  store x date matrix must handle the gap deliberately.

**Columns**

- `train.csv`: Store, DayOfWeek, Date, Sales, Customers, Open, Promo,
  StateHoliday, SchoolHoliday.
- `test.csv`: Id, Store, DayOfWeek, Date, Open, Promo, StateHoliday,
  SchoolHoliday. Note `Id` (submission key) and the absence of Sales/Customers.
- **Train-only columns: `Sales` (the target) and `Customers`.** See rule 5.

**Data quality**

- `test.Open` has 11 blank values. Needs an explicit decision, not a silent fillna.
- `StateHoliday` takes values 0/a/b/c in train but only 0/a in test — categories
  b and c never occur in the forecast window.
- 172,871 train rows (17.0%) have Sales == 0; 172,817 rows have Open == 0. The
  two nearly coincide: only 54 rows are open with zero sales.
- `store.csv` blanks: CompetitionDistance 3; CompetitionOpenSinceMonth/Year 354
  each; Promo2SinceWeek/Year and PromoInterval 544 each. The 544 correspond
  exactly to the 544 stores with Promo2 == 0, so those blanks mean
  "not applicable", not "missing".

## Assumptions

Unconfirmed. Each needs a user decision before it hardens into code.

- **Target is `Sales`** (daily revenue per store), not `Customers` — inferred
  from test.csv omitting Sales, not stated by the user.
- **Closed days.** Whether rows with Open == 0 are excluded from fitting and
  scored as zero, or modelled directly, is undecided. This materially changes
  every error metric, so settle it before comparing runs.
- **Evaluation metric** is unset. The dataset's competition origin suggests
  RMSPE with zero-sales rows excluded, but this has not been confirmed.
- **Validation split** is unset. A like-for-like choice would be the last 48
  days of train (2015-06-14 to 2015-07-31), matching the horizon length.
- **The 184-day gap** is presumed to be store refurbishment closures. The cause
  is not recorded in the data, and how the affected stores are treated in
  training is undecided.
- **`store.csv` attributes are static as of the test window** — the file has no
  effective date, so it is treated as current. Unverified.
