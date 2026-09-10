import pandas as pd

from db import get_connection, init_db


def cluster_overview(conn) -> pd.DataFrame:
    """Basic size and basket-stat profile per cluster."""
    return pd.read_sql(
        """SELECT ss.cluster_label,
                  ss.algorithm,
                  COUNT(*) AS num_shoppers,
                  AVG(cf.avg_basket_size) AS avg_basket_size,
                  AVG(cf.reorder_rate) AS avg_reorder_rate,
                  AVG(cf.total_orders) AS avg_total_orders,
                  SUM(CASE WHEN ss.household_orientation = 'family' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS pct_family,
                  SUM(CASE WHEN ss.shopping_mission = 'stockup' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS pct_stockup,
                  SUM(CASE WHEN ss.purchase_behavior = 'routine' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS pct_routine
           FROM shopper_signatures ss
           JOIN customer_features cf ON ss.user_id = cf.user_id
           GROUP BY ss.cluster_label
           ORDER BY ss.cluster_label""",
        conn,
    )


def department_mix_by_cluster(conn) -> pd.DataFrame:
    """What departments each cluster actually buys from — the core assortment signal."""
    return pd.read_sql(
        """SELECT ss.cluster_label,
                  d.department,
                  COUNT(*) AS items_purchased,
                  COUNT(*) * 1.0 / SUM(COUNT(*)) OVER (PARTITION BY ss.cluster_label) AS share_of_cluster_baskets
           FROM shopper_signatures ss
           JOIN live_orders lo ON ss.user_id = lo.user_id
           JOIN live_order_items li ON lo.order_id = li.order_id
           JOIN products p ON li.product_id = p.product_id
           JOIN departments d ON p.department_id = d.department_id
           GROUP BY ss.cluster_label, d.department
           ORDER BY ss.cluster_label, items_purchased DESC""",
        conn,
    )


def aisle_mix_by_cluster(conn, top_n: int = 5) -> pd.DataFrame:
    """Top aisles per cluster — finer-grained than department, useful for assortment decisions."""
    df = pd.read_sql(
        """SELECT ss.cluster_label,
                  a.aisle,
                  COUNT(*) AS items_purchased
           FROM shopper_signatures ss
           JOIN live_orders lo ON ss.user_id = lo.user_id
           JOIN live_order_items li ON lo.order_id = li.order_id
           JOIN products p ON li.product_id = p.product_id
           JOIN aisles a ON p.aisle_id = a.aisle_id
           GROUP BY ss.cluster_label, a.aisle
           ORDER BY ss.cluster_label, items_purchased DESC""",
        conn,
    )
    return df.groupby("cluster_label").head(top_n).reset_index(drop=True)


def reorder_behavior_by_cluster(conn) -> pd.DataFrame:
    """Reorder ratio at the item level per cluster — complements the user-level reorder_rate average."""
    return pd.read_sql(
        """SELECT ss.cluster_label,
                  AVG(li.reordered) AS item_level_reorder_ratio,
                  COUNT(DISTINCT lo.order_id) AS total_orders_in_cluster
           FROM shopper_signatures ss
           JOIN live_orders lo ON ss.user_id = lo.user_id
           JOIN live_order_items li ON lo.order_id = li.order_id
           GROUP BY ss.cluster_label
           ORDER BY ss.cluster_label""",
        conn,
    )


def pack_size_note() -> str:
    return (
        "NOTE: the Instacart dataset has no structured brand or pack-size fields — "
        "products.csv only has product_name, aisle_id, department_id. Department/aisle "
        "mix above is the available substitute for 'category' comparison. Brand and pack "
        "size would require regex/NLP extraction from product_name text (e.g. matching "
        "'16 oz', '2 pack') — a reasonable follow-up enhancement, not included here since "
        "it wasn't in the original 6 CSVs."
    )


def run() -> None:
    init_db()
    conn = get_connection()

    print("=== Cluster Overview ===")
    overview = cluster_overview(conn)
    print(overview.to_string(index=False))
    overview.to_csv("cluster_overview.csv", index=False)

    print("\n=== Department Mix by Cluster ===")
    dept_mix = department_mix_by_cluster(conn)
    print(dept_mix.to_string(index=False))
    dept_mix.to_csv("department_mix_by_cluster.csv", index=False)

    print("\n=== Top Aisles by Cluster ===")
    aisle_mix = aisle_mix_by_cluster(conn)
    print(aisle_mix.to_string(index=False))
    aisle_mix.to_csv("aisle_mix_by_cluster.csv", index=False)

    print("\n=== Reorder Behavior by Cluster ===")
    reorder = reorder_behavior_by_cluster(conn)
    print(reorder.to_string(index=False))
    reorder.to_csv("reorder_behavior_by_cluster.csv", index=False)

    print(f"\n{pack_size_note()}")

    conn.close()
    print("\nAll 4 CSVs written — these feed directly into the dashboard / Power BI.")


if __name__ == "__main__":
    run()
