from datetime import datetime
from pathlib import Path

import pandas as pd

from db import get_bulk_load_connection, init_db

RAW_DIR = Path(__file__).parent / "data" / "raw"


def already_loaded(conn, filename: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM load_manifest WHERE filename = ?", (filename,)
    ).fetchone()
    return row is not None


def mark_loaded(conn, filename: str, rows: int) -> None:
    conn.execute(
        "INSERT INTO load_manifest (filename, rows_loaded, loaded_at) VALUES (?, ?, ?)",
        (filename, rows, datetime.utcnow().isoformat()),
    )
    conn.commit()


def load_reference_data(conn) -> None:
    for filename, table in [
        ("departments.csv", "departments"),
        ("aisles.csv", "aisles"),
        ("products.csv", "products"),
    ]:
        if already_loaded(conn, filename):
            print(f"Skipping {filename} — already loaded.")
            continue

        df = pd.read_csv(RAW_DIR / filename)
        df.to_sql(table, conn, if_exists="append", index=False)
        mark_loaded(conn, filename, len(df))
        print(f"Loaded {len(df)} rows from {filename} into {table}")


def load_orders(conn, orders_file: str = "orders.csv") -> None:
    if already_loaded(conn, orders_file):
        print(f"Skipping {orders_file} — already loaded.")
        return

    print(f"Loading {orders_file} into source_orders...")
    orders = pd.read_csv(RAW_DIR / orders_file)

    # 'test' orders have no order_products data anywhere in the dataset
    # (they're Kaggle's holdout set) — drop them, keep 'prior' + 'train'.
    before = len(orders)
    orders = orders[orders["eval_set"] != "test"]
    print(f"  {before:,} total rows -> {len(orders):,} after dropping eval_set='test'")

    orders.to_sql("source_orders", conn, if_exists="append", index=False)
    mark_loaded(conn, orders_file, len(orders))
    print(f"  {len(orders):,} orders loaded into source_orders")


def load_order_items(conn, order_products_file: str, chunksize: int = 200_000) -> None:
    if already_loaded(conn, order_products_file):
        print(f"Skipping {order_products_file} — already loaded.")
        return

    print(f"Loading {order_products_file} into source_order_items (chunked)...")
    total = 0
    for chunk in pd.read_csv(RAW_DIR / order_products_file, chunksize=chunksize):
        chunk.to_sql("source_order_items", conn, if_exists="append", index=False)
        total += len(chunk)
        print(f"  ...{total:,} rows loaded")

    mark_loaded(conn, order_products_file, total)
    print(f"{order_products_file} done: {total:,} rows total.")


if __name__ == "__main__":
    init_db()
    conn = get_bulk_load_connection()

    load_reference_data(conn)
    load_orders(conn, orders_file="orders.csv")

    # Both files share the same schema and load into the same table —
    # together they cover every order kept in source_orders (prior + train).
    load_order_items(conn, "order_products__prior.csv")
    load_order_items(conn, "order_products__train.csv")

    conn.close()
    print("Load complete.")
