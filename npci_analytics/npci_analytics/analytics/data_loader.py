import sqlite3
import pandas as pd
from analytics.config import DB_PATH


def load_bank_data():
    """Loads the full cleaned dataset and returns it sorted chronologically."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM upi_remitter_stats", conn)
    conn.close()

    df["date"] = pd.to_datetime(df["period"], format="%Y-%m")
    df = df.sort_values(["bank_name", "date"]).reset_index(drop=True)
    return df


def get_bank_series(df, bank_name, metric):
    """Returns one bank's time series for a given metric, as a clean 2-column frame: date, value."""
    bank_df = df[df["bank_name"] == bank_name][["date", metric]].dropna()
    bank_df = bank_df.sort_values("date").reset_index(drop=True)
    return bank_df


def list_banks_with_enough_history(df, min_months):
    """Returns bank names that have at least `min_months` of non-null target-metric history.
    Real NPCI data has banks entering/exiting the Top 50 list month to month, so not every
    bank has full 29-month coverage - this filters out banks too new/sparse to model."""
    counts = df.groupby("bank_name")["td_pct"].count()
    return counts[counts >= min_months].index.tolist()
