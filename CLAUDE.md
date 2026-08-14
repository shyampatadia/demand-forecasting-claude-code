# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

Store-level demand forecasting on the Rossmann retail dataset. The repository
holds the raw data, four dashboards covering audit through forecast review, and
the scripts that rebuild them. See `outputs/README.md` for how to rerun.

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

Every fact below is visible in the Stage 1 audit dashboard,
`outputs/01_data_audit.html`. Regenerate the dashboards and re-check this
section if the data changes.

**Files and roles**

| file | role | rows | columns |
|---|---|---|---|
| `train.csv` | history — the only file carrying `Sales` | 1,017,209 | 9 |
| `test.csv` | forecast window — no `Sales` | 41,088 | 8 |
| `store.csv` | static store attributes, one row per store | 1,115 | 10 |

**Grain**

- Grain is **one row per store per calendar day** in both `train.csv` and
  `test.csv`. `Store + Date` is unique in both — 1,017,209 distinct keys in
  train, 41,088 in test — with **zero duplicates** in either file.
- `store.csv` is unique on `Store` (1,115 rows, 1,115 keys). `test.Id` is
  unique (41,088).

**Dates and coverage**

- `train.csv` — 2013-01-01 to 2015-07-31, 942 days, up to 1,115 stores.
- `test.csv` — 2015-08-01 to 2015-09-17, 48 days, **856 stores**. A complete
  rectangle: 856 x 48 = 41,088 rows, no gaps.
- The forecast horizon is therefore 48 days, beginning the day after training
  data ends. There is no gap between the two windows.
- 856 of the 1,115 train stores are in forecast scope; the other 259 are out
  of scope.

**The panel is not complete**

- 181 stores are missing history. 180 of them lack exactly a contiguous
  **184-day block**, 2014-07-01 to 2014-12-31; one lacks a single day.
- These are absent rows, not zero-sales rows. Any code that reshapes to a dense
  store x date matrix must handle the gap deliberately.

**Target**

- The target is **`Sales`** — daily revenue per store, one value per store per
  calendar day.
- 844,338 rows are open with non-zero Sales: median 6,369, mean 6,956,
  95th percentile 12,668, range 46 to 41,551.

**`Customers` is train-only and not safe for future forecasting**

- Present in `train.csv`, **absent from `test.csv`**.
- Correlates **0.824** with Sales across 844,338 trading days; mean 763
  customers per day.
- Classified **descriptive only**: it is a same-day measurement, not a
  published plan, so it has no value on a future date. Using it would require
  forecasting footfall first. See rule 5.

**Open, closed and zero-sales rows**

- 83.0% of train rows are Open, 17.0% closed.
- 172,871 rows have Sales == 0; 172,817 of those are closed. Only **54 rows**
  are open with zero sales, and **0 rows** are closed with non-zero sales.

**Missing values**

- 7 of 27 columns contain blanks; six of the seven are in `store.csv`.
- `store.csv` — `Promo2SinceWeek`, `Promo2SinceYear`, `PromoInterval`: 544 each
  (48.8%). These match the 544 stores with `Promo2 == 0` **exactly**, so they
  mean "not applicable", not "missing". Imputing them would invent promotions.
- `store.csv` — `CompetitionOpenSinceMonth` and `CompetitionOpenSinceYear`: 354
  each (31.7%). `CompetitionDistance`: 3 (0.3%), genuinely missing.
- `store.csv` — `Store`, `StoreType`, `Assortment`, `Promo2` have no blanks.
- `test.csv` — `Open`: 11 blanks (0.03%) inside the forecast window. Needs a
  stated rule, not a silent fillna.

**Category coverage**

- `StateHoliday` takes values 0/a/b/c in train but only 0/a in test — Easter
  and Christmas never occur in the forecast window.
- `Promo` is present in both: 38% of train rows, 40% of test rows.

**Columns**

- `train.csv`: Store, DayOfWeek, Date, Sales, Customers, Open, Promo,
  StateHoliday, SchoolHoliday.
- `test.csv`: Id, Store, DayOfWeek, Date, Open, Promo, StateHoliday,
  SchoolHoliday. Note `Id` (submission key) and the absence of Sales/Customers.
- `store.csv`: Store, StoreType, Assortment, CompetitionDistance,
  CompetitionOpenSinceMonth, CompetitionOpenSinceYear, Promo2,
  Promo2SinceWeek, Promo2SinceYear, PromoInterval.
- **Train-only columns: `Sales` (the target) and `Customers`.** See rule 5.

**Stage outputs**

All four dashboards are written to `outputs/`, with `outputs/README.md`
documenting how to rerun them from the raw CSVs.

- `outputs/01_data_audit.html` — data audit. Built by `build_audit.py`.
- `outputs/02_demand_patterns.html` — descriptive demand patterns. Built by
  `build_patterns.py`.
- `outputs/03_baseline_dashboard.html` — validation design and naive baselines.
  Built by `build_baseline.py` on top of `baselines.py`.
- `outputs/04_forecast_review.html` — the forecast against that benchmark.
  Built by `build_forecast.py` on top of `forecast.py`.
- `python3 outputs/rebuild_all.py` rebuilds all four in order.
- The HTML files are generated output. Edit the templates or the build scripts
  and re-run; never hand-edit the rendered HTML.

## Validation and scoring decisions

Approved by the user. These govern every score reported from Stage 4 onward.

**Split**

