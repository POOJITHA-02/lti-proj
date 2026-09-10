# -*- coding: utf-8 -*-
"""
NPCI UPI Remitter Data Ingestion Pipeline (single-file version)
------------------------------------------------------------------
Drop new monthly .xlsx files (as downloaded from npci.org.in) into the
'incoming' folder next to this script, then run:

    python npci_pipeline.py

It will:
  1. Clean and standardize each file (fixing column-name drift, mixed
     percent formats, and trusting the in-file title over the filename)
  2. Check each file's actual DATA content against everything already
     ingested - so if NPCI serves the same data twice under different
     month labels, it's skipped, not double-counted
  3. Insert genuinely new data into the SQLite database (npci_upi.db)
  4. Move successfully processed files into 'processed/' so re-running
     this script is always safe and never reprocesses old files

Run "python npci_pipeline.py status" anytime to see a summary without
ingesting anything new.
"""

import os
import re
import sys
import glob
import shutil
import hashlib
import sqlite3
from contextlib import contextmanager

import pandas as pd

# ============================================================
# CONFIG
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INCOMING_FOLDER = os.path.join(BASE_DIR, "incoming")
PROCESSED_FOLDER = os.path.join(BASE_DIR, "processed")
DB_PATH = os.path.join(BASE_DIR, "npci_upi.db")

CANONICAL_COLUMNS = [
    "sr_no", "bank_name", "total_volume_mn", "approved_pct",
    "bd_pct", "td_pct", "total_debit_reversal_count_mn", "debit_reversal_success_pct"
]

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}


# ============================================================
# CLEANING FUNCTIONS
# ============================================================
def clean_percent(value):
    """Handles both '94.10%' strings and raw decimals like 0.7906 - returns a 0-100 scale float."""
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "")
        try:
            value = float(value)
        except ValueError:
            return None
    val = float(value)
    if val <= 1.5:
        val = val * 100
    return round(val, 2)


