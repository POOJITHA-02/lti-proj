-- ============================================================
-- Reference / dimension tables — static, loaded once
-- ============================================================
CREATE TABLE IF NOT EXISTS departments (
    department_id INTEGER PRIMARY KEY,
    department TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aisles (
    aisle_id INTEGER PRIMARY KEY,
    aisle TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    aisle_id INTEGER REFERENCES aisles(aisle_id),
    department_id INTEGER REFERENCES departments(department_id)
);

-- ============================================================
-- Source pool (landing zone) — the full historical dataset,
-- loaded once via batch ingestion. Represents orders that
-- exist but have not yet been "seen" by the live system.
-- ============================================================
CREATE TABLE IF NOT EXISTS source_orders (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    eval_set TEXT,
    order_number INTEGER,
    order_dow INTEGER,
    order_hour_of_day INTEGER,
    days_since_prior_order REAL
);
CREATE INDEX IF NOT EXISTS idx_source_orders_user ON source_orders(user_id);

CREATE TABLE IF NOT EXISTS source_order_items (
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    add_to_cart_order INTEGER,
    reordered INTEGER,
    PRIMARY KEY (order_id, product_id)
);

-- ============================================================
-- Live tables — populated incrementally by the stream
-- simulator, one order at a time, as if each buyer just
-- checked out.
-- ============================================================
CREATE TABLE IF NOT EXISTS live_orders (
    order_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    order_number INTEGER,
    order_dow INTEGER,
    order_hour_of_day INTEGER,
    days_since_prior_order REAL,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_live_orders_user ON live_orders(user_id);

CREATE TABLE IF NOT EXISTS live_order_items (
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    add_to_cart_order INTEGER,
    reordered INTEGER,
    PRIMARY KEY (order_id, product_id)
);

-- ============================================================
-- Continuously-updated rolling aggregate per shopper.
-- This is the table your dashboard/API reads from.
-- ============================================================
CREATE TABLE IF NOT EXISTS customer_features (
    user_id INTEGER PRIMARY KEY,
    total_orders INTEGER DEFAULT 0,
    total_items INTEGER DEFAULT 0,
    avg_basket_size REAL DEFAULT 0,
    reorder_rate REAL DEFAULT 0,
    avg_days_between_orders REAL DEFAULT 0,
    last_order_id INTEGER,
    last_ingested_at TEXT,
    updated_at TEXT
);

-- ============================================================
-- Load manifest — tracks which raw files have already been
-- ingested, so re-running load_reference_data.py is safe and
-- doesn't re-insert (and collide on) already-loaded data.
-- ============================================================
CREATE TABLE IF NOT EXISTS load_manifest (
    filename TEXT PRIMARY KEY,
    rows_loaded INTEGER,
    loaded_at TEXT
);

-- ============================================================
-- Stream cursor — lets the simulator stop and resume without
-- replaying orders it already ingested.
-- ============================================================
CREATE TABLE IF NOT EXISTS stream_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_order_index INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO stream_state (id, last_order_index) VALUES (1, 0);
