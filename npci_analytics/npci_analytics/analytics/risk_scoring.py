"""
Risk scoring module - computes a transparent, auditable Impact Score per
bank per month, combining decline rate with real transaction volume share.

Deliberately NOT a machine-learning model. In a compliance/monitoring
context, a simple, explainable formula is more appropriate and more
defensible than a black-box score - this is a design choice, not a
limitation, and is worth stating explicitly (see project write-up).

    Impact Score = decline_rate (%) x volume_share (% of that month's total)

A bank with a small decline rate but huge volume share can outrank a bank
with a scarier-looking decline rate but tiny real-world transaction volume -
which is exactly the point of this metric.
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.config import TARGET_METRIC
from analytics.data_loader import load_bank_data


def compute_volume_share(df):
    """Adds a column: what % of that month's TOTAL volume this bank represents."""
    monthly_totals = df.groupby("period")["total_volume_mn"].transform("sum")
    df["volume_share_pct"] = round((df["total_volume_mn"] / monthly_totals) * 100, 4)
    return df


def compute_impact_score(df, metric=TARGET_METRIC):
    """
    Impact Score = decline rate x volume share.
    Higher score = more real-world transactions affected by this bank's decline rate,
    not just a high percentage in isolation.
    """
    df["impact_score"] = round(df[metric] * df["volume_share_pct"], 4)
    return df


def rank_monthly_risk(df):
    """Adds a rank column: 1 = highest Impact Score within that month."""
    df["risk_rank_in_month"] = df.groupby("period")["impact_score"].rank(ascending=False, method="min").astype(int)
    return df


def run_risk_scoring():
    df = load_bank_data()
    df = compute_volume_share(df)
    df = compute_impact_score(df)
    df = rank_monthly_risk(df)

    df = df.sort_values(["period", "risk_rank_in_month"])

    output_cols = ["period", "bank_name", TARGET_METRIC, "total_volume_mn",
                   "volume_share_pct", "impact_score", "risk_rank_in_month"]

    print("Top 5 highest-impact bank-months across the entire dataset:\n")
    top5 = df.sort_values("impact_score", ascending=False).head(5)
    for _, row in top5.iterrows():
        print(f"  {row['period']}  {row['bank_name']:40s}  "
              f"{TARGET_METRIC}={row[TARGET_METRIC]}%  volume_share={row['volume_share_pct']}%  "
              f"-> Impact Score = {row['impact_score']}")

    return df[output_cols]


if __name__ == "__main__":
    results_df = run_risk_scoring()
    results_df.to_csv("risk_scores.csv", index=False)
    print(f"\nSaved {len(results_df)} bank-month risk scores to risk_scores.csv")

    latest_period = results_df["period"].max()
    latest_top3 = results_df[results_df["period"] == latest_period].nsmallest(3, "risk_rank_in_month")
    print(f"\nTop 3 highest-risk banks for the most recent month ({latest_period}):")
    for _, row in latest_top3.iterrows():
        print(f"  #{row['risk_rank_in_month']}  {row['bank_name']}  (Impact Score: {row['impact_score']})")