def clean_number(value):
    """Handles '-' or blank as missing, strips commas, returns float."""
    if pd.isna(value):
        return None
    if isinstance(value, str):
        v = value.strip().replace(",", "")
        if v in ("-", "", "NaN"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return float(value)


def extract_month_year_from_title(title_text):
    """Extracts month/year from the sheet's own title row, e.g. "...(Apr'24)" -> ('Apr', 2024).
    Used INSTEAD of the filename, since NPCI filenames have been observed to disagree
    with the actual month printed inside the file."""
    m = re.search(r"\((\w{3})'(\d{2})\)", str(title_text))
    if not m:
        return None, None
    month_str, year_str = m.group(1), m.group(2)
    return month_str, 2000 + int(year_str)


def process_file(filepath):
    """Reads one raw NPCI 'Top 50 Remitter' Excel file and returns a cleaned DataFrame,
    or None if the file's title can't be parsed."""
    raw = pd.read_excel(filepath, header=None)
    title_text = raw.iloc[0, 0]
    month_str, year = extract_month_year_from_title(title_text)

    if month_str is None:
        return None

    month_num = MONTH_MAP.get(month_str)

    data = raw.iloc[2:].copy()
    data.columns = CANONICAL_COLUMNS[:data.shape[1]]
    data = data.dropna(how="all")

    data["bank_name"] = data["bank_name"].astype(str).str.strip().str.upper()
    data["bank_name"] = data["bank_name"].str.replace(r"\s+", " ", regex=True)
    data["bank_name"] = data["bank_name"].str.replace(r"\.$", "", regex=True)

    data["total_volume_mn"] = data["total_volume_mn"].apply(clean_number)
    data["approved_pct"] = data["approved_pct"].apply(clean_percent)
    data["bd_pct"] = data["bd_pct"].apply(clean_percent)
    data["td_pct"] = data["td_pct"].apply(clean_percent)
    data["total_debit_reversal_count_mn"] = data["total_debit_reversal_count_mn"].apply(clean_number)
    data["debit_reversal_success_pct"] = data["debit_reversal_success_pct"].apply(clean_percent)

    data["month"] = month_str
    data["month_num"] = month_num
    data["year"] = year
    data["period"] = "%d-%02d" % (year, month_num)
    data["source_file"] = os.path.basename(filepath)

    data = data.drop(columns=["sr_no"])
    data = data.dropna(subset=["bank_name"])
    data = data[data["bank_name"] != "NAN"]

    return data


def content_signature(df):
    """Builds a hashable signature of the actual data values (bank/volume/bd/td),
    independent of whatever period label the file's title claims."""
    subset = df.sort_values("bank_name")[["bank_name", "total_volume_mn", "bd_pct", "td_pct"]]
    return tuple(subset.itertuples(index=False, name=None))


def hash_signature(sig):
    return hashlib.sha256(str(sig).encode()).hexdigest()


# ============================================================
# DATABASE FUNCTIONS
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS upi_remitter_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bank_name TEXT NOT NULL,
            total_volume_mn REAL,
            approved_pct REAL,
            bd_pct REAL,
            td_pct REAL,
            total_debit_reversal_count_mn REAL,
            debit_reversal_success_pct REAL,
            month TEXT,
            month_num INTEGER,
            year INTEGER,
            period TEXT,
            source_file TEXT,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            claimed_period TEXT,
            status TEXT NOT NULL,
            row_count INTEGER,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_known_content_hashes():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT content_hash FROM ingestion_log WHERE status = 'ingested'")
        return {row["content_hash"] for row in cur.fetchall()}


def insert_stats(df):
    with get_db() as conn:
        df.to_sql("upi_remitter_stats", conn, if_exists="append", index=False)


def log_ingestion(source_file, content_hash, claimed_period, status, row_count):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO ingestion_log (source_file, content_hash, claimed_period, status, row_count)
               VALUES (?, ?, ?, ?, ?)""",
            (source_file, content_hash, claimed_period, status, row_count),
        )
        conn.commit()


# ============================================================
# MAIN PIPELINE
# ============================================================
def run_pipeline():
    os.makedirs(INCOMING_FOLDER, exist_ok=True)
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)

    init_db()
    known_hashes = get_known_content_hashes()

    files = sorted(glob.glob(os.path.join(INCOMING_FOLDER, "*.xlsx")))

    if not files:
        print("No new files found in %s/. Nothing to do." % INCOMING_FOLDER)
        return

    print("Found %d file(s) to process.\n" % len(files))

    ingested_count = 0
    skipped_count = 0

    for filepath in files:
        fname = os.path.basename(filepath)
        df = process_file(filepath)

        if df is None:
            print("  SKIPPED (unparseable title/format): %s" % fname)
            log_ingestion(fname, "N/A", "N/A", "skipped_unparseable", 0)
            shutil.move(filepath, os.path.join(PROCESSED_FOLDER, fname))
            continue

        claimed_period = df["period"].iloc[0]
        sig = content_signature(df)
        sig_hash = hash_signature(sig)

        if sig_hash in known_hashes:
            print("  SKIPPED (duplicate content - already ingested under a different name/label): %s "
                  "(claimed period: %s)" % (fname, claimed_period))
            log_ingestion(fname, sig_hash, claimed_period, "skipped_duplicate", 0)
            skipped_count += 1
        else:
            insert_stats(df)
            log_ingestion(fname, sig_hash, claimed_period, "ingested", len(df))
            known_hashes.add(sig_hash)
            print("  INGESTED: %s -> period %s (%d banks)" % (fname, claimed_period, len(df)))
            ingested_count += 1

        shutil.move(filepath, os.path.join(PROCESSED_FOLDER, fname))

    print("\nDone. %d file(s) ingested, %d duplicate(s) skipped." % (ingested_count, skipped_count))
    print("Database: %s" % DB_PATH)
    print("Open this file in DB Browser for SQLite to inspect the data anytime "
          "(close DB Browser before re-running this script, so it isn't locked).")


def show_status():
    if not os.path.exists(DB_PATH):
        print("No database found yet at %s. Run the pipeline first (no arguments)." % DB_PATH)
        return

    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) as c FROM upi_remitter_stats")
        total_rows = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(DISTINCT period) as c FROM upi_remitter_stats")
        total_periods = cur.fetchone()["c"]

        cur.execute("SELECT MIN(period) as mn, MAX(period) as mx FROM upi_remitter_stats")
        row = cur.fetchone()
        date_range = "%s to %s" % (row["mn"], row["mx"])

        cur.execute("SELECT DISTINCT period FROM upi_remitter_stats ORDER BY period")
        periods = [r["period"] for r in cur.fetchall()]

        cur.execute("SELECT source_file, status, claimed_period, processed_at FROM ingestion_log ORDER BY id DESC LIMIT 10")
        recent_log = cur.fetchall()

        print("=" * 60)
        print("NPCI UPI DATABASE STATUS")
        print("=" * 60)
        print("Total rows:          %d" % total_rows)
        print("Distinct months:     %d" % total_periods)
        print("Date range covered:  %s" % date_range)
        print("\nAll periods present: %s" % periods)
        print("\nLast 10 ingestion log entries:")
        for r in recent_log:
            print("  [%s] %-65s -> %s (claimed %s)" % (r["processed_at"], r["source_file"], r["status"], r["claimed_period"]))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        show_status()
    else:
        run_pipeline()
