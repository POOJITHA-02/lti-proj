from datetime import datetime

import mlflow
import numpy as np
import pandas as pd
import umap
from sklearn.cluster import DBSCAN, AgglomerativeClustering, KMeans
from sklearn.metrics import davies_bouldin_score, silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from db import get_connection, init_db

# Thresholds for the rule-based dimensions — tune these once you've
# looked at the actual distribution of avg_basket_size / reorder_rate.
STOCKUP_BASKET_THRESHOLD = 15    # avg_basket_size >= this -> "stockup", else "topup"
LARGE_BASKET_THRESHOLD = 15      # avg_basket_size >= this -> "large", else "small"
ROUTINE_REORDER_THRESHOLD = 0.5  # reorder_rate >= this -> "routine", else "exploration"

MLFLOW_EXPERIMENT = "signature-driven-inventory-clustering"


def build_feature_table(conn) -> pd.DataFrame:
    """Joins rule-based basket stats with LLM-tagged product info aggregated to user level."""
    basket_stats = pd.read_sql(
        """SELECT user_id, total_orders, total_items, avg_basket_size,
                  reorder_rate, avg_days_between_orders
           FROM customer_features""",
        conn,
    )

    # Aggregate product-level LLM tags up to user level via majority vote
    # across every product a user has ever ordered.
    llm_agg = pd.read_sql(
        """SELECT lo.user_id, pt.household_orientation, pt.convenience
           FROM live_order_items li
           JOIN live_orders lo ON li.order_id = lo.order_id
           JOIN product_tags pt ON li.product_id = pt.product_id""",
        conn,
    )

    def majority(series: pd.Series) -> str:
        return series.mode().iloc[0] if not series.mode().empty else None

    llm_user_level = (
        llm_agg.groupby("user_id")
        .agg(household_orientation=("household_orientation", majority), convenience=("convenience", majority))
        .reset_index()
    )

    df = basket_stats.merge(llm_user_level, on="user_id", how="left")

    # Rule-based dimensions, computed directly from basket stats already in the DB
    df["shopping_mission"] = np.where(df["avg_basket_size"] >= STOCKUP_BASKET_THRESHOLD, "stockup", "topup")
    df["basket_behavior"] = np.where(df["avg_basket_size"] >= LARGE_BASKET_THRESHOLD, "large", "small")
    df["purchase_behavior"] = np.where(df["reorder_rate"] >= ROUTINE_REORDER_THRESHOLD, "routine", "exploration")

    return df.dropna(subset=["household_orientation", "convenience"])  # drop users with no tagged products yet


def encode_features(df: pd.DataFrame) -> np.ndarray:
    """One-hot encodes the 5 categorical dimensions + scales the numeric basket stats."""
    categorical_cols = ["household_orientation", "convenience", "shopping_mission", "basket_behavior", "purchase_behavior"]
    numeric_cols = ["total_orders", "avg_basket_size", "reorder_rate", "avg_days_between_orders"]

    encoded = pd.get_dummies(df[categorical_cols], prefix=categorical_cols)
    numeric = StandardScaler().fit_transform(df[numeric_cols].fillna(0))

    return np.hstack([numeric, encoded.to_numpy()])


def reduce_dimensions(X: np.ndarray, n_components: int = 2) -> np.ndarray:
    n_neighbors = max(2, min(15, len(X) - 1))
    reducer = umap.UMAP(n_components=n_components, n_neighbors=n_neighbors, random_state=42)
    return reducer.fit_transform(X)


def run_clustering_algorithms(X: np.ndarray) -> dict[str, np.ndarray]:
    n = len(X)
    k = min(5, max(2, n // 3))  # sensible default cluster count for small test data too

    results = {}
    results["kmeans"] = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(X)
    results["gmm"] = GaussianMixture(n_components=k, random_state=42).fit_predict(X)
    results["hierarchical"] = AgglomerativeClustering(n_clusters=k).fit_predict(X)
    results["dbscan"] = DBSCAN(eps=1.5, min_samples=max(2, n // 20)).fit_predict(X)
    return results


def evaluate_and_log(X: np.ndarray, labels: np.ndarray, algo_name: str) -> dict:
    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    metrics = {"n_clusters": n_clusters}

    if n_clusters >= 2:
        metrics["silhouette"] = silhouette_score(X, labels)
        metrics["davies_bouldin"] = davies_bouldin_score(X, labels)
    else:
        metrics["silhouette"] = None
        metrics["davies_bouldin"] = None

    with mlflow.start_run(run_name=algo_name):
        mlflow.log_param("algorithm", algo_name)
        mlflow.log_param("n_clusters_found", n_clusters)
        if metrics["silhouette"] is not None:
            mlflow.log_metric("silhouette_score", metrics["silhouette"])
            mlflow.log_metric("davies_bouldin_score", metrics["davies_bouldin"])

    return metrics


def pick_best_algorithm(all_metrics: dict[str, dict]) -> str:
    """Higher silhouette + lower Davies-Bouldin is better. Falls back to the
    first algorithm with a valid score if none stand out."""
    scored = {
        name: m["silhouette"] - m["davies_bouldin"] * 0.1
        for name, m in all_metrics.items()
        if m["silhouette"] is not None
    }
    if not scored:
        return next(iter(all_metrics))
    return max(scored, key=scored.get)


def save_signatures(conn, df: pd.DataFrame, labels: np.ndarray, algorithm: str) -> None:
    now = datetime.utcnow().isoformat()
    for (_, row), label in zip(df.iterrows(), labels):
        conn.execute(
            """INSERT INTO shopper_signatures
                   (user_id, household_orientation, convenience, shopping_mission,
                    purchase_behavior, basket_behavior, cluster_label, algorithm, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   household_orientation = excluded.household_orientation,
                   convenience = excluded.convenience,
                   shopping_mission = excluded.shopping_mission,
                   purchase_behavior = excluded.purchase_behavior,
                   basket_behavior = excluded.basket_behavior,
                   cluster_label = excluded.cluster_label,
                   algorithm = excluded.algorithm,
                   computed_at = excluded.computed_at""",
            (
                int(row["user_id"]), row["household_orientation"], row["convenience"],
                row["shopping_mission"], row["purchase_behavior"], row["basket_behavior"],
                int(label), algorithm, now,
            ),
        )
    conn.commit()


def run() -> None:
    init_db()
    conn = get_connection()
    mlflow.set_experiment(MLFLOW_EXPERIMENT)

    print("Building feature table...")
    df = build_feature_table(conn)
    if len(df) < 4:
        print(f"Only {len(df)} users have tagged products — not enough to cluster meaningfully. "
              f"Run llm_feature_engineering.py and the live pipeline further first.")
        conn.close()
        return
    print(f"{len(df)} users with complete features.")

    X_encoded = encode_features(df)
    print("Reducing dimensions with UMAP...")
    X_reduced = reduce_dimensions(X_encoded)

    print("Running clustering algorithms...")
    cluster_results = run_clustering_algorithms(X_reduced)

    all_metrics = {}
    for algo_name, labels in cluster_results.items():
        metrics = evaluate_and_log(X_reduced, labels, algo_name)
        all_metrics[algo_name] = metrics
        print(f"  {algo_name}: {metrics}")

    best_algo = pick_best_algorithm(all_metrics)
    print(f"Best algorithm: {best_algo} ({all_metrics[best_algo]})")

    save_signatures(conn, df, cluster_results[best_algo], best_algo)
    print(f"Saved {len(df)} shopper signatures using '{best_algo}' clustering.")

    conn.close()


if __name__ == "__main__":
    run()
