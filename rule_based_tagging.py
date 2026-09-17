"""
Rule-based product tagging — a fast, zero-LLM alternative to llm_feature_engineering.py.

Why this exists: household_orientation and convenience are largely determined by which
aisle a product sits in. Instacart has only 134 aisles, so mapping those 134 once is far
cheaper than asking an LLM about 49,688 products — and on CPU-only hardware it's the
difference between seconds and days.

Trade-off to state honestly in review: this is deterministic and transparent (every tag
can be traced to a rule), but it can't read nuance in an unusual product name the way an
LLM can. Use validate_sample() to measure how often the rules disagree with the LLM on a
small sample, so you can report a real agreement rate rather than assert the rules are fine.
"""

import re
from datetime import datetime

from db import get_connection, init_db

# ---------------------------------------------------------------------------
# convenience: ready_to_eat = no/minimal preparation | raw_ingredients = needs cooking/prep
# Mapped from the real 134-row aisles.csv. Non-food aisles (household, personal care,
# pets, baby care) are 'ready_to_eat' by the literal definition — they need no preparation.
# ---------------------------------------------------------------------------
RAW_INGREDIENT_AISLES = {
    5,   # marinades meat preparation
    7,   # packaged meat
    9,   # pasta sauce
    12,  # fresh pasta
    14,  # tofu meat alternatives
    15,  # packaged seafood
    16,  # fresh herbs
    17,  # baking ingredients
    18,  # bulk dried fruits vegetables
    19,  # oils vinegars
    34,  # frozen meat seafood
    35,  # poultry counter
    36,  # butter
    39,  # seafood counter
    49,  # packaged poultry
    53,  # cream
    58,  # frozen breads doughs
    63,  # grains rice dried goods
    68,  # bulk grains rice dried goods
    69,  # soup broth bouillon
    83,  # fresh vegetables
    86,  # eggs
    97,  # baking supplies decor
    104, # spices seasonings
    105, # doughs gelatins bake mixes
    106, # hot dogs bacon sausage
    116, # frozen produce
    122, # meat counter
    130, # hot cereal pancake mixes
    131, # dry pasta
}

# Non-food aisles. These get 'ready_to_eat' because they literally require no preparation,
# but that tag is meaningless for them — they're reported separately below so the overall
# convenience split can be read honestly rather than looking misleadingly ready-to-eat-heavy.
NON_FOOD_AISLES = {
    10, 11, 20, 22, 25, 40, 41, 44, 47, 54, 55, 56, 60, 70, 73, 74, 75, 80,
    82, 85, 87, 101, 102, 109, 111, 114, 118, 126, 127, 132, 133,
}

# ---------------------------------------------------------------------------
# household_orientation: aisles that strongly imply a multi-person household
# ---------------------------------------------------------------------------
FAMILY_AISLES = {
    82,  # baby accessories
    92,  # baby food formula
    102, # baby bath body care
    56,  # diapers wipes
    40,  # dog food care
    41,  # cat food care
    54,  # paper goods
    60,  # trash bags liners
    75,  # laundry
    87,  # more household
}

FAMILY_NAME_PATTERNS = re.compile(
    r"\b(family\s*size|value\s*pack|party\s*size|bulk|multi[\s-]*pack|"
    r"club\s*pack|jumbo|mega|\d{2,}\s*(ct|count|pack|pk))\b",
    re.IGNORECASE,
)

SOLO_NAME_PATTERNS = re.compile(
    r"\b(single\s*serve|single[\s-]*serving|individual|personal\s*size|"
    r"snack\s*size|mini|one\s*cup|k[\s-]*cup|for\s*one|serves\s*1|1\s*(ct|count|pack))\b",
    re.IGNORECASE,
)


def classify_convenience(aisle_id: int) -> str:
    return "raw_ingredients" if aisle_id in RAW_INGREDIENT_AISLES else "ready_to_eat"


def classify_household(name: str, aisle_id: int, avg_basket_size: float, family_threshold: float) -> str:
    """Precedence: explicit name signal > aisle signal > purchase-behaviour signal.
    Name wins because 'Family Size' on the label is the most direct evidence there is."""
    if SOLO_NAME_PATTERNS.search(name):
        return "solo"
    if FAMILY_NAME_PATTERNS.search(name):
        return "family"
    if aisle_id in FAMILY_AISLES:
        return "family"
    # Fall back to behaviour: products bought inside larger baskets lean household/stock-up.
    return "family" if avg_basket_size >= family_threshold else "solo"


def get_family_threshold(conn) -> float:
    """Median avg_basket_size_when_purchased — a data-driven split point rather than a
    number picked out of the air. Falls back to a neutral default if stats are missing."""
    row = conn.execute(
        """SELECT avg_basket_size_when_purchased
           FROM product_stats
           WHERE avg_basket_size_when_purchased > 0
           ORDER BY avg_basket_size_when_purchased
           LIMIT 1
           OFFSET (SELECT COUNT(*) / 2 FROM product_stats WHERE avg_basket_size_when_purchased > 0)"""
    ).fetchone()
    return row[0] if row else 10.0


