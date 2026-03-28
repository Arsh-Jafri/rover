import sqlite3
from datetime import datetime, timezone


SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY,
    gmail_message_id TEXT UNIQUE,
    item_name TEXT,
    price_paid REAL,
    product_url TEXT,
    retailer TEXT,
    purchase_date TEXT,
    currency TEXT DEFAULT 'USD',
    order_number TEXT,
    raw_email_snippet TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_checks (
    id INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    current_price REAL,
    checked_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT CHECK(status IN ('success', 'scrape_failed', 'parse_failed')),
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
    price_check_id INTEGER NOT NULL REFERENCES price_checks(id),
    original_price REAL,
    dropped_price REAL,
    savings_amount REAL,
    detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'new' CHECK(status IN ('new', 'notified', 'claimed'))
);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS retailers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    domain TEXT UNIQUE,
    refund_window_days INTEGER,
    support_email TEXT,
    support_url TEXT,
    policy_url TEXT,
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'scraped')),
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add columns that may not exist in older databases."""
        columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(purchases)").fetchall()
        }
        if "order_number" not in columns:
            self.conn.execute("ALTER TABLE purchases ADD COLUMN order_number TEXT")
        if "url_search_attempted" not in columns:
            self.conn.execute("ALTER TABLE purchases ADD COLUMN url_search_attempted INTEGER DEFAULT 0")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def add_purchase(
        self,
        gmail_message_id: str,
        item_name: str,
        price_paid: float,
        product_url: str | None,
        retailer: str,
        purchase_date: str,
        currency: str = "USD",
        order_number: str | None = None,
        raw_email_snippet: str | None = None,
    ) -> int | None:
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO purchases
               (gmail_message_id, item_name, price_paid, product_url, retailer,
                purchase_date, currency, order_number, raw_email_snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (gmail_message_id, item_name, price_paid, product_url, retailer,
             purchase_date, currency, order_number, raw_email_snippet),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return cursor.lastrowid

    def has_purchase_for_item(self, retailer: str, order_number: str, item_name: str) -> bool:
        """Check if a purchase already exists for the given retailer + order + item."""
        row = self.conn.execute(
            "SELECT 1 FROM purchases WHERE retailer = ? AND order_number = ? AND item_name = ? LIMIT 1",
            (retailer, order_number, item_name),
        ).fetchone()
        return row is not None

    def update_purchase_url(self, purchase_id: int, product_url: str) -> None:
        """Set the product_url for a purchase."""
        self.conn.execute(
            "UPDATE purchases SET product_url = ? WHERE id = ?",
            (product_url, purchase_id),
        )
        self.conn.commit()

    def mark_url_search_attempted(self, purchase_id: int) -> None:
        """Mark that we've already tried to find a product URL for this purchase."""
        self.conn.execute(
            "UPDATE purchases SET url_search_attempted = 1 WHERE id = ?",
            (purchase_id,),
        )
        self.conn.commit()

    def get_purchases_needing_url(self) -> list[dict]:
        """Get purchases that have no product_url and haven't been searched yet."""
        rows = self.conn.execute(
            "SELECT * FROM purchases WHERE product_url IS NULL AND url_search_attempted = 0"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_purchase(self, purchase_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM purchases WHERE id = ?", (purchase_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_purchases(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM purchases").fetchall()
        return [dict(r) for r in rows]

    def get_purchases_with_url(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM purchases WHERE product_url IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]

    def add_price_check(
        self,
        purchase_id: int,
        current_price: float | None,
        status: str,
        error_detail: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO price_checks (purchase_id, current_price, status, error_detail)
               VALUES (?, ?, ?, ?)""",
            (purchase_id, current_price, status, error_detail),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_latest_price_check(self, purchase_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM price_checks
               WHERE purchase_id = ?
               ORDER BY checked_at DESC LIMIT 1""",
            (purchase_id,),
        ).fetchone()
        return dict(row) if row else None

    def add_saving(
        self,
        purchase_id: int,
        price_check_id: int,
        original_price: float,
        dropped_price: float,
        savings_amount: float,
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO savings
               (purchase_id, price_check_id, original_price, dropped_price, savings_amount)
               VALUES (?, ?, ?, ?, ?)""",
            (purchase_id, price_check_id, original_price, dropped_price, savings_amount),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_new_savings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM savings WHERE status = 'new'"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_saving_status(self, saving_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE savings SET status = ? WHERE id = ?", (status, saving_id)
        )
        self.conn.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO metadata (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?""",
            (key, value, self._now(), value, self._now()),
        )
        self.conn.commit()

    def get_retailer_by_domain(self, domain: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM retailers WHERE domain = ?", (domain,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_retailer(
        self,
        name: str,
        domain: str,
        refund_window_days: int,
        support_email: str | None = None,
        support_url: str | None = None,
        policy_url: str | None = None,
        source: str = "manual",
    ) -> int:
        cursor = self.conn.execute(
            """INSERT INTO retailers
               (name, domain, refund_window_days, support_email, support_url, policy_url, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                 name = ?, refund_window_days = ?, support_email = ?, support_url = ?,
                 policy_url = ?, source = ?, updated_at = ?""",
            (name, domain, refund_window_days, support_email, support_url, policy_url, source, self._now(),
             name, refund_window_days, support_email, support_url, policy_url, source, self._now()),
        )
        self.conn.commit()
        return cursor.lastrowid
