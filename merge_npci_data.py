import pandas as pd
import glob
import re
import os

INPUT_FOLDER = "."   # folder containing the .xlsx files
OUTPUT_FILE = "npci_upi_remitter_merged_2024_2026.csv"

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

CANONICAL_COLUMNS = [
    "sr_no", "bank_name", "total_volume_mn", "approved_pct",
    "bd_pct", "td_pct", "total_debit_reversal_count_mn", "debit_reversal_success_pct"
]


def clean_percent(value):
    """Handles both '94.10%' strings and raw decimals like 0.7906 - returns a 0-100 scale float."""
    if pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip().replace("%", "")
        try:
            return float(value)
        except ValueError:
            return None
    val = float(value)
    # If it's a fraction (e.g. 0.79 meaning 79%), scale it up. Real % values from this
    # source are always > 1 when already in percent form, so this threshold is safe.
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
    """Extracts month/year from the sheet's own title row, e.g. \"...(Apr'24)\" -> ('Apr', 2024)."""
    m = re.search(r"\((\w{3})'(\d{2})\)", str(title_text))
    if not m:
        return None, None
    month_str, year_str = m.group(1), m.group(2)
    return month_str, 2000 + int(year_str)


def process_file(filepath):
    raw = pd.read_excel(filepath, header=None)
    title_text = raw.iloc[0, 0]
    month_str, year = extract_month_year_from_title(title_text)

    if month_str is None:
        print(f"  WARNING: could not parse month/year from title in {filepath} -> '{title_text}'. Skipping file.")
        return None

    month_num = MONTH_MAP.get(month_str)

    # Row 1 (index 1) is the real header; data starts at index 2
    data = raw.iloc[2:].copy()
    data.columns = CANONICAL_COLUMNS[:data.shape[1]]
    data = data.dropna(how="all")

    # Clean fields
    data["bank_name"] = data["bank_name"].astype(str).str.strip().str.upper()
    data["bank_name"] = data["bank_name"].str.replace(r"\s+", " ", regex=True)  # collapse multiple spaces
    data["bank_name"] = data["bank_name"].str.replace(r"\.$", "", regex=True)   # trailing periods e.g. "AXIS BANK LTD."

    data["total_volume_mn"] = data["total_volume_mn"].apply(clean_number)
    data["approved_pct"] = data["approved_pct"].apply(clean_percent)
    data["bd_pct"] = data["bd_pct"].apply(clean_percent)
    data["td_pct"] = data["td_pct"].apply(clean_percent)
    data["total_debit_reversal_count_mn"] = data["total_debit_reversal_count_mn"].apply(clean_number)
    data["debit_reversal_success_pct"] = data["debit_reversal_success_pct"].apply(clean_percent)

    data["month"] = month_str
    data["month_num"] = month_num
    data["year"] = year
    data["period"] = f"{year}-{month_num:02d}"
    data["source_file"] = os.path.basename(filepath)

    # Drop the sr_no column from source (rank can shift month to month, recompute if needed) - keep bank_name onward
    data = data.drop(columns=["sr_no"])
    data = data.dropna(subset=["bank_name"])
    data = data[data["bank_name"] != "NAN"]

    return data


def main():
    files = sorted(glob.glob(os.path.join(INPUT_FOLDER, "*Remitter.xlsx")))
    print(f"Found {len(files)} files to process.\n")

    all_data = []
    seen_content_signatures = {}  # maps a content hash -> first filename that had it
    skipped_files = []

    for f in files:
        df = process_file(f)
        if df is None:
            continue

        # Build a content signature from the actual bank/volume/bd/td values,
        # independent of whatever period label the title claimed
        sig = tuple(
            df.sort_values("bank_name")[["bank_name", "total_volume_mn", "bd_pct", "td_pct"]]
            .itertuples(index=False, name=None)
        )

        if sig in seen_content_signatures:
            original = seen_content_signatures[sig]
            print(f"  SKIPPING {os.path.basename(f)}: identical data content to '{original}' "
                  f"(title claimed period {df['period'].iloc[0]}, but NPCI had not published this "
                  f"month yet and served repeat data - re-download this month later)")
            skipped_files.append((os.path.basename(f), df["period"].iloc[0]))
            continue

        seen_content_signatures[sig] = os.path.basename(f)
        all_data.append(df)
        print(f"  Processed {os.path.basename(f):70s} -> {df['period'].iloc[0]} ({len(df)} banks)")

    merged = pd.concat(all_data, ignore_index=True)

    # Sort chronologically, then by volume within each month
    merged = merged.sort_values(["year", "month_num", "total_volume_mn"], ascending=[True, True, False])
    merged = merged.reset_index(drop=True)

    merged.to_csv(OUTPUT_FILE, index=False)

    print(f"\nDone. Merged {len(files)} files -> {len(merged)} total rows ({len(all_data)} valid months).")
    print(f"Saved to: {OUTPUT_FILE}")
    print(f"\nDistinct periods covered: {sorted(merged['period'].unique())}")
    print(f"Distinct bank names found: {merged['bank_name'].nunique()}")
    if skipped_files:
        print(f"\n*** ACTION NEEDED: these files were unpublished repeats - re-download once NPCI posts them:")
        for fname, period in skipped_files:
            print(f"    {fname}  (claimed period: {period})")


if __name__ == "__main__":
    main()
