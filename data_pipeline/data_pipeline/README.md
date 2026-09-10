# Live Data Pipeline — Setup

## 1. Add the missing file
`departments.csv`, `products.csv`, `aisles.csv`, and `order_products__train.csv` are already in `data/raw/`.

You still need to add **`orders.csv`** (download from the Instacart Market Basket Analysis dataset on Kaggle) into `data/raw/`.

Optional: once you're ready to move past the demo-scale `order_products__train.csv` (131K orders) to the full dataset, also add `order_products__prior.csv` and change the filename in `load_reference_data.py`'s `__main__` block.

## 2. Install dependencies
```
pip install pandas
```

## 3. Load the batch data (run once)
```
python load_reference_data.py
```
This creates `instacart.db`, loads the reference tables (departments, aisles, products), and loads `orders.csv` + `order_products__train.csv` into the **source pool** (`source_orders`, `source_order_items`) — this is the "not yet ingested" landing zone.

## 4. Start the live stream
```
python stream_simulator.py
```
This pulls one not-yet-ingested order at a time (in a fixed random order), inserts it into `live_orders` / `live_order_items`, and updates that shopper's row in `customer_features` — a 1.5 second gap between orders by default (edit `DELAY_SECONDS` in `stream_simulator.py` to change the pace, or set it to `0` for a fast test run).

Stop it anytime with `Ctrl+C` — the cursor is saved in `stream_state`, so rerunning resumes exactly where it left off instead of replaying ingested orders.

## 5. Watch it update live
Open `instacart.db` in **DB Browser for SQLite** (or the VS Code SQL extension) and run:
```sql
SELECT * FROM customer_features ORDER BY updated_at DESC LIMIT 20;
```
Re-run the query (or use DB Browser's "Reload" if it shows the file as locked) while `stream_simulator.py` is running in another terminal — you'll see rows update as new orders come in.

## Tables reference
- `source_orders` / `source_order_items` — full historical data, the "not yet live" pool
- `live_orders` / `live_order_items` — orders that have been streamed in
- `customer_features` — rolling per-shopper aggregate, recomputed after every order (this is what your dashboard/API should query)
- `stream_state` — resume cursor for the simulator
