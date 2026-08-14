# Rossmann demand forecasting — dashboards

Four self-contained HTML dashboards covering the workflow from raw data to a
scored forecast. Open any of them directly in a browser; there is no server, no
build step and no network access required. Each carries its own data inline.

| File | Stage | What it answers |
|---|---|---|
| [`01_data_audit.html`](01_data_audit.html) | Audit | Is the data fit to model? Grain, coverage, missing values, target, leakage risks |
| [`02_demand_patterns.html`](02_demand_patterns.html) | Patterns | How does the chain actually trade? Descriptive only |
| [`03_baseline_dashboard.html`](03_baseline_dashboard.html) | Baselines | How will a forecast be judged, and what must it beat? |
| [`04_forecast_review.html`](04_forecast_review.html) | Forecast | Does the model beat that benchmark, and where does it fail? |

## Headline result

On the approved holdout (2015-06-14 → 2015-07-31), scored on the 856 stores in
live forecast scope, open days only:

| Model | MAE | WAPE | RMSE |
|---|---:|---:|---:|
| Store weekday average (benchmark) | 1,259 | 17.80% | 1,664 |
| **Multiplicative factor forecast** | **723** | **10.22%** | **1,015** |

42.6% lower MAE than the benchmark, better at 852 of the 856 stores. Read the
limitations panel at the foot of `04_forecast_review.html` before acting on it.

## Rebuilding from the raw CSVs

Requires **Python 3.8+ and nothing else** — no numpy, pandas or scikit-learn.

Put `train.csv`, `test.csv` and `store.csv` in the repository root, then:

```bash
python3 outputs/rebuild_all.py
```

That runs all four stages in order and rewrites the four HTML files in this
folder. It takes roughly a minute; Stage 4 and Stage 5 refit the models on each
rolling-origin window, which is most of that.

To rebuild a single dashboard, run its stage script from the repository root:

```bash
python3 build_audit.py        # -> outputs/01_data_audit.html
python3 build_patterns.py     # -> outputs/02_demand_patterns.html
python3 build_baseline.py     # -> outputs/03_baseline_dashboard.html
python3 build_forecast.py     # -> outputs/04_forecast_review.html
```

If the CSVs live elsewhere:

```bash
python3 outputs/rebuild_all.py --data-dir /path/to/csvs
python3 build_audit.py --data-dir /path/to/csvs
```

Other flags: `python3 outputs/rebuild_all.py --list` shows the stages without
building; each stage script also accepts `--template` and `--out`.

## How the pieces fit

```
train.csv  test.csv  store.csv        raw data, repository root
        |
        |  build_audit.py      + audit_template.html
        |  build_patterns.py   + patterns_template.html
        |  build_baseline.py   + baseline_template.html   -> uses baselines.py
        |  build_forecast.py   + forecast_template.html   -> uses forecast.py
        v
outputs/*.html                        rendered dashboards
```

Two reusable modules sit behind the last two stages:

- **`baselines.py`** — the three naive rules (`LastWeekday`, `RecentAverage`,
  `StoreWeekdayMean`), plus `score()`, `mae()`, `wape()`, `rmse()`. Also holds
  `LeakyLastWeekday`, which reads actuals from inside the forecast window and
  exists only as an invalid diagnostic — never as a candidate benchmark.
- **`forecast.py`** — `MultiplicativeForecast`, the Stage 5 model. Each store's
  weekday average scaled by promotion, school holiday, day of month, month and
  the store's recent trading level.

The forecast is scored through the same `baselines.score` path as the
benchmark, so the comparison is like-for-like by construction.

**The HTML files are generated output.** Edit a template or a build script and
re-run; never hand-edit a rendered dashboard, or the next rebuild will discard
the change.

## Rules the numbers depend on

These are settled decisions, not defaults. `CLAUDE.md` in the repository root
records them in full.

- **Target** — `Sales`, daily revenue per store.
- **Grain** — one store, one calendar day.
- **Split** — final 48 days of train held out (2015-06-14 → 2015-07-31),
  matching the 48-day forecast horizon. Chronological, never random.
- **Store scope** — the 856 stores present in `test.csv` are the primary
  evaluation population. All 1,115 are a secondary diagnostic only.
- **Closed days** — predicted `Sales` of 0 as a business rule; open-day MAE is
  the headline. All-day MAE is an operational measure only and is lower purely
  because known zeros enter the denominator.
- **Metrics** — MAE primary, WAPE for scale context, RMSE for outliers.
  RMSPE and MAPE are excluded: undefined at zero, and 172,871 rows have zero
  Sales.
- **`Customers` is never a predictor.** It exists in `train.csv`, correlates
  0.824 with Sales, and does not exist in `test.csv` — a same-day measurement,
  not a published plan. `forecast.prepare_features()` never carries it.

## Regenerating after a data change

The dashboards are built from whatever CSVs you point them at, but the written
findings in `CLAUDE.md` were taken from this specific extract. If the data
changes, rebuild and then re-check that document — particularly the confirmed
facts and the Stage 4 and Stage 5 result tables.
