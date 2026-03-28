"""Shared fixtures for Rover test suite."""

import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from rover.db import Database


@pytest.fixture(scope="session")
def db_url():
    """Get test database URL. Skip tests if not available."""
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("No TEST_DATABASE_URL or DATABASE_URL set — skipping DB tests")
    return url


@pytest.fixture
def tmp_db(db_url):
    """Create a fresh database state for each test."""
    db = Database(db_url)
    yield db
    # Clean up all data between tests (order matters for FK constraints)
    with db.conn.cursor() as cur:
        cur.execute("DELETE FROM savings")
        cur.execute("DELETE FROM price_checks")
        cur.execute("DELETE FROM purchases")
        cur.execute("DELETE FROM metadata")
        cur.execute("DELETE FROM user_gmail_tokens")
        cur.execute("DELETE FROM retailers")
        cur.execute("DELETE FROM users")
    db.conn.close()


@pytest.fixture
def test_user(tmp_db) -> dict:
    """Create and return a test user."""
    return tmp_db.create_user(
        email="test@example.com",
        supabase_auth_id=str(uuid.uuid4()),
        name="Test User",
    )


@pytest.fixture
def test_user_id(test_user) -> str:
    """Return just the user_id string for convenience."""
    return str(test_user["id"])


@pytest.fixture
def scraper_config():
    """Minimal scraping config for tests (no real delays)."""
    return {
        "scraping": {
            "max_retries": 1,
            "timeout": 5,
            "min_delay": 0,
            "max_delay": 0,
            "rate_limit_per_domain": 0,
        }
    }


@pytest.fixture
def sample_purchase(tmp_db, test_user_id):
    """Insert and return a sample purchase row."""
    pid = tmp_db.add_purchase(
        user_id=test_user_id,
        gmail_message_id="msg_001",
        item_name="Classic Tee",
        price_paid=24.99,
        product_url="https://www.example.com/tee",
        retailer="Example Store",
        purchase_date="2026-03-01",
        order_number="ORD-123",
    )
    return tmp_db.get_purchase(pid)


@pytest.fixture
def anthropic_config():
    """Config dict with anthropic model settings."""
    return {"anthropic": {"model": "claude-sonnet-4-20250514"}}
