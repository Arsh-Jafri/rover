"""Shared fixtures for Rover test suite."""

import os
import tempfile

import pytest

from rover.db import Database


@pytest.fixture
def tmp_db(tmp_path):
    """Create a fresh in-memory-like SQLite database for each test."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    return db


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
def sample_purchase(tmp_db):
    """Insert and return a sample purchase row."""
    pid = tmp_db.add_purchase(
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
