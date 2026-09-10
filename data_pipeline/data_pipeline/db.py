import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "instacart.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_bulk_load_connection() -> sqlite3.Connection:
    """Faster connection for one-time bulk loads (e.g. order_products__prior.csv).
    Trades some crash-safety for speed — fine for a reproducible, one-time load."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def init_db() -> None:
    conn = get_connection()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(f"Schema ready at {DB_PATH}")


if __name__ == "__main__":
    init_db()