- **Final holdout — 2015-06-14 to 2015-07-31**, the last 48 days of train,
  matching the 48-day forecast horizon. Fitting data ends 2015-06-13.
- **Rolling-origin windows** (primary stability checks, 48 days each):
  2015-04-27→2015-06-13, 2015-03-10→2015-04-26, 2015-01-21→2015-03-09. Each
  rule is refitted from scratch on data before its window.
- **Season-matched diagnostic — 2013-06-14 to 2013-07-31**, reported
  separately and never pooled with the rolling origins: it sits on 164 days of
  preceding history against 894 for the final holdout.
- Windows overlapping the 184-day panel gap (2014-07-01 to 2014-12-31) are
  excluded — only 935 stores report inside it. This is why just three
  consecutive rolling origins are available.

**Store scope**

- **Primary evaluation population: the 856 stores present in `test.csv`**,
  matching live forecast scope. 35,262 open store-days in the final holdout.
- All 1,115 stores are reported only as a **secondary population diagnostic**
  (45,884 open store-days). Both populations are labelled wherever either
  appears.

**Closed days**

- Where `Open == 0`, predicted Sales is **0** as a business rule.
- **Open-day MAE is the primary headline score.**
- All-day MAE is reported only as a **secondary operational measure**. It is
  lower because known zeros enter the denominator, and must never be described
  as improved forecasting accuracy.
- WAPE is unaffected by this choice: closed days contribute nothing to either
  its numerator or its denominator.

**Metrics**

- **MAE primary** — in the target's own units, so it reads as money per store
  per day.
- **WAPE secondary** — total error over total actual, for scale context.
- **RMSE** — outlier-sensitive diagnostic.
- Store-level MAE distribution is reported for context and **does not replace**
  the chain-level primary metric.
- RMSPE and MAPE remain excluded: undefined at zero, and 172,871 train rows
  have zero Sales.

**Excluded from benchmark selection**

- A "same weekday last week" rule reading actuals from *inside* the forecast
  window is an **invalid diagnostic**, not a candidate. It cannot run in
  production on a 48-day horizon. Measured at MAE 2,165 on the primary
  population — worse than every valid rule, because a single lagged day is
  noisier than an average.

## Future-forecast data handling

Decisions that apply to the live forecast window only. They do not affect the
historical holdout, where `Open` is observed.

- **Blank `test.Open`** — 11 rows, all store 622, dates 2015-09-05 to
  2015-09-17. Inferred as open where the store traded on a majority of that
  weekday in history. **Fallback 1**: no store-weekday history → the chain's
  modal Open for that weekday. **Fallback 2**: store absent from train → the
  same chain-wide modal. All 11 resolved on the primary rule; no fallback
  fired. Every inferred value is listed on the dashboard — never silently
  filled.

## Stage 4 baseline results

Primary population, open days, final holdout. The benchmark any model must beat.

| rule | MAE | WAPE | RMSE |
|---|---|---|---|
| **Store weekday average** | **1,259** | **17.80%** | 1,664 |
| Recent 28-day average | 1,387 | 19.61% | 1,827 |
| Same weekday, last week | 1,454 | 20.56% | 2,055 |

- The benchmark wins all three rolling origins and the season-matched window;
  its MAE ranges 1,093 to 1,283 across them, with the final holdout at 1,259
  inside that range.
- Baselines are naive rules, not models. See Stage 5 for the first fitted model.

## Stage 5 forecast results

Primary population, open days, final holdout — the same split and scoring path
as the baselines above.

| model | MAE | WAPE | RMSE |
|---|---|---|---|
| Store weekday average (benchmark) | 1,259 | 17.80% | 1,664 |
| **Multiplicative factor forecast** | **723** | **10.22%** | **1,015** |

- **42.6% lower MAE than the benchmark**, and better at 852 of the 856 stores.
- Model: each store's weekday average scaled by promotion (per store),
  school holiday, day of month, month, and the store's recent level. Built by
  `build_forecast.py` on top of `forecast.py`; output `outputs/04_forecast_review.html`.
- Every input is knowable at forecast time. **`Customers` is excluded by
  construction** — `prepare_features()` never carries it.
- Feature selection ran on an inner window (2015-04-27 to 2015-06-13) with the
  configuration frozen before the final holdout was scored once.
- It also beats the benchmark on all three rolling origins, by 31% to 43%.
- `StateHoliday` was **tested and rejected**: adding it raised inner-window MAE
  from 734 to 764.
- Known failure modes: one-off clearance surges (store 909 took 41,551 — the
  dataset maximum — then closed for 16 days), and level breaks after the cutoff
  (store 722 traded 26% below its recent norm). Store-level variability
  correlates 0.452 with relative error. The 184-day panel gap does **not**
  degrade forecasts: 10.0% relative error against 9.6% for full-history stores.

## Assumptions

Unconfirmed. Each needs a user decision before it hardens into code.

- **The 184-day gap** is presumed to be store refurbishment closures. The cause
  is not recorded in the data, and how the affected stores are treated in
  training is undecided.
- **`store.csv` attributes are static as of the test window** — the file has no
  effective date, so it is treated as current. Unverified.
- **Rolling-origin cadence for Stage 5** — whether a model is re-scored on all
  four windows or only the final holdout is undecided.
- **Extending the rolling-origin set** would mean either accepting a 180-store
  drop in coverage or changing the window length. Undecided.
