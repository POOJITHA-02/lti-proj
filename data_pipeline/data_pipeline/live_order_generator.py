import random
import time
from datetime import datetime

from db import get_connection
from stream_simulator import update_customer_features

DELAY_SECONDS = 2        # gap between simulated new orders arriving
NEW_USER_CHANCE = 0.05   # probability a "buyer" is a brand-new customer, not a returning one


def get_product_pool(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT product_id FROM products")]


def get_next_order_id(conn) -> int:
    max_live = conn.execute("SELECT MAX(order_id) FROM live_orders").fetchone()[0] or 0
    max_source = conn.execute("SELECT MAX(order_id) FROM source_orders").fetchone()[0] or 0
    return max(max_live, max_source) + 1


def pick_user(conn) -> tuple[int, bool]:
    """Returns (user_id, is_new_user)."""
    if random.random() < NEW_USER_CHANCE:
        max_user = conn.execute("SELECT MAX(user_id) FROM customer_features").fetchone()[0] or 0
        max_user_src = conn.execute("SELECT MAX(user_id) FROM source_orders").fetchone()[0] or 0
        return max(max_user, max_user_src) + 1, True

    row = conn.execute("SELECT user_id FROM customer_features ORDER BY RANDOM() LIMIT 1").fetchone()
    if row:
        return row[0], False

    # Fallback if customer_features is still empty (e.g. bulk backfill hasn't run yet)
    row = conn.execute("SELECT DISTINCT user_id FROM source_orders ORDER BY RANDOM() LIMIT 1").fetchone()
    return (row[0] if row else 1), False


def generate_new_order(conn, product_pool: list[int]) -> tuple[int, int, int]:
    order_id = get_next_order_id(conn)
    user_id, is_new_user = pick_user(conn)

    if is_new_user:
        order_number = 1
        days_since_prior = None
    else:
        prev = conn.execute(
            "SELECT total_orders FROM customer_features WHERE user_id = ?", (user_id,)
        ).fetchone()
        order_number = (prev[0] if prev else 0) + 1
        days_since_prior = round(random.uniform(0, 30), 1)

    order_dow = random.randint(0, 6)
    order_hour = random.randint(0, 23)
    now = datetime.utcnow().isoformat()

    conn.execute(
        """INSERT INTO live_orders
               (order_id, user_id, order_number, order_dow, order_hour_of_day,
                days_since_prior_order, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (order_id, user_id, order_number, order_dow, order_hour, days_since_prior, now),
    )

    basket_size = random.randint(1, 15)
    basket = random.sample(product_pool, min(basket_size, len(product_pool)))
    for position, product_id in enumerate(basket, start=1):
        reordered = 0 if is_new_user else random.choices([0, 1], weights=[0.4, 0.6])[0]
        conn.execute(
            """INSERT INTO live_order_items (order_id, product_id, add_to_cart_order, reordered)
               VALUES (?, ?, ?, ?)""",
            (order_id, product_id, position, reordered),
        )

    conn.commit()
    return order_id, user_id, len(basket)


def run() -> None:
    conn = get_connection()
    product_pool = get_product_pool(conn)
    if not product_pool:
        print("No products found — run load_reference_data.py first.")
        return

    print("Live order generator started. Ctrl+C to stop.")
    count = 0
    try:
        while True:
            order_id, user_id, basket_size = generate_new_order(conn, product_pool)
            update_customer_features(conn, user_id)
            count += 1
            print(
                f"[{datetime.utcnow().strftime('%H:%M:%S')}] "
                f"NEW order {order_id} — user {user_id}, basket size {basket_size} "
                f"| total generated this session: {count}"
            )
            time.sleep(DELAY_SECONDS)
    except KeyboardInterrupt:
        print(f"\nStopped. {count} new live orders generated this session.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
