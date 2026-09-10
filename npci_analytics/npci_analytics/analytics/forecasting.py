"""
Forecasting module - predicts next-month decline rate (TD% by default) per bank.

Two models are run for every bank, deliberately:
  1. Prophet          - captures trend + any seasonality
  2. Naive baseline    - "next month = same as last month"

The naive baseline exists so we can PROVE Prophet is actually adding value,
not just assume it. If Prophet doesn't beat the naive baseline on backtested
accuracy for a given bank, that's flagged rather than hidden.
"""

import warnings
import os
import sys
import pandas as pd
import numpy as np
from prophet import Prophet

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.config import TARGET_METRIC, MIN_MONTHS_REQUIRED, BACKTEST_HOLDOUT_MONTHS
from analytics.data_loader import load_bank_data, get_bank_series, list_banks_with_enough_history

warnings.filterwarnings("ignore")  # Prophet/cmdstanpy are very verbose by default


def _fit_prophet(train_df):
    """train_df must have columns 'ds' and 'y'. Returns a fitted Prophet model."""
    model = Prophet(
        yearly_seasonality=False,   # not enough history (< 3 years) to trust yearly seasonality
        weekly_seasonality=False,   # monthly data, weekly seasonality is meaningless here
        daily_seasonality=False,
        changepoint_prior_scale=0.1,  # slightly more flexible trend than Prophet's default
    )
    model.fit(train_df)
    return model


def _naive_forecast(train_df, horizon):
    """Naive baseline: repeat the last observed value forward for `horizon` months."""
    last_value = train_df["y"].iloc[-1]
    return np.array([last_value] * horizon)


def _mape(actual, predicted):
    actual, predicted = np.array(actual), np.array(predicted)
    mask = actual != 0
    if mask.sum() == 0:
        return None
    return round(float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100), 2)


def backtest_bank(series_df, holdout_months=BACKTEST_HOLDOUT_MONTHS):
    """
    Holds out the last `holdout_months` real months, forecasts them using both
    Prophet and the naive baseline, and returns accuracy (MAPE) for each -
    this is what actually justifies using Prophet instead of just assuming it's better.
    """
    if len(series_df) < holdout_months + 4:
        return None  # not enough history to both train and honestly backtest

    train = series_df.iloc[:-holdout_months].rename(columns={"date": "ds", TARGET_METRIC: "y"})
    test = series_df.iloc[-holdout_months:].rename(columns={"date": "ds", TARGET_METRIC: "y"})

    model = _fit_prophet(train[["ds", "y"]])
    future = model.make_future_dataframe(periods=holdout_months, freq="MS")
    forecast = model.predict(future)
    prophet_pred = forecast["yhat"].iloc[-holdout_months:].values

    naive_pred = _naive_forecast(train, holdout_months)

    backtest = {
        "prophet_mape": _mape(test["y"].values, prophet_pred),
        "naive_mape": _mape(test["y"].values, naive_pred),
    }
    if backtest["prophet_mape"] is not None and backtest["naive_mape"] is not None:
        backtest["prophet_beats_naive"] = backtest["prophet_mape"] < backtest["naive_mape"]
    else:
        backtest["prophet_beats_naive"] = None
    return backtest


def forecast_next_month(series_df):
    """
    Fits Prophet on ALL available history for one bank and forecasts the single
    next month ahead. Returns a dict with the forecast and its uncertainty range.
    """
    train = series_df.rename(columns={"date": "ds", TARGET_METRIC: "y"})[["ds", "y"]]
    model = _fit_prophet(train)
    future = model.make_future_dataframe(periods=1, freq="MS")
    forecast = model.predict(future)
    last_row = forecast.iloc[-1]

    return {
        "forecast_date": last_row["ds"].strftime("%Y-%m"),
        # TD%/BD% can never be negative in reality - Prophet's trend line can dip
        # below zero on a declining series, so we clip to a valid 0-100% range.
        "forecasted_value": round(max(0, min(100, last_row["yhat"])), 3),
        "lower_bound": round(max(0, last_row["yhat_lower"]), 3),
        "upper_bound": round(min(100, last_row["yhat_upper"]), 3),
        "last_actual_value": round(train["y"].iloc[-1], 3),
    }


def run_forecasting_for_all_banks():
    """
    Main entry point. Loops through every bank with enough history, backtests
    Prophet vs. naive, then produces a next-month forecast. Returns one combined
    DataFrame - one row per bank.
    """
    df = load_bank_data()
    eligible_banks = list_banks_with_enough_history(df, MIN_MONTHS_REQUIRED)

    print(f"Forecasting {TARGET_METRIC} for {len(eligible_banks)} banks "
          f"(out of {df['bank_name'].nunique()} total - "
          f"the rest have fewer than {MIN_MONTHS_REQUIRED} months of history).\n")

    results = []
    for bank in eligible_banks:
        series = get_bank_series(df, bank, TARGET_METRIC)

        backtest = backtest_bank(series)
        forecast = forecast_next_month(series)

        row = {"bank_name": bank, "months_of_history": len(series)}
        row.update(forecast)
        if backtest:
            row.update(backtest)
        else:
            row.update({"prophet_mape": None, "naive_mape": None, "prophet_beats_naive": None})

        results.append(row)
        status = "OK" if backtest and backtest["prophet_beats_naive"] else (
            "Prophet did not beat naive baseline" if backtest else "too little history to backtest"
        )
        print(f"  {bank:45s} -> next month forecast: {forecast['forecasted_value']}%  ({status})")

    return pd.DataFrame(results)


if __name__ == "__main__":
    results_df = run_forecasting_for_all_banks()
    results_df.to_csv("forecast_results.csv", index=False)
    print(f"\nSaved {len(results_df)} bank forecasts to forecast_results.csv")

    valid_backtests = results_df["prophet_beats_naive"].dropna()
    if len(valid_backtests) > 0:
        beat_rate = valid_backtests.mean() * 100
        print(f"\nProphet beat the naive baseline on {beat_rate:.0f}% of the "
              f"{len(valid_backtests)} banks with enough data to backtest.")
    else:
        print("\nNo banks had enough history to backtest.")
