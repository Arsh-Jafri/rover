import os
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    supabase_auth_id UUID UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_gmail_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    encrypted_access_token BYTEA,
    encrypted_refresh_token BYTEA,
    token_expiry TIMESTAMPTZ,
    gmail_email TEXT,
    connected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id)
);

CREATE TABLE IF NOT EXISTS purchases (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gmail_message_id TEXT,
    item_name TEXT,
    price_paid DOUBLE PRECISION,
    product_url TEXT,
    retailer TEXT,
    purchase_date TEXT,
    currency TEXT DEFAULT 'USD',
    order_number TEXT,
    raw_email_snippet TEXT,
    url_search_attempted INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, gmail_message_id)
);

CREATE TABLE IF NOT EXISTS price_checks (
    id SERIAL PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    current_price DOUBLE PRECISION,
    checked_at TIMESTAMPTZ DEFAULT now(),
    status TEXT CHECK(status IN ('success', 'scrape_failed', 'parse_failed')),
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS savings (
    id SERIAL PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    price_check_id INTEGER NOT NULL REFERENCES price_checks(id) ON DELETE CASCADE,
    original_price DOUBLE PRECISION,
    dropped_price DOUBLE PRECISION,
    savings_amount DOUBLE PRECISION,
    detected_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT 'new' CHECK(status IN ('new', 'notified', 'claimed'))
);

CREATE TABLE IF NOT EXISTS metadata (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS retailers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    domain TEXT UNIQUE,
    refund_window_days INTEGER,
    support_email TEXT,
    support_url TEXT,
    policy_url TEXT,
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'scraped')),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT,
    link TEXT,
    read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


