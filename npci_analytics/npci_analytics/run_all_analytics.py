"""
Runs all three analytics modules in sequence and saves their outputs as CSVs.
Usage: python run_all_analytics.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analytics.forecasting import run_forecasting_for_all_banks
from analytics.changepoint import run_changepoint_detection_for_all_banks
from analytics.risk_scoring import run_risk_scoring

if __name__ == "__main__":
    print("=" * 70)
    print("STEP 1/3: FORECASTING")
    print("=" * 70)
    forecast_df = run_forecasting_for_all_banks()
    forecast_df.to_csv("forecast_results.csv", index=False)
    print(f"-> Saved forecast_results.csv ({len(forecast_df)} banks)\n")

    print("=" * 70)
    print("STEP 2/3: CHANGE-POINT DETECTION")
    print("=" * 70)
    changepoint_df = run_changepoint_detection_for_all_banks()
    changepoint_df.to_csv("changepoint_results.csv", index=False)
    print(f"-> Saved changepoint_results.csv ({len(changepoint_df)} detected shifts)\n")

    print("=" * 70)
    print("STEP 3/3: RISK SCORING")
    print("=" * 70)
    risk_df = run_risk_scoring()
    risk_df.to_csv("risk_scores.csv", index=False)
    print(f"-> Saved risk_scores.csv ({len(risk_df)} bank-months)\n")

    print("=" * 70)
    print("ALL DONE. Three output files ready:")
    print("  forecast_results.csv    - next-month prediction per bank")
    print("  changepoint_results.csv - detected reliability shifts per bank")
    print("  risk_scores.csv          - volume-weighted Impact Score per bank-month")
    print("=" * 70)
