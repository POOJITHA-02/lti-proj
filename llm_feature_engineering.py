import concurrent.futures
import json
import sqlite3
import threading
import time
from datetime import datetime

import ollama

from db import get_connection, init_db

MODEL = "llama3.2"       # pull first: `ollama pull llama3.2`
BATCH_SIZE = 1           # llama3.2 doesn't reliably follow multi-item batch instructions
MAX_RETRIES = 3
MAX_WORKERS = 6          # concurrent Ollama calls — tune based on your CPU/RAM (try 4-10)

SINGLE_ITEM_PROMPT_TEMPLATE = """Classify this grocery product on two dimensions.

Product: {name}
Purchased {count} times | reorder_rate {rr:.2f} | avg_basket_size_when_purchased {abs_:.1f}

1. household_orientation: "solo" or "family" — a high avg_basket_size_when_purchased leans
   "family" (bought as part of a bigger stock-up trip); small/quick baskets lean "solo".
2. convenience: "ready_to_eat" (no/minimal prep needed) or "raw_ingredients" (needs cooking).
   A high reorder_rate on a "raw"-sounding item often signals a routine cooking staple.

Respond with ONLY this JSON object, no other text:
{{"product_id": {pid}, "household_orientation": "<solo|family>", "convenience": "<ready_to_eat|raw_ingredients>"}}
"""

BATCH_PROMPT_TEMPLATE = """You are tagging grocery products for a retail analytics system.
For each product below, classify it on two dimensions, using BOTH the product name
AND the purchase statistics provided — don't rely on the name alone.

1. household_orientation: "solo" (single-person consumption) or "family" (household/shared
   consumption). A high avg_basket_size_when_purchased suggests it's bought as part of a
   larger stock-up trip (leans "family"); products bought in small, quick baskets lean "solo".
2. convenience: "ready_to_eat" (needs no/minimal preparation, e.g. snacks, prepared meals)
   or "raw_ingredients" (needs cooking/preparation, e.g. raw produce, flour, meat). A high
   reorder_rate combined with a "raw"-sounding name often signals a routine cooking staple.

There are exactly {n_products} products listed below. You MUST return exactly {n_products}
objects in your response — one for every single product listed, in the same order, with no
omissions and no extra commentary.

Products (id: name | purchased N times | reorder_rate | avg_basket_size_when_purchased):
{product_list}

Respond with ONLY a JSON array of exactly {n_products} objects, no other text:
[{{"product_id": <id>, "household_orientation": "<solo|family>", "convenience": "<ready_to_eat|raw_ingredients>"}}, ...]
"""


def get_untagged_products(conn, limit: int) -> list[tuple[int, str, int, float, float]]:
    return conn.execute(
        """SELECT p.product_id, p.product_name,
                  COALESCE(ps.purchase_count, 0),
                  COALESCE(ps.reorder_rate, 0.0),
                  COALESCE(ps.avg_basket_size_when_purchased, 0.0)
           FROM products p
           LEFT JOIN product_tags pt ON p.product_id = pt.product_id
           LEFT JOIN product_stats ps ON p.product_id = ps.product_id
           WHERE pt.product_id IS NULL
           LIMIT ?""",
        (limit,),
    ).fetchall()


def tag_batch(batch: list[tuple[int, str, int, float, float]]) -> list[dict]:
    if len(batch) == 1:
        pid, name, count, rr, abs_ = batch[0]
        prompt = SINGLE_ITEM_PROMPT_TEMPLATE.format(pid=pid, name=name, count=count, rr=rr, abs_=abs_)
    else:
        product_list = "\n".join(
            f"{pid}: {name} | purchased {count} times | reorder_rate {rr:.2f} | avg_basket_size_when_purchased {abs_:.1f}"
            for pid, name, count, rr, abs_ in batch
        )
        prompt = BATCH_PROMPT_TEMPLATE.format(product_list=product_list, n_products=len(batch))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
        except Exception as e:
            print(f"  attempt {attempt}/{MAX_RETRIES} — ollama.chat() call failed: {e}")
            time.sleep(1)
            continue

        content = response["message"]["content"]
        try:
            parsed = json.loads(content)

            if isinstance(parsed, dict):
                # Case 1: dict wraps a list under some key, e.g. {"tags": [...]}
                list_value = next((v for v in parsed.values() if isinstance(v, list)), None)
                if list_value is not None:
                    parsed = list_value
                # Case 2: dict IS a single product's tags — the model only answered
                # for one item instead of the whole batch. Treat it as a 1-item list
                # so at least that one product makes progress instead of 0.
                elif "product_id" in parsed:
                    parsed = [parsed]
                else:
                    parsed = []

            if not parsed:
                print(f"  attempt {attempt}/{MAX_RETRIES} — parsed but empty/unexpected shape. Raw response:\n{content}")
                time.sleep(1)
                continue

            if len(parsed) < len(batch):
                print(f"  note: model returned {len(parsed)}/{len(batch)} products — "
                      f"the rest stay untagged and will be retried in a later batch")

            return parsed
        except json.JSONDecodeError as e:
            print(f"  attempt {attempt}/{MAX_RETRIES} — invalid JSON: {e}\nRaw response:\n{content}")
            time.sleep(1)

    return []  # give up on this batch after retries — those products stay untagged, retried next run


def save_tags(conn, tags: list[dict], now: str) -> int:
    saved = 0
    for t in tags:
        try:
            conn.execute(
                """INSERT INTO product_tags (product_id, household_orientation, convenience, tagged_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(product_id) DO UPDATE SET
                       household_orientation = excluded.household_orientation,
                       convenience = excluded.convenience,
                       tagged_at = excluded.tagged_at""",
                (t["product_id"], t["household_orientation"], t["convenience"], now),
            )
            saved += 1
        except (KeyError, sqlite3.Error) as e:
            print(f"  skipped malformed tag {t}: {e}")
    conn.commit()
    return saved


def tag_one(product: tuple[int, str, int, float, float]) -> list[dict]:
    """Runs in a worker thread — just the Ollama call, no DB access, so no locking needed here."""
    return tag_batch([product])


def run(limit_products: int | None = None) -> None:
    init_db()
    db_lock = threading.Lock()
    conn = sqlite3.connect("instacart.db", check_same_thread=False)

    pool_size = limit_products if limit_products is not None else 5000
    products = get_untagged_products(conn, pool_size)

    total_tagged = 0
    print(f"Tagging {len(products)} products with {MAX_WORKERS} concurrent workers...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_product = {executor.submit(tag_one, p): p for p in products}

        for future in concurrent.futures.as_completed(future_to_product):
            tags = future.result()
            with db_lock:
                saved = save_tags(conn, tags, datetime.utcnow().isoformat())
            total_tagged += saved

            if total_tagged % 50 == 0:
                print(f"  ...{total_tagged}/{len(products)} tagged")

    print(f"Done. {total_tagged}/{len(products)} products tagged this run.")

    # If this pool wasn't the full remaining set (limit_products was None and there
    # were more than pool_size left), just rerun the script to keep going.
    remaining = conn.execute(
        "SELECT COUNT(*) FROM products p LEFT JOIN product_tags pt ON p.product_id = pt.product_id WHERE pt.product_id IS NULL"
    ).fetchone()[0]
    if remaining > 0:
        print(f"{remaining} products still untagged — rerun this script to continue.")

    conn.close()


if __name__ == "__main__":
    # For a first test run, cap it: run(limit_products=200)
    run()
