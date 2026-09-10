# UPI Reliability Analytics — Forecasting, Change-Point Detection & Risk Scoring

Three modules that run directly on top of the `npci_upi.db` database produced by
the ingestion pipeline (see the earlier `npci_pipeline.py` / `npci_pipeline_single_file.py`).

## On upsampling — you don't need it
The dataset has ~1,450 rows, but that's really **50 separate monthly time
series, each ~29 points long** — row count isn't the constraint, per-bank
history length is. Upsampling (duplicating/interpolating rows) would fabricate
months that never happened and would corrupt both forecasting and change-point
detection, which depend on genuine chronological variation. 29 real monthly
points is workable for both techniques — thinner than ideal for capturing
yearly seasonality, but sufficient to demonstrate the method correctly. This
is stated as an honest limitation in the project write-up, not hidden.

A real constraint this surfaced: NPCI's "Top 50" list rotates month to month,
so of ~106 distinct banks seen across 29 months, only 21 have full coverage
and 48 have fewer than 6 months. `MIN_MONTHS_REQUIRED` in `config.py` filters
out banks with too little history to model meaningfully, rather than forcing
a forecast on 2-3 data points.

## 1. Install
```
pip install -r requirements.txt
```

## 2. Requires
`npci_upi.db` (from the ingestion pipeline) must be in the **same folder** as
this project, containing the `upi_remitter_stats` table.

## 3. Run everything
```
python run_all_analytics.py
```
Produces three CSV files:
- `forecast_results.csv` — next-month prediction per bank (Prophet + naive baseline comparison)
- `changepoint_results.csv` — detected reliability shifts per bank, with before/after averages
- `risk_scores.csv` — volume-weighted Impact Score per bank-month, ranked

Or run any module individually:
```
python analytics/forecasting.py
python analytics/changepoint.py
python analytics/risk_scoring.py
```

## 4. What each module does, and why it's built this way

### Forecasting (`analytics/forecasting.py`)
- Runs **Prophet** per bank to forecast next month's decline rate (TD% by default).
- Also runs a **naive baseline** ("next month = same as last month") for every bank.
- **Backtests both** on the last 3 real months before trusting either — this is
  what actually justifies using Prophet rather than assuming it's better.
- **Real finding from this dataset:** Prophet only beat the naive baseline on
  ~31% of banks with enough history to test. This is reported honestly, not
  hidden — with only ~29 sparse monthly points and no real seasonality to
  exploit, a simple persistence model is often competitive. This is a
  legitimate, defensible result to present, not a failure of the pipeline.
- Forecasts are clipped to a valid 0-100% range (a raw Prophet trend line can
  dip below zero on a declining series, which is nonsensical for a percentage).

### Change-Point Detection (`analytics/changepoint.py`)
- Uses **PELT** (Pruned Exact Linear Time, via the `ruptures` library) to find
  the exact month a bank's reliability trend genuinely shifted.
- Also runs **Binary Segmentation** as a comparison method, flagging whether
  both algorithms agree on a detected shift (`also_detected_by_binseg`).
- A bank with no detected change-point simply doesn't appear in the output —
  that's a meaningful "stable, no shift" result, not a missing/failed case.
- Each detected shift reports the average value before vs. after, and whether
  it WORSENED or IMPROVED — ready to feed directly into a dashboard or RAG
  explanation step.

### Risk Scoring (`analytics/risk_scoring.py`)
- `Impact Score = decline_rate (%) x volume_share (%)`.
- Deliberately a **transparent formula, not a trained model** — appropriate
  for a compliance-monitoring context where auditability matters more than
  marginal predictive sophistication.
- Real example from this data: State Bank of India dominates the Impact Score
  ranking almost every month purely due to its ~25-26% share of total UPI
  remitter volume — even a modest decline rate there affects far more real
  transactions than a scarier-looking percentage at a tiny bank.

## 5. Config you can change (`analytics/config.py`)
- `TARGET_METRIC` — switch between `td_pct` (technical decline, this
  project's focus) and `bd_pct` (business decline) if needed.
- `MIN_MONTHS_REQUIRED` — minimum history before a bank is modeled at all.
- `BACKTEST_HOLDOUT_MONTHS` — how many recent months to hold out for testing
  forecast accuracy.
