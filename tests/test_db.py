"""Tests for rover.db — SQLite database layer."""

import pytest

from rover.db import Database


class TestPurchases:
    def test_add_purchase(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_001",
            item_name="Widget",
            price_paid=9.99,
            product_url=None,
            retailer="Acme",
            purchase_date="2026-03-01",
        )
        assert pid is not None
        row = tmp_db.get_purchase(pid)
        assert row["item_name"] == "Widget"
        assert row["price_paid"] == 9.99
        assert row["retailer"] == "Acme"
        assert row["currency"] == "USD"

    def test_duplicate_gmail_id_returns_none(self, tmp_db):
        tmp_db.add_purchase(
            gmail_message_id="msg_dup",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        result = tmp_db.add_purchase(
            gmail_message_id="msg_dup",
            item_name="B",
            price_paid=2.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-02",
        )
        assert result is None

    def test_has_purchase_for_item(self, tmp_db):
        tmp_db.add_purchase(
            gmail_message_id="msg_has",
            item_name="Shirt",
            price_paid=20.0,
            product_url=None,
            retailer="Gap",
            purchase_date="2026-02-01",
            order_number="GAP-100",
        )
        assert tmp_db.has_purchase_for_item("Gap", "GAP-100", "Shirt")
        assert not tmp_db.has_purchase_for_item("Gap", "GAP-100", "Pants")
        assert not tmp_db.has_purchase_for_item("Gap", "GAP-999", "Shirt")

    def test_get_active_purchases(self, tmp_db):
        assert tmp_db.get_active_purchases() == []
        tmp_db.add_purchase(
            gmail_message_id="msg_a1",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        tmp_db.add_purchase(
            gmail_message_id="msg_a2",
            item_name="B",
            price_paid=2.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-02",
        )
        assert len(tmp_db.get_active_purchases()) == 2

    def test_update_purchase_url(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_url",
            item_name="Shoe",
            price_paid=80.0,
            product_url=None,
            retailer="Nike",
            purchase_date="2026-03-01",
        )
        assert tmp_db.get_purchase(pid)["product_url"] is None
        tmp_db.update_purchase_url(pid, "https://nike.com/shoe")
        assert tmp_db.get_purchase(pid)["product_url"] == "https://nike.com/shoe"

    def test_get_purchases_with_url(self, tmp_db):
        tmp_db.add_purchase(
            gmail_message_id="msg_no_url",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_with_url",
            item_name="B",
            price_paid=2.0,
            product_url="https://example.com/b",
            retailer="Y",
            purchase_date="2026-01-02",
        )
        results = tmp_db.get_purchases_with_url()
        assert len(results) == 1
        assert results[0]["id"] == pid

    def test_get_purchases_needing_url(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_need",
            item_name="C",
            price_paid=3.0,
            product_url=None,
            retailer="Z",
            purchase_date="2026-01-01",
        )
        assert len(tmp_db.get_purchases_needing_url()) == 1

        tmp_db.mark_url_search_attempted(pid)
        assert len(tmp_db.get_purchases_needing_url()) == 0

    def test_mark_url_search_attempted(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_mark",
            item_name="D",
            price_paid=4.0,
            product_url=None,
            retailer="W",
            purchase_date="2026-01-01",
        )
        tmp_db.mark_url_search_attempted(pid)
        row = tmp_db.get_purchase(pid)
        assert row["url_search_attempted"] == 1


class TestPriceChecks:
    def test_add_and_get_price_check(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_pc",
            item_name="Hat",
            price_paid=15.0,
            product_url="https://example.com/hat",
            retailer="Caps",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(
            purchase_id=pid,
            current_price=12.0,
            status="success",
        )
        latest = tmp_db.get_latest_price_check(pid)
        assert latest is not None
        assert latest["id"] == check_id
        assert latest["current_price"] == 12.0
        assert latest["status"] == "success"
        assert latest["error_detail"] is None

    def test_price_check_with_error(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_pcerr",
            item_name="Gloves",
            price_paid=10.0,
            product_url="https://example.com/gloves",
            retailer="Winter",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(
            purchase_id=pid,
            current_price=None,
            status="scrape_failed",
            error_detail="Connection timeout",
        )
        latest = tmp_db.get_latest_price_check(pid)
        assert latest["status"] == "scrape_failed"
        assert latest["error_detail"] == "Connection timeout"

    def test_latest_price_check_returns_most_recent(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_multi",
            item_name="Scarf",
            price_paid=25.0,
            product_url="https://example.com/scarf",
            retailer="Warm",
            purchase_date="2026-01-01",
        )
        id1 = tmp_db.add_price_check(pid, 20.0, "success")
        # Manually set different timestamps so ORDER BY checked_at DESC works
        tmp_db.conn.execute(
            "UPDATE price_checks SET checked_at = '2026-03-01T00:00:00' WHERE id = ?", (id1,)
        )
        tmp_db.conn.commit()
        id2 = tmp_db.add_price_check(pid, 18.0, "success")
        tmp_db.conn.execute(
            "UPDATE price_checks SET checked_at = '2026-03-02T00:00:00' WHERE id = ?", (id2,)
        )
        tmp_db.conn.commit()
        latest = tmp_db.get_latest_price_check(pid)
        assert latest["id"] == id2
        assert latest["current_price"] == 18.0

    def test_no_price_check_returns_none(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_nopc",
            item_name="Belt",
            price_paid=30.0,
            product_url=None,
            retailer="Leather",
            purchase_date="2026-01-01",
        )
        assert tmp_db.get_latest_price_check(pid) is None


class TestSavings:
    def test_add_and_get_savings(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_sav",
            item_name="Jacket",
            price_paid=100.0,
            product_url="https://example.com/jacket",
            retailer="OutdoorCo",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(pid, 80.0, "success")
        save_id = tmp_db.add_saving(
            purchase_id=pid,
            price_check_id=check_id,
            original_price=100.0,
            dropped_price=80.0,
            savings_amount=20.0,
        )
        new_savings = tmp_db.get_new_savings()
        assert len(new_savings) == 1
        assert new_savings[0]["savings_amount"] == 20.0
        assert new_savings[0]["status"] == "new"

    def test_update_saving_status(self, tmp_db):
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_stat",
            item_name="Boots",
            price_paid=120.0,
            product_url="https://example.com/boots",
            retailer="FootCo",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(pid, 90.0, "success")
        save_id = tmp_db.add_saving(pid, check_id, 120.0, 90.0, 30.0)

        tmp_db.update_saving_status(save_id, "notified")
        assert tmp_db.get_new_savings() == []

        tmp_db.update_saving_status(save_id, "claimed")
        # Still not "new"
        assert tmp_db.get_new_savings() == []


class TestMetadata:
    def test_set_and_get_metadata(self, tmp_db):
        assert tmp_db.get_metadata("last_scan") is None
        tmp_db.set_metadata("last_scan", "2026-03-01")
        assert tmp_db.get_metadata("last_scan") == "2026-03-01"

    def test_metadata_upsert(self, tmp_db):
        tmp_db.set_metadata("key", "v1")
        tmp_db.set_metadata("key", "v2")
        assert tmp_db.get_metadata("key") == "v2"


class TestRetailers:
    def test_upsert_and_get_retailer(self, tmp_db):
        tmp_db.upsert_retailer(
            name="Amazon",
            domain="amazon.com",
            refund_window_days=30,
            source="manual",
        )
        row = tmp_db.get_retailer_by_domain("amazon.com")
        assert row is not None
        assert row["name"] == "Amazon"
        assert row["refund_window_days"] == 30
        assert row["source"] == "manual"

    def test_upsert_updates_existing(self, tmp_db):
        tmp_db.upsert_retailer("Amazon", "amazon.com", 30, source="manual")
        tmp_db.upsert_retailer("Amazon", "amazon.com", 45, source="scraped")
        row = tmp_db.get_retailer_by_domain("amazon.com")
        assert row["refund_window_days"] == 45
        assert row["source"] == "scraped"

    def test_get_nonexistent_retailer(self, tmp_db):
        assert tmp_db.get_retailer_by_domain("nonexistent.com") is None


class TestMigration:
    def test_fresh_db_has_all_columns(self, tmp_db):
        """Verify url_search_attempted and order_number columns exist."""
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_mig",
            item_name="Test",
            price_paid=1.0,
            product_url=None,
            retailer="Test",
            purchase_date="2026-01-01",
            order_number="ORD-MIG",
        )
        row = tmp_db.get_purchase(pid)
        assert row["order_number"] == "ORD-MIG"
        assert row["url_search_attempted"] == 0
