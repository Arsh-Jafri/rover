"""Tests for rover.parser — receipt detection and LLM-based parsing."""

import pytest

from rover.parser import ReceiptParser, _clean_html_to_text


# Use the static method directly for convenience
is_likely_receipt = ReceiptParser.is_likely_receipt


# ---------- is_likely_receipt ----------


class TestIsLikelyReceipt:
    def test_obvious_receipt(self):
        assert is_likely_receipt(
            subject="Your order confirmation #12345",
            body_text="Thank you for your purchase! Total: $49.99",
        )

    def test_receipt_with_html_only(self):
        assert is_likely_receipt(
            subject="Order Confirmation",
            body_text="",
            body_html="<p>Your order total is $29.99</p>",
        )

    def test_no_price_returns_false(self):
        assert not is_likely_receipt(
            subject="Your order has been placed",
            body_text="Thank you for your order. We'll send a confirmation soon.",
        )

    def test_no_order_keyword_returns_false(self):
        assert not is_likely_receipt(
            subject="Hello there",
            body_text="Check out this deal for only $19.99!",
        )

    def test_shipping_email_rejected(self):
        assert not is_likely_receipt(
            subject="Your order has shipped",
            body_text="Your order #123 ($49.99) is on its way!",
        )

    def test_refund_email_rejected(self):
        assert not is_likely_receipt(
            subject="Your refund has been processed",
            body_text="We've issued a refund of $30.00 to your order #456",
        )

    def test_subscription_email_rejected(self):
        assert not is_likely_receipt(
            subject="Your subscription renewal",
            body_text="Your Netflix subscription for $15.99 has been renewed. Order #789",
        )

    def test_receipt_override_for_shipping(self):
        """Subject with 'receipt' should override shipping pattern."""
        assert is_likely_receipt(
            subject="Your e-receipt for shipped order",
            body_text="Order #123 Total: $49.99",
        )

    def test_doordash_ignored(self):
        assert not is_likely_receipt(
            subject="Your DoorDash order confirmation",
            body_text="Order total: $25.99",
            sender="no-reply@doordash.com",
        )

    def test_instacart_ignored(self):
        assert not is_likely_receipt(
            subject="Your Instacart receipt",
            body_text="Total: $89.50",
            sender="orders@instacart.com",
        )

    def test_ubereats_ignored(self):
        assert not is_likely_receipt(
            subject="Your Uber Eats receipt",
            body_text="Total: $19.99",
            sender="uber.us@uber eats.com",
        )

    def test_grubhub_ignored(self):
        assert not is_likely_receipt(
            subject="Your Grubhub order",
            body_text="Total: $32.50",
            sender="orders@grubhub.com",
        )

    def test_various_price_formats(self):
        """Test different price patterns."""
        assert is_likely_receipt("Order confirmation", "$19.99 total for your order")
        assert is_likely_receipt("Order confirmation", "USD 19.99 total for your order")
        assert is_likely_receipt("Order confirmation", "19.99 USD total for your order")
        assert is_likely_receipt("Order confirmation", "Total: $19 for your order")

    def test_price_in_subject(self):
        assert is_likely_receipt(
            subject="Order confirmation - $49.99",
            body_text="Thank you for your purchase",
        )

    def test_delivered_rejected(self):
        assert not is_likely_receipt(
            subject="Your package has been delivered!",
            body_text="Order #100 ($15.00) was delivered today.",
        )


# ---------- _clean_html_to_text ----------


class TestCleanHtmlToText:
    def test_strips_tags(self):
        html = "<html><body><p>Hello <b>World</b></p></body></html>"
        text = _clean_html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "<p>" not in text

    def test_preserves_links(self):
        html = '<p>Visit <a href="https://example.com">Example</a></p>'
        text = _clean_html_to_text(html)
        assert "Example" in text
        assert "https://example.com" in text

    def test_strips_scripts_and_styles(self):
        html = """<html>
        <style>body{color:red}</style>
        <script>alert('bad')</script>
        <p>Good content</p>
        </html>"""
        text = _clean_html_to_text(html)
        assert "Good content" in text
        assert "alert" not in text
        assert "color:red" not in text

    def test_removes_long_tracking_urls(self):
        long_url = "https://tracking.example.com/" + "a" * 250
        html = f'<p>Click <a href="{long_url}">here</a></p>'
        text = _clean_html_to_text(html)
        assert "a" * 200 not in text

    def test_collapses_filler_characters(self):
        html = "<p>Hello\u200b\u200b\u200bWorld</p>"
        text = _clean_html_to_text(html)
        assert "Hello" in text
        assert "World" in text
        assert "\u200b" not in text


