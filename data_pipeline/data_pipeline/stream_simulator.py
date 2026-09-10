import random
import time
from datetime import datetime

from db import get_connection

DELAY_SECONDS = 0.05  # only used in "live" mode
RANDOM_SEED = 42
MODE = "bulk"          # "bulk" = push everything at once (fast); "live" = one order at a time with delay


def get_shuffled_order_ids(conn) -> list[int]:
    ids = [r[0] for r in conn.execute("SELECT order_id FROM source_orders ORDER BY order_id")]
    random.Random(RANDOM_SEED).shuffle(ids)
    return ids


def get_cursor(conn) -> int:
    return conn.execute("SELECT last_order_index FROM stream_state WHERE id = 1").fetchone()[0]


def set_cursor(conn, idx: int) -> None:
    conn.execute("UPDATE stream_state SET last_order_index = ? WHERE id = 1", (idx,))
    conn.commit()


def fetch_source_order(conn, order_id: int):
    return conn.execute(
        """SELECT order_id, user_id, order_number, order_dow,
                  order_hour_of_day, days_since_prior_order
           FROM source_orders WHERE order_id = ?""",
        (order_id,),
    ).fetchone()


def ingest_order(conn, order_row) -> tuple[int, int, float]:
    order_id, user_id, order_number, order_dow, order_hour, days_since_prior = order_row
    now = datetime.utcnow().isoformat()
    cur = conn.cursor()

    cur.execute(
        """INSERT INTO live_orders
           (order_id, user_id, order_number, order_dow, order_hour_of_day,
            days_since_prior_order, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (order_id, user_id, order_number, order_dow, order_hour, days_since_prior, now),
    )

    cur.execute(
        """INSERT INTO live_order_items (order_id, product_id, add_to_cart_order, reordered)
           SELECT order_id, product_id, add_to_cart_order, reordered
           FROM source_order_items WHERE order_id = ?""",
        (order_id,),
    )

    basket_size, reorder_ratio = cur.execute(
        "SELECT COUNT(*), AVG(reordered) FROM live_order_items WHERE order_id = ?",
        (order_id,),
    ).fetchone()

    conn.commit()
    return user_id, basket_size, reorder_ratio or 0.0


def update_customer_features(conn, user_id: int) -> None:
    total_orders, total_items, reorder_rate, avg_days, last_order_id = conn.execute(
        """SELECT COUNT(DISTINCT lo.order_id),
                  COUNT(li.product_id),
                  AVG(li.reordered),
                  AVG(lo.days_since_prior_order),
                  MAX(lo.order_id)
           FROM live_orders lo
           JOIN live_order_items li ON lo.order_id = li.order_id
           WHERE lo.user_id = ?""",
        (user_id,),
    ).fetchone()

    avg_basket_size = total_items / total_orders if total_orders else 0
    now = datetime.utcnow().isoformat()

    conn.execute(
        """INSERT INTO customer_features
               (user_id, total_orders, total_items, avg_basket_size, reorder_rate,
                avg_days_between_orders, last_order_id, last_ingested_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               total_orders = excluded.total_orders,
               total_items = excluded.total_items,
               avg_basket_size = excluded.avg_basket_size,
               reorder_rate = excluded.reorder_rate,
               avg_days_between_orders = excluded.avg_days_between_orders,
               last_order_id = excluded.last_order_id,
               last_ingested_at = excluded.last_ingested_at,
               updated_at = excluded.updated_at""",
        (user_id, total_orders, total_items, avg_basket_size, reorder_rate or 0,
         avg_days or 0, last_order_id, now, now),
    )
    conn.commit()


def run() -> None:
    conn = get_connection()
    order_ids = get_shuffled_order_ids(conn)
    total = len(order_ids)
    idx = get_cursor(conn)

    print(f"Live stream simulator started at position {idx}/{total}. Ctrl+C to stop.")

    try:
        while idx < total:
            order_id = order_ids[idx]
            order_row = fetch_source_order(conn, order_id)

            user_id, basket_size, reorder_ratio = ingest_order(conn, order_row)
            update_customer_features(conn, user_id)
            idx += 1
            set_cursor(conn, idx)

            print(
                f"[{datetime.utcnow().strftime('%H:%M:%S')}] "
                f"order {order_id} ingested — user {user_id}, "
                f"basket size {basket_size}, reorder ratio {reorder_ratio:.2f} "
                f"| progress {idx}/{total}"
            )
            time.sleep(DELAY_SECONDS)

        print("All historical orders ingested. Stream exhausted.")
    except KeyboardInterrupt:
        print(f"\nStopped at position {idx}/{total}. Cursor saved — rerun to resume.")
    finally:
        conn.close()


def run_bulk() -> None:
    """Push every not-yet-ingested order into the live tables in one shot,
    then recompute customer_features for every affected user in a single
    aggregate query — seconds/minutes instead of hours."""
    conn = get_connection()
    print("Bulk ingest started...")

    cur = conn.cursor()

    cur.execute(
        """INSERT INTO live_orders
               (order_id, user_id, order_number, order_dow, order_hour_of_day,
                days_since_prior_order, ingested_at)
           SELECT order_id, user_id, order_number, order_dow, order_hour_of_day,
                  days_since_prior_order, datetime('now')
           FROM source_orders
           WHERE order_id NOT IN (SELECT order_id FROM live_orders)"""
    )
    print(f"  {cur.rowcount:,} orders copied into live_orders")

    cur.execute(
        """INSERT INTO live_order_items (order_id, product_id, add_to_cart_order, reordered)
           SELECT soi.order_id, soi.product_id, soi.add_to_cart_order, soi.reordered
           FROM source_order_items soi
           WHERE soi.order_id IN (SELECT order_id FROM live_orders)
             AND soi.order_id NOT IN (SELECT DISTINCT order_id FROM live_order_items)"""
    )
    print(f"  {cur.rowcount:,} item rows copied into live_order_items")
    conn.commit()

    print("Recomputing customer_features for all users...")
    cur.execute(
        """INSERT INTO customer_features
               (user_id, total_orders, total_items, avg_basket_size, reorder_rate,
                avg_days_between_orders, last_order_id, last_ingested_at, updated_at)
           SELECT lo.user_id,
                  COUNT(DISTINCT lo.order_id),
                  COUNT(li.product_id),
                  CAST(COUNT(li.product_id) AS REAL) / COUNT(DISTINCT lo.order_id),
                  AVG(li.reordered),
                  AVG(lo.days_since_prior_order),
                  MAX(lo.order_id),
                  datetime('now'),
                  datetime('now')
           FROM live_orders lo
           JOIN live_order_items li ON lo.order_id = li.order_id
           GROUP BY lo.user_id
           ON CONFLICT(user_id) DO UPDATE SET
               total_orders = excluded.total_orders,
               total_items = excluded.total_items,
               avg_basket_size = excluded.avg_basket_size,
               reorder_rate = excluded.reorder_rate,
               avg_days_between_orders = excluded.avg_days_between_orders,
               last_order_id = excluded.last_order_id,
               last_ingested_at = excluded.last_ingested_at,
               updated_at = excluded.updated_at"""
    )
    conn.commit()

    total_orders = get_shuffled_order_ids(conn)
    set_cursor(conn, len(total_orders))  # mark everything as ingested for "live" mode too

    print("Bulk ingest complete.")
    conn.close()


if __name__ == "__main__":
    if MODE == "bulk":
        run_bulk()
    else:
        run()
