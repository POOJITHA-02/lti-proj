"""
Change-point detection module - finds the exact month a bank's reliability
trend genuinely shifted, as opposed to normal month-to-month noise.

Uses PELT (Pruned Exact Linear Time) via the `ruptures` library - the
standard, efficient algorithm for this exact task. A secondary, simpler
method (Binary Segmentation) is also run for comparison, since presenting
only one algorithm with no comparison is a weaker methodological story.
"""

import os
import sys
import pandas as pd
import numpy as np
import ruptures as rpt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.config import TARGET_METRIC, MIN_MONTHS_REQUIRED
from analytics.data_loader import load_bank_data, get_bank_series, list_banks_with_enough_history

# Minimum number of points required between two change-points. Prevents flagging
# noise as a "shift" every single month - a real trend shift should persist.
MIN_SEGMENT_SIZE = 3

# Penalty value for PELT - higher = fewer, more confident change-points.
# Tuned for percentage data typically ranging 0-5%; adjust if using bd_pct instead.
PELT_PENALTY = 1.0


def detect_changepoints_pelt(values):
    """Returns a list of indices where PELT detects a genuine shift in the series."""
    signal = np.array(values).reshape(-1, 1)
    algo = rpt.Pelt(model="rbf", min_size=MIN_SEGMENT_SIZE).fit(signal)
    breakpoints = algo.predict(pen=PELT_PENALTY)
    return breakpoints[:-1]  # ruptures includes the series length as a final marker - drop it


def detect_changepoints_binseg(values, n_bkps_guess=2):
    """Binary Segmentation as a comparison method - faster, simpler, less precise than PELT."""
    signal = np.array(values).reshape(-1, 1)
    algo = rpt.Binseg(model="rbf", min_size=MIN_SEGMENT_SIZE).fit(signal)
    try:
        breakpoints = algo.predict(n_bkps=n_bkps_guess)
        return breakpoints[:-1]
    except Exception:
        return []


def summarize_shift(series_df, breakpoint_idx, metric):
    """Describes what changed at a detected breakpoint: average before vs. after."""
    before = series_df[metric].iloc[:breakpoint_idx]
    after = series_df[metric].iloc[breakpoint_idx:]

    if len(before) == 0 or len(after) == 0:
        return None

    return {
        "change_month": series_df["date"].iloc[breakpoint_idx].strftime("%Y-%m"),
        "avg_before": round(before.mean(), 3),
        "avg_after": round(after.mean(), 3),
        "direction": "WORSENED" if after.mean() > before.mean() else "IMPROVED",
        "magnitude_pct_points": round(after.mean() - before.mean(), 3),
    }


def run_changepoint_detection_for_all_banks():
    """
    Main entry point. Loops through every bank with enough history, runs PELT
    (and Binseg for comparison), and reports any detected reliability shifts.
    Returns one row PER DETECTED CHANGE-POINT (a bank with no real shift simply
    won't appear in the output - that itself is a meaningful, stable result).
    """
    df = load_bank_data()
    eligible_banks = list_banks_with_enough_history(df, MIN_MONTHS_REQUIRED)

    print(f"Running change-point detection on {TARGET_METRIC} for {len(eligible_banks)} banks.\n")

    results = []
    for bank in eligible_banks:
        series = get_bank_series(df, bank, TARGET_METRIC)
        values = series[TARGET_METRIC].tolist()

        if len(values) < MIN_SEGMENT_SIZE * 2:
            continue  # can't have two segments if there isn't room for both

        pelt_bkps = detect_changepoints_pelt(values)
        binseg_bkps = detect_changepoints_binseg(values)

        if not pelt_bkps:
            continue  # no genuine shift detected for this bank - a stable bank, not an error

        for bp in pelt_bkps:
            shift = summarize_shift(series, bp, TARGET_METRIC)
            if shift is None:
                continue
            shift["bank_name"] = bank
            shift["detected_by_pelt"] = True
            shift["also_detected_by_binseg"] = bp in binseg_bkps
            results.append(shift)
            print(f"  {bank:45s} -> shift detected at {shift['change_month']}: "
                  f"{shift['avg_before']}% -> {shift['avg_after']}% ({shift['direction']})")

    if not results:
        print("  No significant change-points detected in the current dataset.")
        return pd.DataFrame()

    result_df = pd.DataFrame(results)
    cols = ["bank_name", "change_month", "avg_before", "avg_after",
            "magnitude_pct_points", "direction", "detected_by_pelt", "also_detected_by_binseg"]
    return result_df[cols]


if __name__ == "__main__":
    results_df = run_changepoint_detection_for_all_banks()
    results_df.to_csv("changepoint_results.csv", index=False)
    print(f"\nSaved {len(results_df)} detected change-point(s) to changepoint_results.csv")

    if len(results_df) > 0:
        worsened = (results_df["direction"] == "WORSENED").sum()
        print(f"{worsened} of {len(results_df)} detected shifts were WORSENING trends.")