class Database:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("DATABASE_URL")
        if not self.database_url:
            raise ValueError("DATABASE_URL must be provided or set as environment variable")
        self.conn = psycopg2.connect(self.database_url)
        self.conn.autocommit = True
        self._init_schema()

    def _init_schema(self):
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            cur.execute(SCHEMA)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def close(self):
        """Close the database connection."""
        if self.conn and not self.conn.closed:
            self.conn.close()

    def _cursor(self):
        return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------

    def create_user(self, email: str, supabase_auth_id: str, name: str | None = None) -> dict:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO users (email, supabase_auth_id, name)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (supabase_auth_id) DO UPDATE SET email = EXCLUDED.email
                   RETURNING *""",
                (email, supabase_auth_id, name),
            )
            return dict(cur.fetchone())

    def get_user_by_auth_id(self, supabase_auth_id: str) -> dict | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM users WHERE supabase_auth_id = %s", (supabase_auth_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_user(self, user_id: str) -> dict | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_user(self, user_id: str) -> bool:
        """Delete a user and all their data (cascades via foreign keys)."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Gmail Tokens
    # ------------------------------------------------------------------

    def store_gmail_token(
        self,
        user_id: str,
        encrypted_access_token: bytes,
        encrypted_refresh_token: bytes,
        token_expiry: str | None = None,
        gmail_email: str | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO user_gmail_tokens
                   (user_id, encrypted_access_token, encrypted_refresh_token, token_expiry, gmail_email)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (user_id) DO UPDATE SET
                     encrypted_access_token = EXCLUDED.encrypted_access_token,
                     encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                     token_expiry = EXCLUDED.token_expiry,
                     gmail_email = COALESCE(EXCLUDED.gmail_email, user_gmail_tokens.gmail_email),
                     connected_at = now()""",
                (user_id, encrypted_access_token, encrypted_refresh_token, token_expiry, gmail_email),
            )

    def get_gmail_token(self, user_id: str) -> dict | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM user_gmail_tokens WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def delete_gmail_token(self, user_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM user_gmail_tokens WHERE user_id = %s", (user_id,))

    def get_users_with_gmail(self) -> list[dict]:
        """Get all users who have connected their Gmail account."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT u.* FROM users u
                   JOIN user_gmail_tokens t ON u.id = t.user_id"""
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Purchases (tenant-scoped)
    # ------------------------------------------------------------------

    def add_purchase(
        self,
        user_id: str,
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
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO purchases
                   (user_id, gmail_message_id, item_name, price_paid, product_url, retailer,
                    purchase_date, currency, order_number, raw_email_snippet)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, gmail_message_id) DO NOTHING
                   RETURNING id""",
                (user_id, gmail_message_id, item_name, price_paid, product_url, retailer,
                 purchase_date, currency, order_number, raw_email_snippet),
            )
            row = cur.fetchone()
            return row["id"] if row else None

    def has_purchase_for_item(self, user_id: str, retailer: str, order_number: str, item_name: str) -> bool:
        with self._cursor() as cur:
            cur.execute(
                "SELECT 1 FROM purchases WHERE user_id = %s AND retailer = %s AND order_number = %s AND item_name = %s LIMIT 1",
                (user_id, retailer, order_number, item_name),
            )
            return cur.fetchone() is not None

    def update_purchase_url(self, purchase_id: int, product_url: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE purchases SET product_url = %s WHERE id = %s",
                (product_url, purchase_id),
            )

    def mark_url_search_attempted(self, purchase_id: int) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE purchases SET url_search_attempted = 1 WHERE id = %s",
                (purchase_id,),
            )

    def get_purchases_needing_url(self, user_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM purchases WHERE user_id = %s AND product_url IS NULL AND url_search_attempted = 0",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_purchase(self, purchase_id: int) -> dict | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM purchases WHERE id = %s", (purchase_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_active_purchases(self, user_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM purchases WHERE user_id = %s", (user_id,))
            return [dict(r) for r in cur.fetchall()]

    def get_purchases_with_url(self, user_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM purchases WHERE user_id = %s AND product_url IS NOT NULL",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Price Checks
    # ------------------------------------------------------------------

    def add_price_check(
        self,
        purchase_id: int,
        current_price: float | None,
        status: str,
        error_detail: str | None = None,
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO price_checks (purchase_id, current_price, status, error_detail)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (purchase_id, current_price, status, error_detail),
            )
            return cur.fetchone()["id"]

    def get_latest_price_check(self, purchase_id: int) -> dict | None:
        with self._cursor() as cur:
            cur.execute(
                """SELECT * FROM price_checks
                   WHERE purchase_id = %s
                   ORDER BY checked_at DESC LIMIT 1""",
                (purchase_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Savings
    # ------------------------------------------------------------------

    def add_saving(
        self,
        purchase_id: int,
        price_check_id: int,
        original_price: float,
        dropped_price: float,
        savings_amount: float,
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO savings
                   (purchase_id, price_check_id, original_price, dropped_price, savings_amount)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id""",
                (purchase_id, price_check_id, original_price, dropped_price, savings_amount),
            )
            return cur.fetchone()["id"]

    def get_new_savings(self, user_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT s.* FROM savings s
                   JOIN purchases p ON s.purchase_id = p.id
                   WHERE p.user_id = %s AND s.status = 'new'""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_notified_savings(self, user_id: str) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                """SELECT s.* FROM savings s
                   JOIN purchases p ON s.purchase_id = p.id
                   WHERE p.user_id = %s AND s.status = 'notified'""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def update_saving_status(self, saving_id: int, status: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE savings SET status = %s WHERE id = %s", (status, saving_id)
            )

    # ------------------------------------------------------------------
    # Metadata (tenant-scoped)
    # ------------------------------------------------------------------

    def get_metadata(self, user_id: str, key: str) -> str | None:
        with self._cursor() as cur:
            cur.execute(
                "SELECT value FROM metadata WHERE user_id = %s AND key = %s",
                (user_id, key),
            )
            row = cur.fetchone()
            return row["value"] if row else None

    def set_metadata(self, user_id: str, key: str, value: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO metadata (user_id, key, value, updated_at) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (user_id, key, value, self._now()),
            )

    # ------------------------------------------------------------------
    # Retailers (global — shared across users)
    # ------------------------------------------------------------------

    def get_retailer_by_domain(self, domain: str) -> dict | None:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM retailers WHERE domain = %s", (domain,))
            row = cur.fetchone()
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
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO retailers
                   (name, domain, refund_window_days, support_email, support_url, policy_url, source, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (domain) DO UPDATE SET
                     name = EXCLUDED.name,
                     refund_window_days = EXCLUDED.refund_window_days,
                     support_email = EXCLUDED.support_email,
                     support_url = EXCLUDED.support_url,
                     policy_url = EXCLUDED.policy_url,
                     source = EXCLUDED.source,
                     updated_at = EXCLUDED.updated_at
                   RETURNING id""",
                (name, domain, refund_window_days, support_email, support_url, policy_url, source, self._now()),
            )
            return cur.fetchone()["id"]

    def get_all_retailers(self) -> list[dict]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM retailers ORDER BY name")
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Dashboard queries
    # ------------------------------------------------------------------

    def get_dashboard_summary(self, user_id: str) -> dict:
        """Get aggregate stats for a user's dashboard."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as count FROM purchases WHERE user_id = %s", (user_id,))
            total_purchases = cur.fetchone()["count"]

            cur.execute(
                "SELECT COUNT(*) as count FROM purchases WHERE user_id = %s AND product_url IS NOT NULL",
                (user_id,),
            )
            active_tracking = cur.fetchone()["count"]

            cur.execute(
                """SELECT COUNT(*) as count, COALESCE(SUM(s.savings_amount), 0) as total
                   FROM savings s JOIN purchases p ON s.purchase_id = p.id
                   WHERE p.user_id = %s""",
                (user_id,),
            )
            row = cur.fetchone()
            total_savings_count = row["count"]
            total_savings_amount = float(row["total"])

            cur.execute(
                """SELECT COUNT(*) as count FROM savings s
                   JOIN purchases p ON s.purchase_id = p.id
                   WHERE p.user_id = %s AND s.status IN ('new', 'notified')""",
                (user_id,),
            )
            pending_claims = cur.fetchone()["count"]

            return {
                "total_purchases": total_purchases,
                "active_tracking": active_tracking,
                "total_savings_count": total_savings_count,
                "total_savings_amount": total_savings_amount,
                "pending_claims": pending_claims,
            }

    def get_purchases_with_latest_check(self, user_id: str) -> list[dict]:
        """Get all purchases with their most recent price check status and retailer info."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT p.*,
                          pc.current_price as latest_price,
                          pc.checked_at as last_checked,
                          pc.status as check_status,
                          r.support_email as retailer_support_email,
                          r.support_url as retailer_support_url,
                          r.refund_window_days,
                          s_active.savings_status,
                          s_active.savings_amount as active_savings_amount
                   FROM purchases p
                   LEFT JOIN LATERAL (
                       SELECT current_price, checked_at, status
                       FROM price_checks
                       WHERE purchase_id = p.id
                       ORDER BY checked_at DESC
                       LIMIT 1
                   ) pc ON true
                   LEFT JOIN retailers r ON lower(r.name) = lower(p.retailer)
                   LEFT JOIN LATERAL (
                       SELECT s.status as savings_status, s.savings_amount
                       FROM savings s
                       WHERE s.purchase_id = p.id
                       ORDER BY s.detected_at DESC
                       LIMIT 1
                   ) s_active ON true
                   WHERE p.user_id = %s
                   ORDER BY p.created_at DESC""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_savings_with_details(self, user_id: str) -> list[dict]:
        """Get all savings with purchase details for the dashboard."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT s.*, p.item_name, p.retailer, p.product_url, p.order_number, p.purchase_date
                   FROM savings s
                   JOIN purchases p ON s.purchase_id = p.id
                   WHERE p.user_id = %s
                   ORDER BY s.detected_at DESC""",
                (user_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_users_with_active_purchases(self) -> list[dict]:
        """Get all users who have purchases with URLs (for price check scheduling)."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT DISTINCT u.* FROM users u
                   JOIN purchases p ON u.id = p.user_id
                   WHERE p.product_url IS NOT NULL"""
            )
            return [dict(r) for r in cur.fetchall()]

    def get_users_with_notified_savings(self) -> list[dict]:
        """Get all users who have savings ready to be claimed."""
        with self._cursor() as cur:
            cur.execute(
                """SELECT DISTINCT u.* FROM users u
                   JOIN purchases p ON u.id = p.user_id
                   JOIN savings s ON s.purchase_id = p.id
                   WHERE s.status = 'notified'"""
            )
            return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def add_notification(
        self,
        user_id: str,
        title: str,
        body: str | None = None,
        link: str | None = None,
    ) -> int:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO notifications (user_id, title, body, link)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user_id, title, body, link),
            )
            return cur.fetchone()["id"]

    def add_notification_for_all(self, title: str, body: str | None = None, link: str | None = None) -> int:
        """Send a notification to every user. Returns count sent."""
        with self._cursor() as cur:
            cur.execute("SELECT id FROM users")
            users = cur.fetchall()
            for u in users:
                cur.execute(
                    "INSERT INTO notifications (user_id, title, body, link) VALUES (%s, %s, %s, %s)",
                    (u["id"], title, body, link),
                )
            return len(users)

    def get_notifications(self, user_id: str, limit: int = 20) -> list[dict]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def get_unread_notification_count(self, user_id: str) -> int:
        with self._cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as count FROM notifications WHERE user_id = %s AND read = FALSE",
                (user_id,),
            )
            return cur.fetchone()["count"]

    def mark_notification_read(self, notification_id: int, user_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE notifications SET read = TRUE WHERE id = %s AND user_id = %s",
                (notification_id, user_id),
            )

    def mark_all_notifications_read(self, user_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE notifications SET read = TRUE WHERE user_id = %s AND read = FALSE",
                (user_id,),
            )
