"""Tests for rover.db — Postgres database layer."""

import uuid

import pytest

from rover.db import Database


class TestUsers:
    def test_create_user(self, tmp_db):
        auth_id = str(uuid.uuid4())
        user = tmp_db.create_user("alice@example.com", auth_id, "Alice")
        assert user["email"] == "alice@example.com"
        assert user["name"] == "Alice"
        assert user["id"] is not None

    def test_get_user_by_auth_id(self, tmp_db):
        auth_id = str(uuid.uuid4())
        created = tmp_db.create_user("bob@example.com", auth_id)
        found = tmp_db.get_user_by_auth_id(auth_id)
        assert found is not None
        assert found["email"] == "bob@example.com"

    def test_get_user(self, tmp_db, test_user):
        found = tmp_db.get_user(str(test_user["id"]))
        assert found["email"] == test_user["email"]

    def test_duplicate_auth_id_upserts_email(self, tmp_db):
        auth_id = str(uuid.uuid4())
        tmp_db.create_user("old@example.com", auth_id)
        user = tmp_db.create_user("new@example.com", auth_id)
        assert user["email"] == "new@example.com"


class TestPurchases:
    def test_add_purchase(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
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

    def test_duplicate_gmail_id_returns_none(self, tmp_db, test_user_id):
        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_dup",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        result = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_dup",
            item_name="B",
            price_paid=2.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-02",
        )
        assert result is None

    def test_same_gmail_id_different_users(self, tmp_db, test_user_id):
        """Different users can have the same gmail_message_id."""
        user2 = tmp_db.create_user("other@example.com", str(uuid.uuid4()))
        user2_id = str(user2["id"])

        pid1 = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_shared",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        pid2 = tmp_db.add_purchase(
            user_id=user2_id,
            gmail_message_id="msg_shared",
            item_name="B",
            price_paid=2.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-02",
        )
        assert pid1 is not None
        assert pid2 is not None
        assert pid1 != pid2

    def test_has_purchase_for_item(self, tmp_db, test_user_id):
        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_has",
            item_name="Shirt",
            price_paid=20.0,
            product_url=None,
            retailer="Gap",
            purchase_date="2026-02-01",
            order_number="GAP-100",
        )
        assert tmp_db.has_purchase_for_item(test_user_id, "Gap", "GAP-100", "Shirt")
        assert not tmp_db.has_purchase_for_item(test_user_id, "Gap", "GAP-100", "Pants")
        assert not tmp_db.has_purchase_for_item(test_user_id, "Gap", "GAP-999", "Shirt")

    def test_user_isolation(self, tmp_db, test_user_id):
        """Users can only see their own purchases."""
        user2 = tmp_db.create_user("other@example.com", str(uuid.uuid4()))
        user2_id = str(user2["id"])

        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_u1",
            item_name="User1 Item",
            price_paid=10.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        tmp_db.add_purchase(
            user_id=user2_id,
            gmail_message_id="msg_u2",
            item_name="User2 Item",
            price_paid=20.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-01",
        )

        u1_purchases = tmp_db.get_active_purchases(test_user_id)
        u2_purchases = tmp_db.get_active_purchases(user2_id)
        assert len(u1_purchases) == 1
        assert u1_purchases[0]["item_name"] == "User1 Item"
        assert len(u2_purchases) == 1
        assert u2_purchases[0]["item_name"] == "User2 Item"

    def test_get_active_purchases(self, tmp_db, test_user_id):
        assert tmp_db.get_active_purchases(test_user_id) == []
        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_a1",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_a2",
            item_name="B",
            price_paid=2.0,
            product_url=None,
            retailer="Y",
            purchase_date="2026-01-02",
        )
        assert len(tmp_db.get_active_purchases(test_user_id)) == 2

    def test_update_purchase_url(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
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

    def test_get_purchases_with_url(self, tmp_db, test_user_id):
        tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_no_url",
            item_name="A",
            price_paid=1.0,
            product_url=None,
            retailer="X",
            purchase_date="2026-01-01",
        )
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_with_url",
            item_name="B",
            price_paid=2.0,
            product_url="https://example.com/b",
            retailer="Y",
            purchase_date="2026-01-02",
        )
        results = tmp_db.get_purchases_with_url(test_user_id)
        assert len(results) == 1
        assert results[0]["id"] == pid

    def test_get_purchases_needing_url(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_need",
            item_name="C",
            price_paid=3.0,
            product_url=None,
            retailer="Z",
            purchase_date="2026-01-01",
        )
        assert len(tmp_db.get_purchases_needing_url(test_user_id)) == 1

        tmp_db.mark_url_search_attempted(pid)
        assert len(tmp_db.get_purchases_needing_url(test_user_id)) == 0

    def test_mark_url_search_attempted(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
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
    def test_add_and_get_price_check(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
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

    def test_price_check_with_error(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_pcerr",
            item_name="Gloves",
            price_paid=10.0,
            product_url="https://example.com/gloves",
            retailer="Winter",
            purchase_date="2026-01-01",
        )
        tmp_db.add_price_check(
            purchase_id=pid,
            current_price=None,
            status="scrape_failed",
            error_detail="Connection timeout",
        )
        latest = tmp_db.get_latest_price_check(pid)
        assert latest["status"] == "scrape_failed"
        assert latest["error_detail"] == "Connection timeout"

    def test_no_price_check_returns_none(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_nopc",
            item_name="Belt",
            price_paid=30.0,
            product_url=None,
            retailer="Leather",
            purchase_date="2026-01-01",
        )
        assert tmp_db.get_latest_price_check(pid) is None


class TestSavings:
    def test_add_and_get_savings(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_sav",
            item_name="Jacket",
            price_paid=100.0,
            product_url="https://example.com/jacket",
            retailer="OutdoorCo",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(pid, 80.0, "success")
        tmp_db.add_saving(
            purchase_id=pid,
            price_check_id=check_id,
            original_price=100.0,
            dropped_price=80.0,
            savings_amount=20.0,
        )
        new_savings = tmp_db.get_new_savings(test_user_id)
        assert len(new_savings) == 1
        assert new_savings[0]["savings_amount"] == 20.0
        assert new_savings[0]["status"] == "new"

    def test_update_saving_status(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
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
        assert tmp_db.get_new_savings(test_user_id) == []

        tmp_db.update_saving_status(save_id, "claimed")
        assert tmp_db.get_new_savings(test_user_id) == []

    def test_get_notified_savings(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_notif",
            item_name="Notified Item",
            price_paid=50.0,
            product_url="https://example.com/item",
            retailer="Shop",
            purchase_date="2026-01-01",
        )
        check_id = tmp_db.add_price_check(pid, 40.0, "success")

        s_new = tmp_db.add_saving(pid, check_id, 50.0, 40.0, 10.0)
        s_notified = tmp_db.add_saving(pid, check_id, 50.0, 35.0, 15.0)
        s_claimed = tmp_db.add_saving(pid, check_id, 50.0, 30.0, 20.0)

        tmp_db.update_saving_status(s_notified, "notified")
        tmp_db.update_saving_status(s_claimed, "claimed")

        notified = tmp_db.get_notified_savings(test_user_id)
        assert len(notified) == 1
        assert notified[0]["id"] == s_notified

    def test_savings_user_isolation(self, tmp_db, test_user_id):
        """Users can only see savings for their own purchases."""
        user2 = tmp_db.create_user("other@example.com", str(uuid.uuid4()))
        user2_id = str(user2["id"])

        # Create purchase + saving for user 1
        pid1 = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_s1",
            item_name="Item1",
            price_paid=50.0,
            product_url="https://example.com/1",
            retailer="Shop",
            purchase_date="2026-01-01",
        )
        chk1 = tmp_db.add_price_check(pid1, 40.0, "success")
        tmp_db.add_saving(pid1, chk1, 50.0, 40.0, 10.0)

        # Create purchase + saving for user 2
        pid2 = tmp_db.add_purchase(
            user_id=user2_id,
            gmail_message_id="msg_s2",
            item_name="Item2",
            price_paid=80.0,
            product_url="https://example.com/2",
            retailer="Store",
            purchase_date="2026-01-01",
        )
        chk2 = tmp_db.add_price_check(pid2, 60.0, "success")
        tmp_db.add_saving(pid2, chk2, 80.0, 60.0, 20.0)

        assert len(tmp_db.get_new_savings(test_user_id)) == 1
        assert len(tmp_db.get_new_savings(user2_id)) == 1
        assert tmp_db.get_new_savings(test_user_id)[0]["savings_amount"] == 10.0
        assert tmp_db.get_new_savings(user2_id)[0]["savings_amount"] == 20.0


class TestMetadata:
    def test_set_and_get_metadata(self, tmp_db, test_user_id):
        assert tmp_db.get_metadata(test_user_id, "last_scan") is None
        tmp_db.set_metadata(test_user_id, "last_scan", "2026-03-01")
        assert tmp_db.get_metadata(test_user_id, "last_scan") == "2026-03-01"

    def test_metadata_upsert(self, tmp_db, test_user_id):
        tmp_db.set_metadata(test_user_id, "key", "v1")
        tmp_db.set_metadata(test_user_id, "key", "v2")
        assert tmp_db.get_metadata(test_user_id, "key") == "v2"

    def test_metadata_user_isolation(self, tmp_db, test_user_id):
        user2 = tmp_db.create_user("other@example.com", str(uuid.uuid4()))
        user2_id = str(user2["id"])

        tmp_db.set_metadata(test_user_id, "last_scan", "2026-03-01")
        tmp_db.set_metadata(user2_id, "last_scan", "2026-03-15")

        assert tmp_db.get_metadata(test_user_id, "last_scan") == "2026-03-01"
        assert tmp_db.get_metadata(user2_id, "last_scan") == "2026-03-15"


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


class TestDashboard:
    def test_dashboard_summary_empty(self, tmp_db, test_user_id):
        summary = tmp_db.get_dashboard_summary(test_user_id)
        assert summary["total_purchases"] == 0
        assert summary["active_tracking"] == 0
        assert summary["total_savings_count"] == 0
        assert summary["total_savings_amount"] == 0.0
        assert summary["pending_claims"] == 0

    def test_dashboard_summary_with_data(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_dash",
            item_name="Widget",
            price_paid=50.0,
            product_url="https://example.com/widget",
            retailer="Shop",
            purchase_date="2026-01-01",
        )
        chk = tmp_db.add_price_check(pid, 40.0, "success")
        tmp_db.add_saving(pid, chk, 50.0, 40.0, 10.0)

        summary = tmp_db.get_dashboard_summary(test_user_id)
        assert summary["total_purchases"] == 1
        assert summary["active_tracking"] == 1
        assert summary["total_savings_count"] == 1
        assert summary["total_savings_amount"] == 10.0
        assert summary["pending_claims"] == 1  # status = 'new'

    def test_purchases_with_latest_check(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_lc",
            item_name="Tee",
            price_paid=30.0,
            product_url="https://example.com/tee",
            retailer="Store",
            purchase_date="2026-01-01",
        )
        tmp_db.add_price_check(pid, 25.0, "success")

        rows = tmp_db.get_purchases_with_latest_check(test_user_id)
        assert len(rows) == 1
        assert rows[0]["latest_price"] == 25.0
        assert rows[0]["check_status"] == "success"

    def test_savings_with_details(self, tmp_db, test_user_id):
        pid = tmp_db.add_purchase(
            user_id=test_user_id,
            gmail_message_id="msg_sd",
            item_name="Jacket",
            price_paid=100.0,
            product_url="https://example.com/jacket",
            retailer="OutdoorCo",
            purchase_date="2026-01-01",
        )
        chk = tmp_db.add_price_check(pid, 80.0, "success")
        tmp_db.add_saving(pid, chk, 100.0, 80.0, 20.0)

        rows = tmp_db.get_savings_with_details(test_user_id)
        assert len(rows) == 1
        assert rows[0]["item_name"] == "Jacket"
        assert rows[0]["retailer"] == "OutdoorCo"
        assert rows[0]["savings_amount"] == 20.0
