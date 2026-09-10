import json
import sqlite3
import time
from datetime import datetime

import ollama

from db import get_connection, init_db

MODEL = "llama3.2"       # pull first: `ollama pull llama3.2`
BATCH_SIZE = 15          # products per LLM call — cuts call count ~15x vs one-at-a-time
MAX_RETRIES = 3

PROMPT_TEMPLATE = """You are tagging grocery products for a retail analytics system.
For each product below, classify it on two dimensions:

1. household_orientation: "solo" (single-person consumption, e.g. single-serve, personal care)
   or "family" (household/shared consumption, e.g. bulk packs, family-size)
2. convenience: "ready_to_eat" (needs no/minimal preparation, e.g. snacks, prepared meals)
   or "raw_ingredients" (needs cooking/preparation, e.g. raw produce, flour, meat)

Products (id: name):
{product_list}

Respond with ONLY a JSON array, one object per product, no other text:
[{{"product_id": <id>, "household_orientation": "<solo|family>", "convenience": "<ready_to_eat|raw_ingredients>"}}, ...]
"""


def get_untagged_products(conn, limit: int) -> list[tuple[int, str]]:
    return conn.execute(
        """SELECT p.product_id, p.product_name
           FROM products p
           LEFT JOIN product_tags pt ON p.product_id = pt.product_id
           WHERE pt.product_id IS NULL
           LIMIT ?""",
        (limit,),
    ).fetchall()


def tag_batch(batch: list[tuple[int, str]]) -> list[dict]:
    product_list = "\n".join(f"{pid}: {name}" for pid, name in batch)
    prompt = PROMPT_TEMPLATE.format(product_list=product_list)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                format="json",
            )
            content = response["message"]["content"]
            parsed = json.loads(content)
            # Ollama's json format sometimes wraps the array in a key — handle both shapes
            if isinstance(parsed, dict):
                parsed = next((v for v in parsed.values() if isinstance(v, list)), [])
            return parsed
        except (json.JSONDecodeError, KeyError, Exception) as e:
            print(f"  attempt {attempt}/{MAX_RETRIES} failed: {e}")
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


def run(limit_products: int | None = None) -> None:
    init_db()
    conn = get_connection()

    total_tagged = 0
    while True:
        remaining_cap = BATCH_SIZE if limit_products is None else min(BATCH_SIZE, limit_products - total_tagged)
        if remaining_cap <= 0:
            break

        batch = get_untagged_products(conn, remaining_cap)
        if not batch:
            print("All products tagged.")
            break

        print(f"Tagging batch of {len(batch)} products (total tagged so far: {total_tagged})...")
        tags = tag_batch(batch)
        saved = save_tags(conn, tags, datetime.utcnow().isoformat())
        total_tagged += saved

        if saved < len(batch):
            print(f"  warning: only {saved}/{len(batch)} products in this batch were tagged successfully")

    print(f"Done. {total_tagged} products tagged this run.")
    conn.close()


if __name__ == "__main__":
    # For a first test run, cap it: run(limit_products=200)
    run()
