"""Tests for rover.notifier — email notifications for price drops."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from rover.notifier import NotificationManager
from rover.policies import PolicyLookup, RetailerInfo


@pytest.fixture
def mock_policy_lookup():
    lookup = MagicMock(spec=PolicyLookup)
    lookup.extract_domain.return_value = "example.com"
    lookup.get_retailer_info.return_value = RetailerInfo(
        name="Example", domain="example.com",
        refund_window_days=30, source="manual",
    )
    return lookup


def _make_notifier(tmp_db, mock_policy_lookup, enabled=True, recipient="test@example.com"):
    config = {
        "notifications": {
            "enabled": enabled,
            "recipient_email": recipient,
            "sender_email": "rover@tryrover.app",
            "sender_name": "Rover",
        }
    }
    return NotificationManager(
        config=config,
        db=tmp_db,
        policy_lookup=mock_policy_lookup,
    )


def _insert_drop(tmp_db, gmail_id="msg_drop", price_paid=40.0, current_price=28.0):
    """Insert a purchase, price check, and saving. Returns a drop dict."""
    pid = tmp_db.add_purchase(
        gmail_message_id=gmail_id,
        item_name="Sale Shirt",
        price_paid=price_paid,
        product_url="https://example.com/shirt",
        retailer="TestShop",
        purchase_date=(datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
    )
    check_id = tmp_db.add_price_check(pid, current_price, "success")
    savings_amount = round(price_paid - current_price, 2)
    save_id = tmp_db.add_saving(pid, check_id, price_paid, current_price, savings_amount)
    return {
        "purchase_id": pid,
        "price_check_id": check_id,
        "item_name": "Sale Shirt",
        "price_paid": price_paid,
        "current_price": current_price,
        "savings_amount": savings_amount,
        "saving_id": save_id,
        "status": "success",
        "price_dropped": True,
    }


class TestNotifyDrops:
    @patch("rover.notifier.send_email")
    def test_disabled_returns_false(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup, enabled=False)
        drop = _insert_drop(tmp_db)
        assert notifier.notify_drops([drop]) is False
        mock_send.assert_not_called()

    @patch("rover.notifier.send_email")
    def test_no_recipient_returns_false(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup, recipient="")
        drop = _insert_drop(tmp_db)
        assert notifier.notify_drops([drop]) is False
        mock_send.assert_not_called()

    @patch("rover.notifier.send_email")
    def test_empty_drops_returns_false(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        assert notifier.notify_drops([]) is False

    @patch("rover.notifier.send_email")
    def test_success_sends_email_and_marks_notified(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        drop = _insert_drop(tmp_db)
        mock_send.return_value = {"id": "sent_123"}

        result = notifier.notify_drops([drop])

        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "test@example.com"  # recipient
        assert "$12.00" in call_args[0][1]  # subject contains savings
        assert "1 item" in call_args[0][1]  # subject contains count
        assert call_args[0][3] == "rover@tryrover.app"  # sender_email

        # Saving should be marked as notified
        new_savings = tmp_db.get_new_savings()
        assert len(new_savings) == 0

    @patch("rover.notifier.send_email")
    def test_multiple_drops_in_one_email(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        drop1 = _insert_drop(tmp_db, gmail_id="msg_1", price_paid=50.0, current_price=40.0)
        drop2 = _insert_drop(tmp_db, gmail_id="msg_2", price_paid=30.0, current_price=20.0)
        mock_send.return_value = {"id": "sent_456"}

        result = notifier.notify_drops([drop1, drop2])

        assert result is True
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "$20.00" in subject  # 10 + 10
        assert "2 items" in subject

        # Both savings should be notified
        assert len(tmp_db.get_new_savings()) == 0

    @patch("rover.notifier.send_email")
    def test_send_failure_leaves_savings_new(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        drop = _insert_drop(tmp_db)
        mock_send.side_effect = RuntimeError("API error")

        result = notifier.notify_drops([drop])

        assert result is False
        # Saving should still be "new" — not marked notified
        new_savings = tmp_db.get_new_savings()
        assert len(new_savings) == 1

    @patch("rover.notifier.send_email")
    def test_missing_purchase_skipped(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        fake_drop = {
            "purchase_id": 9999,
            "current_price": 10.0,
            "savings_amount": 5.0,
            "saving_id": None,
        }
        result = notifier.notify_drops([fake_drop])
        assert result is False


class TestBuildHtml:
    def test_contains_drop_info(self):
        drops = [{
            "item_name": "Blue Tee",
            "retailer": "CoolShop",
            "price_paid": 29.99,
            "current_price": 19.99,
            "savings_amount": 10.00,
            "product_url": "https://coolshop.com/blue-tee",
            "days_remaining": 12,
        }]
        html = NotificationManager._build_html(drops, 10.00)
        assert "Blue Tee" in html
        assert "CoolShop" in html
        assert "$29.99" in html
        assert "$19.99" in html
        assert "$10.00" in html
        assert "coolshop.com/blue-tee" in html
        assert "12d" in html

    def test_days_remaining_colors(self):
        drops_red = [{
            "item_name": "Urgent", "retailer": "X", "price_paid": 10.0,
            "current_price": 5.0, "savings_amount": 5.0, "product_url": "", "days_remaining": 2,
        }]
        html_red = NotificationManager._build_html(drops_red, 5.0)
        assert "D32F2F" in html_red  # red color

        drops_amber = [{
            "item_name": "Soon", "retailer": "X", "price_paid": 10.0,
            "current_price": 5.0, "savings_amount": 5.0, "product_url": "", "days_remaining": 5,
        }]
        html_amber = NotificationManager._build_html(drops_amber, 5.0)
        assert "d97706" in html_amber  # amber color

    def test_no_days_shows_dash(self):
        drops = [{
            "item_name": "Item", "retailer": "X", "price_paid": 10.0,
            "current_price": 5.0, "savings_amount": 5.0, "product_url": "", "days_remaining": None,
        }]
        html = NotificationManager._build_html(drops, 5.0)
        # No days badge should be rendered when days_remaining is None
        assert "d left" not in html

    def test_total_savings_in_header(self):
        drops = [{
            "item_name": "Item", "retailer": "X", "price_paid": 100.0,
            "current_price": 75.0, "savings_amount": 25.0, "product_url": "", "days_remaining": 10,
        }]
        html = NotificationManager._build_html(drops, 25.00)
        assert "$25.00" in html

    def test_product_url_linked(self):
        drops = [{
            "item_name": "Linked Item", "retailer": "X", "price_paid": 10.0,
            "current_price": 5.0, "savings_amount": 5.0,
            "product_url": "https://shop.com/product/123", "days_remaining": 10,
        }]
        html = NotificationManager._build_html(drops, 5.0)
        assert 'href="https://shop.com/product/123"' in html

    def test_no_url_no_link(self):
        drops = [{
            "item_name": "Unlinked", "retailer": "X", "price_paid": 10.0,
            "current_price": 5.0, "savings_amount": 5.0,
            "product_url": "", "days_remaining": 10,
        }]
        html = NotificationManager._build_html(drops, 5.0)
        assert "Unlinked" in html
        assert 'href=""' not in html


class TestDaysRemaining:
    def test_calculates_correctly(self, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        # Purchase 10 days ago, 30-day window -> 20 days remaining
        purchase_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_days",
            item_name="Day Test",
            price_paid=50.0,
            product_url="https://example.com/test",
            retailer="DayCo",
            purchase_date=purchase_date,
        )
        check_id = tmp_db.add_price_check(pid, 40.0, "success")
        save_id = tmp_db.add_saving(pid, check_id, 50.0, 40.0, 10.0)

        drop = {
            "purchase_id": pid, "current_price": 40.0,
            "savings_amount": 10.0, "saving_id": save_id,
        }
        enriched = notifier._enrich_drops([drop])
        assert len(enriched) == 1
        # Should be approximately 20 days (±1 for time-of-day rounding)
        assert 19 <= enriched[0]["days_remaining"] <= 21

    def test_expired_window_shows_zero(self, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        # Purchase 60 days ago, 30-day window -> 0 days remaining
        purchase_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        pid = tmp_db.add_purchase(
            gmail_message_id="msg_expired",
            item_name="Expired",
            price_paid=50.0,
            product_url="https://example.com/old",
            retailer="OldCo",
            purchase_date=purchase_date,
        )
        check_id = tmp_db.add_price_check(pid, 30.0, "success")
        save_id = tmp_db.add_saving(pid, check_id, 50.0, 30.0, 20.0)

        drop = {
            "purchase_id": pid, "current_price": 30.0,
            "savings_amount": 20.0, "saving_id": save_id,
        }
        enriched = notifier._enrich_drops([drop])
        assert enriched[0]["days_remaining"] == 0


class TestSendTestNotification:
    @patch("rover.notifier.send_email")
    def test_sends_fake_data(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        mock_send.return_value = {"id": "test_123"}

        result = notifier.send_test_notification()

        assert result is True
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "Test" in subject

    @patch("rover.notifier.send_email")
    def test_no_recipient_returns_false(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup, recipient="")
        result = notifier.send_test_notification()
        assert result is False

    @patch("rover.notifier.send_email")
    def test_send_failure_returns_false(self, mock_send, tmp_db, mock_policy_lookup):
        notifier = _make_notifier(tmp_db, mock_policy_lookup)
        mock_send.side_effect = RuntimeError("API down")
        result = notifier.send_test_notification()
        assert result is False
