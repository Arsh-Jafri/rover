"""Tests for rover.claimer — automated price adjustment claim emails."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from rover.claimer import ClaimManager
from rover.policies import PolicyLookup, RetailerInfo


@pytest.fixture
def mock_gmail():
    return MagicMock()


@pytest.fixture
def mock_policy_lookup():
    lookup = MagicMock(spec=PolicyLookup)
    lookup.extract_domain.return_value = "example.com"
    lookup.discover_support_email.return_value = "support@example.com"
    lookup.get_retailer_info.return_value = RetailerInfo(
        name="Example", domain="example.com",
        refund_window_days=30, source="manual",
    )
    return lookup


def _make_claimer(tmp_db, mock_gmail, mock_policy_lookup, enabled=True, customer_name="Test User"):
    config = {
        "claims": {"enabled": enabled, "customer_name": customer_name},
        "notifications": {"recipient_email": "test@example.com"},
    }
    return ClaimManager(
        config=config,
        db=tmp_db,
        gmail_client=mock_gmail,
        policy_lookup=mock_policy_lookup,
    )


def _insert_notified_saving(tmp_db, gmail_id="msg_claim", price_paid=40.0, current_price=28.0):
    """Insert a purchase + price_check + saving with status='notified'."""
    pid = tmp_db.add_purchase(
        gmail_message_id=gmail_id,
        item_name="Sale Shirt",
        price_paid=price_paid,
        product_url="https://example.com/shirt",
        retailer="TestShop",
        purchase_date=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
        order_number="ORD-123",
    )
    check_id = tmp_db.add_price_check(pid, current_price, "success")
    savings_amount = round(price_paid - current_price, 2)
    save_id = tmp_db.add_saving(pid, check_id, price_paid, current_price, savings_amount)
    tmp_db.update_saving_status(save_id, "notified")
    return save_id, pid


class TestSendClaims:
    def test_disabled_returns_early(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup, enabled=False)
        _insert_notified_saving(tmp_db)
        result = claimer.send_claims()
        assert result["sent"] == 0
        mock_gmail.send_email.assert_not_called()

    def test_no_customer_name_returns_early(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup, customer_name="")
        _insert_notified_saving(tmp_db)
        result = claimer.send_claims()
        assert result["sent"] == 0
        mock_gmail.send_email.assert_not_called()

    def test_no_notified_savings(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        result = claimer.send_claims()
        assert result == {"sent": 0, "skipped": 0, "failed": 0}

    def test_skips_retailer_without_email(self, tmp_db, mock_gmail, mock_policy_lookup):
        mock_policy_lookup.discover_support_email.return_value = None
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        _insert_notified_saving(tmp_db)

        result = claimer.send_claims()
        assert result["skipped"] == 1
        assert result["sent"] == 0
        mock_gmail.send_email.assert_not_called()
        # Should still be 'notified'
        assert len(tmp_db.get_notified_savings()) == 1

    def test_sends_claim_and_marks_claimed(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        mock_gmail.send_email.return_value = {"id": "sent_123"}
        _insert_notified_saving(tmp_db)

        result = claimer.send_claims()
        assert result["sent"] == 1
        mock_gmail.send_email.assert_called_once()
        # Verify sent to retailer support, not user
        call_args = mock_gmail.send_email.call_args
        assert call_args[0][0] == "support@example.com"
        assert "Price Adjustment" in call_args[0][1]
        # Should be marked claimed
        assert len(tmp_db.get_notified_savings()) == 0

    def test_groups_items_per_retailer(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        mock_gmail.send_email.return_value = {"id": "sent_456"}
        _insert_notified_saving(tmp_db, gmail_id="msg_1", price_paid=50.0, current_price=40.0)
        _insert_notified_saving(tmp_db, gmail_id="msg_2", price_paid=30.0, current_price=20.0)

        result = claimer.send_claims()
        assert result["sent"] == 2
        # Should send ONE email (grouped by retailer)
        mock_gmail.send_email.assert_called_once()

    def test_separate_emails_per_retailer(self, tmp_db, mock_gmail, mock_policy_lookup):
        # Two different retailers
        pid1 = tmp_db.add_purchase(
            gmail_message_id="msg_r1", item_name="Item A", price_paid=50.0,
            product_url="https://shopA.com/item", retailer="Shop A",
            purchase_date="2026-03-20", order_number="A-001",
        )
        check1 = tmp_db.add_price_check(pid1, 40.0, "success")
        save1 = tmp_db.add_saving(pid1, check1, 50.0, 40.0, 10.0)
        tmp_db.update_saving_status(save1, "notified")

        pid2 = tmp_db.add_purchase(
            gmail_message_id="msg_r2", item_name="Item B", price_paid=60.0,
            product_url="https://shopB.com/item", retailer="Shop B",
            purchase_date="2026-03-20", order_number="B-001",
        )
        check2 = tmp_db.add_price_check(pid2, 45.0, "success")
        save2 = tmp_db.add_saving(pid2, check2, 60.0, 45.0, 15.0)
        tmp_db.update_saving_status(save2, "notified")

        # Different domains for different retailers
        mock_policy_lookup.extract_domain.side_effect = lambda url: (
            "shopa.com" if "shopA" in (url or "") else "shopb.com"
        )
        mock_policy_lookup.discover_support_email.return_value = "help@shop.com"
        mock_gmail.send_email.return_value = {"id": "sent"}

        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        result = claimer.send_claims()
        assert result["sent"] == 2
        assert mock_gmail.send_email.call_count == 2

    def test_send_failure_leaves_notified(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        mock_gmail.send_email.side_effect = RuntimeError("SMTP error")
        _insert_notified_saving(tmp_db)

        result = claimer.send_claims()
        assert result["failed"] == 1
        assert result["sent"] == 0
        assert len(tmp_db.get_notified_savings()) == 1

    def test_does_not_reclaim_already_claimed(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        save_id, _ = _insert_notified_saving(tmp_db)
        tmp_db.update_saving_status(save_id, "claimed")

        result = claimer.send_claims()
        assert result["sent"] == 0
        mock_gmail.send_email.assert_not_called()


class TestBuildClaimMessage:
    def test_contains_customer_name(self):
        items = [{"item_name": "Tee", "order_number": "ORD-1", "purchase_date": "2026-03-15",
                  "price_paid": 30.0, "current_price": 20.0, "savings_amount": 10.0, "product_url": "https://shop.com/tee"}]
        msg = ClaimManager.build_claim_message("Alice", items, "CoolShop")
        assert "Alice" in msg

    def test_contains_order_number(self):
        items = [{"item_name": "Tee", "order_number": "ORD-999", "purchase_date": "2026-03-15",
                  "price_paid": 30.0, "current_price": 20.0, "savings_amount": 10.0, "product_url": ""}]
        msg = ClaimManager.build_claim_message("Bob", items, "Shop")
        assert "ORD-999" in msg

    def test_contains_prices_and_savings(self):
        items = [{"item_name": "Hat", "order_number": "", "purchase_date": "2026-03-01",
                  "price_paid": 45.00, "current_price": 30.00, "savings_amount": 15.00, "product_url": ""}]
        msg = ClaimManager.build_claim_message("Carol", items, "HatStore")
        assert "$45.00" in msg
        assert "$30.00" in msg
        assert "$15.00" in msg

    def test_contains_product_url(self):
        items = [{"item_name": "Shoe", "order_number": "S-1", "purchase_date": "2026-03-10",
                  "price_paid": 100.0, "current_price": 80.0, "savings_amount": 20.0,
                  "product_url": "https://shoes.com/running-shoe"}]
        msg = ClaimManager.build_claim_message("Dave", items, "ShoeStore")
        assert "https://shoes.com/running-shoe" in msg

    def test_multiple_items(self):
        items = [
            {"item_name": "Item A", "order_number": "O-1", "purchase_date": "2026-03-01",
             "price_paid": 20.0, "current_price": 15.0, "savings_amount": 5.0, "product_url": ""},
            {"item_name": "Item B", "order_number": "O-1", "purchase_date": "2026-03-01",
             "price_paid": 40.0, "current_price": 30.0, "savings_amount": 10.0, "product_url": ""},
        ]
        msg = ClaimManager.build_claim_message("Eve", items, "MultiShop")
        assert "Item A" in msg
        assert "Item B" in msg
        assert "$15.00" in msg  # total

    def test_retailer_name_in_greeting(self):
        items = [{"item_name": "X", "order_number": "", "purchase_date": "",
                  "price_paid": 10.0, "current_price": 5.0, "savings_amount": 5.0, "product_url": ""}]
        msg = ClaimManager.build_claim_message("Frank", items, "Fancy Retailer")
        assert "Dear Fancy Retailer Customer Service" in msg


class TestBuildClaimHtml:
    def test_wraps_in_html(self):
        items = [{"item_name": "Tee", "order_number": "ORD-1", "purchase_date": "2026-03-15",
                  "price_paid": 30.0, "current_price": 20.0, "savings_amount": 10.0, "product_url": ""}]
        html = ClaimManager._build_claim_html("Alice", items, "Shop")
        assert "<!DOCTYPE html>" in html
        assert "Alice" in html
        assert "Shop" in html


class TestBuildSubject:
    def test_single_order(self):
        items = [{"order_number": "ORD-123"}]
        assert "ORD-123" in ClaimManager._build_subject(items)

    def test_multiple_orders(self):
        items = [{"order_number": "ORD-1"}, {"order_number": "ORD-2"}]
        subject = ClaimManager._build_subject(items)
        assert "ORD-1" in subject
        assert "ORD-2" in subject

    def test_no_order_number(self):
        items = [{"order_number": ""}]
        assert ClaimManager._build_subject(items) == "Price Adjustment Request"


class TestSendTestClaim:
    def test_sends_to_user(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        mock_gmail.send_email.return_value = {"id": "test_123"}

        result = claimer.send_test_claim()
        assert result is True
        mock_gmail.send_email.assert_called_once()
        assert mock_gmail.send_email.call_args[0][0] == "test@example.com"

    def test_no_recipient_returns_false(self, tmp_db, mock_gmail, mock_policy_lookup):
        config = {"claims": {"enabled": True, "customer_name": "Test"}, "notifications": {}}
        claimer = ClaimManager(config=config, db=tmp_db, gmail_client=mock_gmail, policy_lookup=mock_policy_lookup)
        assert claimer.send_test_claim() is False

    def test_send_failure_returns_false(self, tmp_db, mock_gmail, mock_policy_lookup):
        claimer = _make_claimer(tmp_db, mock_gmail, mock_policy_lookup)
        mock_gmail.send_email.side_effect = RuntimeError("API down")
        assert claimer.send_test_claim() is False
