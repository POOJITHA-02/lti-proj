# LLM Feature Engineering, Pattern Discovery & Comparative Analysis

These 3 modules run **after** your data pipeline (`load_reference_data.py` +
`stream_simulator.py`/`live_order_generator.py`) has already populated `instacart.db`
with orders, order items, and `customer_features`.

## 1. Install dependencies
```
pip install -r requirements_modeling.txt
```

## 2. Install Ollama and pull a model (one-time, local — no API key, no cost)
Download Ollama from https://ollama.com, then:
```
ollama pull llama3.2
```
Leave Ollama running in the background (it runs as a local service after install).

## 3. Tag products (Module 1 — `llm_feature_engineering.py`)
```
python llm_feature_engineering.py
```
- Tags every **unique product** (not every order) on 2 dimensions: `household_orientation`
  (solo/family) and `convenience` (ready_to_eat/raw_ingredients).
- Sends 15 products per LLM call to keep the total call count manageable (~49,688 products
  → ~3,300 calls instead of ~49,688).
- **Resumable**: rerunning skips products already tagged (checked via a `LEFT JOIN` against
  `product_tags`) — safe to stop with `Ctrl+C` and continue later.
- To test on a small slice first: open the file and change the last line to
  `run(limit_products=200)`.

## 4. Discover patterns (Module 2 — `pattern_discovery.py`)
```
python pattern_discovery.py
```
- Builds a feature table per user: the 2 LLM-tagged dimensions (aggregated to user level
  by majority vote across everything they've bought) + 3 rule-based dimensions computed
  directly from `customer_features` stats already in the DB:
  - `shopping_mission` (topup/stockup) — from `avg_basket_size`
  - `basket_behavior` (small/large) — from `avg_basket_size`
  - `purchase_behavior` (routine/exploration) — from `reorder_rate`
- Reduces dimensions with UMAP, then runs **K-Means, GMM, Hierarchical, and DBSCAN**.
- Logs every run's parameters and metrics (silhouette score, Davies-Bouldin index) to
  **MLflow** — view them with `mlflow ui` (opens at http://127.0.0.1:5000).
- Automatically picks the best-scoring algorithm and writes final cluster assignments +
  all 5 shopper-signature dimensions to the `shopper_signatures` table.
- **Only users with at least one tagged product** are included — run Module 1 (fully or
  on enough of the catalog) before this.
- The two threshold constants at the top of the file (`STOCKUP_BASKET_THRESHOLD`,
  `LARGE_BASKET_THRESHOLD`, `ROUTINE_REORDER_THRESHOLD`) are reasonable defaults —
  tune them once you've looked at your actual data's distribution.

## 5. Compare clusters (Module 3 — `comparative_analysis.py`)
```
python comparative_analysis.py
```
Produces 4 CSVs (also printed to console) that feed directly into your dashboard/Power BI:
- `cluster_overview.csv` — shopper count, avg basket size, reorder rate, % family, etc. per cluster
- `department_mix_by_cluster.csv` — which departments each cluster buys from
- `aisle_mix_by_cluster.csv` — top 5 aisles per cluster (finer-grained than department)
- `reorder_behavior_by_cluster.csv` — item-level reorder ratio per cluster

**Note on brand/pack-size:** the Instacart dataset has no structured brand or pack-size
columns — only `product_name`, `aisle_id`, `department_id`. Department/aisle mix is the
available substitute for "category" comparison from the original plan. Brand and pack size
would need regex/NLP extraction from the raw `product_name` text (e.g. matching "16 oz",
"2 pack") — a reasonable enhancement to add later, not included here since it isn't in the
original 6 CSVs.

## Run order summary
```
python load_reference_data.py        # once
python stream_simulator.py           # once (MODE = "bulk")
python live_order_generator.py       # ongoing, in its own terminal
python llm_feature_engineering.py    # once per new product batch (resumable)
python pattern_discovery.py          # rerun whenever you want fresh clusters
python comparative_analysis.py       # rerun after each pattern_discovery.py run
```