# ---------- parse_receipt (with mocked LLM) ----------


class TestParseReceipt:
    def _make_llm_response(self, mocker, tool_input):
        """Create a mock Claude tool_use response."""
        mock_block = mocker.Mock()
        mock_block.type = "tool_use"
        mock_block.name = "extract_receipt"
        mock_block.input = tool_input

        mock_response = mocker.Mock()
        mock_response.content = [mock_block]
        return mock_response

    def test_parses_single_item_receipt(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "Acme Store",
            "purchase_date": "2026-03-15",
            "currency": "USD",
            "order_number": "ACM-001",
            "items": [
                {"item_name": "Widget", "price_paid": 29.99, "product_url": None}
            ],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        items = parser.parse_receipt("Order confirmation", "shop@acme.com", "Your order for Widget $29.99")
        assert items is not None
        assert len(items) == 1
        assert items[0]["item_name"] == "Widget"
        assert items[0]["price_paid"] == 29.99
        assert items[0]["retailer"] == "Acme Store"
        assert items[0]["order_number"] == "ACM-001"

    def test_parses_multi_item_receipt(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "Fashion Co",
            "purchase_date": "2026-03-10",
            "currency": "USD",
            "items": [
                {"item_name": "Shirt", "price_paid": 24.99},
                {"item_name": "Pants", "price_paid": 49.99},
            ],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        items = parser.parse_receipt("Your receipt", "shop@fashion.com", "Shirt $24.99 Pants $49.99")
        assert len(items) == 2

    def test_filters_non_receipt_email_type(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "shipping",
            "retailer": "Acme",
            "purchase_date": "2026-03-01",
            "currency": "USD",
            "items": [{"item_name": "Widget", "price_paid": 10.0}],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        result = parser.parse_receipt("Your shipment", "ship@acme.com", "Shipped: Widget $10")
        assert result is None

    def test_filters_zero_price_items(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "FreeStuff",
            "purchase_date": "2026-03-01",
            "currency": "USD",
            "items": [
                {"item_name": "Free Gift", "price_paid": 0.0},
                {"item_name": "Real Item", "price_paid": 15.0},
            ],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        items = parser.parse_receipt("Your order", "shop@free.com", "Order total: $15.00")
        assert len(items) == 1
        assert items[0]["item_name"] == "Real Item"

    def test_returns_none_on_empty_body(self, anthropic_config):
        parser = ReceiptParser(anthropic_config)
        result = parser.parse_receipt("Subject", "sender@x.com", "", body_html=None)
        assert result is None

    def test_prefers_html_body(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "HTML Corp",
            "purchase_date": "2026-03-01",
            "currency": "USD",
            "items": [{"item_name": "Item", "price_paid": 10.0}],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mock_create = mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        parser.parse_receipt(
            "Receipt",
            "shop@html.com",
            "plain text body",
            body_html="<p>HTML body with order total $10.00</p>",
        )
        # The prompt sent to LLM should contain cleaned HTML, not plain text
        call_args = mock_create.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "HTML body" in prompt

    def test_passes_email_date_to_prompt(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "DateCo",
            "purchase_date": "2026-03-15",
            "currency": "USD",
            "items": [{"item_name": "Thing", "price_paid": 5.0}],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mock_create = mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        parser.parse_receipt("Receipt", "shop@date.com", "Total: $5.00", email_date="Sat, 15 Mar 2026 10:00:00 -0500")
        prompt = mock_create.call_args[1]["messages"][0]["content"]
        assert "15 Mar 2026" in prompt

    def test_returns_none_for_all_invalid_prices(self, mocker, anthropic_config):
        parser = ReceiptParser(anthropic_config)

        tool_input = {
            "email_type": "receipt",
            "retailer": "BadPrice",
            "purchase_date": "2026-01-01",
            "currency": "USD",
            "items": [
                {"item_name": "Broken", "price_paid": "not_a_number"},
                {"item_name": "Negative", "price_paid": -5.0},
            ],
        }
        mock_resp = self._make_llm_response(mocker, tool_input)
        mocker.patch.object(parser.client.messages, "create", return_value=mock_resp)

        result = parser.parse_receipt("Order", "x@y.com", "Total: $0.00")
        assert result is None