def run(overwrite: bool = False) -> None:
    init_db()
    conn = get_connection()

    stats_count = conn.execute("SELECT COUNT(*) FROM product_stats").fetchone()[0]
    if stats_count == 0:
        print(
            "WARNING: product_stats is empty, so the family/solo fallback has no purchase\n"
            "         signal to work with and nearly everything will be tagged 'solo'.\n"
            "         Run `python compute_product_stats.py` first, then re-run this.\n"
        )

    threshold = get_family_threshold(conn)
    print(f"Using median basket-size threshold of {threshold:.1f} for the family/solo fallback.")

    where_clause = "" if overwrite else "WHERE pt.product_id IS NULL"
    products = conn.execute(
        f"""SELECT p.product_id, p.product_name, p.aisle_id,
                   COALESCE(ps.avg_basket_size_when_purchased, 0.0)
            FROM products p
            LEFT JOIN product_tags pt ON p.product_id = pt.product_id
            LEFT JOIN product_stats ps ON p.product_id = ps.product_id
            {where_clause}"""
    ).fetchall()

    if not products:
        print("Nothing to tag. Pass overwrite=True to re-tag everything.")
        conn.close()
        return

    now = datetime.utcnow().isoformat()
    rows = [
        (
            pid,
            classify_household(name, aisle_id, avg_basket, threshold),
            classify_convenience(aisle_id),
            now,
        )
        for pid, name, aisle_id, avg_basket in products
    ]

    conn.executemany(
        """INSERT INTO product_tags (product_id, household_orientation, convenience, tagged_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(product_id) DO UPDATE SET
               household_orientation = excluded.household_orientation,
               convenience = excluded.convenience,
               tagged_at = excluded.tagged_at""",
        rows,
    )
    conn.commit()

    print(f"Tagged {len(rows):,} products.")
    for column in ("household_orientation", "convenience"):
        print(f"\n{column} distribution:")
        for value, count in conn.execute(
            f"SELECT {column}, COUNT(*) FROM product_tags GROUP BY {column} ORDER BY COUNT(*) DESC"
        ):
            print(f"  {value}: {count:,} ({count / len(rows):.1%})")

    non_food = conn.execute(
        f"""SELECT COUNT(*) FROM products
            WHERE aisle_id IN ({','.join(str(a) for a in NON_FOOD_AISLES)})"""
    ).fetchone()[0]
    print(
        f"\nNote: {non_food:,} ({non_food / len(rows):.1%}) of products sit in non-food aisles\n"
        f"(household, personal care, pets, baby care). They count as 'ready_to_eat' because\n"
        f"they need no preparation, which inflates that share — read the split with that in mind."
    )

    conn.close()


def validate_sample(n: int = 50) -> None:
    """Optional: compare rule output against the LLM on a small random sample, so you can
    report a real agreement rate in your review instead of assuming the rules are correct.
    Needs Ollama running — but only for n products, not 49,688."""
    import llm_feature_engineering as lfe

    conn = get_connection()
    threshold = get_family_threshold(conn)
    sample = conn.execute(
        """SELECT p.product_id, p.product_name, p.aisle_id,
                  COALESCE(ps.purchase_count, 0),
                  COALESCE(ps.reorder_rate, 0.0),
                  COALESCE(ps.avg_basket_size_when_purchased, 0.0)
           FROM products p
           LEFT JOIN product_stats ps ON p.product_id = ps.product_id
           ORDER BY RANDOM() LIMIT ?""",
        (n,),
    ).fetchall()

    agree_h = agree_c = compared = 0
    for pid, name, aisle_id, count, rr, avg_basket in sample:
        llm_result = lfe.tag_batch([(pid, name, count, rr, avg_basket)])
        if not llm_result:
            continue
        llm_tag = llm_result[0]
        rule_h = classify_household(name, aisle_id, avg_basket, threshold)
        rule_c = classify_convenience(aisle_id)

        compared += 1
        agree_h += rule_h == llm_tag.get("household_orientation")
        agree_c += rule_c == llm_tag.get("convenience")
        print(f"{name[:45]:45} | rules: {rule_h:6}/{rule_c:15} | llm: "
              f"{llm_tag.get('household_orientation','?'):6}/{llm_tag.get('convenience','?')}")

    if compared:
        print(f"\nAgreement over {compared} products — "
              f"household_orientation: {agree_h / compared:.0%}, convenience: {agree_c / compared:.0%}")
    conn.close()


if __name__ == "__main__":
    run(overwrite=True)
