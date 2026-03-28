"""Tests for rover.price_checker — price extraction, URL discovery, and savings detection."""

from unittest.mock import MagicMock

import pytest

from rover.db import Database
from rover.policies import PolicyLookup, RetailerInfo
from rover.price_checker import PriceChecker, _PRICE_RE, PRICE_EXTRACTION_PROMPT
from rover.scraper import Scraper


@pytest.fixture
def mock_scraper(scraper_config):
    scraper = Scraper(scraper_config)
    return scraper


@pytest.fixture
def mock_policy_lookup():
    lookup = MagicMock(spec=PolicyLookup)
    lookup.extract_domain.return_value = "example.com"
    lookup.is_within_refund_window.return_value = True
    return lookup


@pytest.fixture
def price_checker(anthropic_config, tmp_db, mock_scraper, mock_policy_lookup, mocker):
    # Patch Anthropic client creation so it doesn't need a real API key
    mock_client = MagicMock()
    mocker.patch("rover.price_checker.anthropic.Anthropic", return_value=mock_client)
    checker = PriceChecker(
        config=anthropic_config,
        db=tmp_db,
        scraper=mock_scraper,
        policy_lookup=mock_policy_lookup,
    )
    return checker


def _add_purchase(db, **overrides):
    """Helper to insert a real purchase into the DB and return the dict."""
    defaults = {
        "gmail_message_id": "msg_test",
        "item_name": "Test Item",
        "price_paid": 25.00,
        "product_url": "https://example.com/item",
        "retailer": "TestShop",
        "purchase_date": "2026-03-01",
    }
    defaults.update(overrides)
    pid = db.add_purchase(**defaults)
    return db.get_purchase(pid)


# ---------- _PRICE_RE ----------


class TestPriceRegex:
    def test_integer(self):
        assert _PRICE_RE.search("14").group() == "14"

    def test_decimal(self):
        assert _PRICE_RE.search("14.95").group() == "14.95"

    def test_three_digits(self):
        assert _PRICE_RE.search("129.99").group() == "129.99"

    def test_extracts_from_text(self):
        assert _PRICE_RE.search("The price is 24.99 USD").group() == "24.99"

    def test_no_match(self):
        assert _PRICE_RE.search("null") is None
        assert _PRICE_RE.search("no price found") is None


# ---------- _extract_price ----------


class TestExtractPrice:
    def _mock_llm_response(self, mocker, text):
        mock_block = mocker.Mock()
        mock_block.text = text
        mock_response = mocker.Mock()
        mock_response.content = [mock_block]
        return mock_response

    def test_extracts_simple_price(self, price_checker, mocker):
        price_checker.client.messages.create.return_value = self._mock_llm_response(mocker, "14.95")
        result = price_checker._extract_price("Product page content with price $14.95", "Classic Tee")
        assert result == 14.95

    def test_extracts_integer_price(self, price_checker, mocker):
        price_checker.client.messages.create.return_value = self._mock_llm_response(mocker, "25")
        result = price_checker._extract_price("Price: $25", "Basic Hat")
        assert result == 25.0

    def test_null_response(self, price_checker, mocker):
        price_checker.client.messages.create.return_value = self._mock_llm_response(mocker, "null")
        result = price_checker._extract_price("Product is sold out", "Sold Shirt")
        assert result is None

    def test_extracts_number_even_with_text(self, price_checker, mocker):
        """If LLM returns verbose text with a number, extract the number."""
        price_checker.client.messages.create.return_value = self._mock_llm_response(
            mocker, "The current price is 29.99"
        )
        result = price_checker._extract_price("Some content", "Item")
        assert result == 29.99

    def test_api_error_returns_none(self, price_checker, mocker):
        import anthropic
        price_checker.client.messages.create.side_effect = anthropic.APIError(
            message="error", request=mocker.Mock(), body=None,
        )
        result = price_checker._extract_price("content", "item")
        assert result is None

    def test_uses_temperature_zero(self, price_checker, mocker):
        mock_resp = self._mock_llm_response(mocker, "19.99")
        price_checker.client.messages.create.return_value = mock_resp

        price_checker._extract_price("content", "item")
        call_kwargs = price_checker.client.messages.create.call_args[1]
        assert call_kwargs["temperature"] == 0

    def test_uses_max_tokens_32(self, price_checker, mocker):
        mock_resp = self._mock_llm_response(mocker, "19.99")
        price_checker.client.messages.create.return_value = mock_resp

        price_checker._extract_price("content", "item")
        call_kwargs = price_checker.client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 32

    def test_prompt_includes_item_name(self, price_checker, mocker):
        mock_resp = self._mock_llm_response(mocker, "19.99")
        price_checker.client.messages.create.return_value = mock_resp

        price_checker._extract_price("page content", "Blue Running Shoe")
        call_kwargs = price_checker.client.messages.create.call_args[1]
        prompt = call_kwargs["messages"][0]["content"]
        assert "Blue Running Shoe" in prompt
        assert "page content" in prompt


# ---------- check_price ----------


class TestCheckPrice:
    def test_successful_price_check_no_drop(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_nodrop", price_paid=24.99)
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html><body>Price: $24.99</body></html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Product: Classic Tee\nPrice: $24.99\n" + "x" * 100)
        mocker.patch.object(price_checker, "_extract_price", return_value=24.99)

        result = price_checker.check_price(purchase)
        assert result["status"] == "success"
        assert result["price_dropped"] is False
        assert result["current_price"] == 24.99

    def test_price_drop_detected(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_drop", price_paid=40.00)
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>content</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Price: $28.00\n" + "x" * 100)
        mocker.patch.object(price_checker, "_extract_price", return_value=28.00)

        result = price_checker.check_price(purchase)
        assert result["price_dropped"] is True
        assert result["savings_amount"] == 12.00
        assert result["current_price"] == 28.00

        # Verify saving was recorded in DB
        savings = tmp_db.get_new_savings()
        assert len(savings) == 1
        assert savings[0]["savings_amount"] == 12.00

    def test_scrape_failed(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_sf")
        mocker.patch.object(price_checker.scraper, "fetch", return_value=None)

        result = price_checker.check_price(purchase)
        assert result["status"] == "scrape_failed"
        assert result["price_dropped"] is False

    def test_parse_failed(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_pf")
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>content</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Some content without clear price\n" + "x" * 100)
        mocker.patch.object(price_checker, "_extract_price", return_value=None)

        result = price_checker.check_price(purchase)
        assert result["status"] == "parse_failed"
        assert result["price_dropped"] is False

    def test_no_url_returns_none(self, price_checker, tmp_db):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_nourl", product_url=None)
        result = price_checker.check_price(purchase)
        assert result is None

    def test_short_content_is_scrape_failed(self, price_checker, tmp_db, mocker):
        """Pages with very little content should be marked scrape_failed."""
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_short")
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>tiny</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="tiny")

        result = price_checker.check_price(purchase)
        assert result["status"] == "scrape_failed"

    def test_sold_out_detection(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_sold")
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>sold out</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Sold Out!")

        result = price_checker.check_price(purchase)
        assert result["status"] == "scrape_failed"

    def test_price_same_no_savings_record(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_same", price_paid=50.00)
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>content</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Price: $50.00\n" + "x" * 100)
        mocker.patch.object(price_checker, "_extract_price", return_value=50.00)

        result = price_checker.check_price(purchase)
        assert result["price_dropped"] is False
        assert tmp_db.get_new_savings() == []

    def test_price_increase_no_savings_record(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_up", price_paid=30.00)
        mocker.patch.object(price_checker.scraper, "fetch", return_value="<html>content</html>")
        mocker.patch.object(price_checker.scraper, "clean_html", return_value="Price: $45.00\n" + "x" * 100)
        mocker.patch.object(price_checker, "_extract_price", return_value=45.00)

        result = price_checker.check_price(purchase)
        assert result["price_dropped"] is False


# ---------- check_all_prices ----------


class TestCheckAllPrices:
    def test_filters_by_refund_window(self, price_checker, tmp_db, mocker):
        p1 = _add_purchase(tmp_db, gmail_message_id="msg_in", item_name="In Window",
                           price_paid=50.0, purchase_date="2026-03-20")
        p2 = _add_purchase(tmp_db, gmail_message_id="msg_out", item_name="Out Window",
                           price_paid=60.0, purchase_date="2026-01-01")

        mocker.patch.object(price_checker, "discover_product_urls", return_value=0)

        # First call within window, second outside
        price_checker.policy_lookup.is_within_refund_window.side_effect = [True, False]

        mock_check = mocker.patch.object(price_checker, "check_price", return_value={
            "purchase_id": p1["id"], "price_dropped": False,
        })

        price_checker.check_all_prices()
        # Only the in-window purchase should be checked
        mock_check.assert_called_once()

    def test_calls_discover_urls_first(self, price_checker, tmp_db, mocker):
        mock_discover = mocker.patch.object(price_checker, "discover_product_urls", return_value=0)
        price_checker.check_all_prices()
        mock_discover.assert_called_once()

    def test_returns_only_drops(self, price_checker, tmp_db, mocker):
        p1 = _add_purchase(tmp_db, gmail_message_id="msg_d1", item_name="Drop", price_paid=50.0)
        p2 = _add_purchase(tmp_db, gmail_message_id="msg_nd", item_name="No Drop", price_paid=30.0)

        mocker.patch.object(price_checker, "discover_product_urls", return_value=0)

        results = [
            {"purchase_id": p1["id"], "price_dropped": True, "savings_amount": 10.0},
            {"purchase_id": p2["id"], "price_dropped": False},
        ]
        mocker.patch.object(price_checker, "check_price", side_effect=results)

        drops = price_checker.check_all_prices()
        assert len(drops) == 1
        assert drops[0]["savings_amount"] == 10.0

    def test_skips_purchases_without_domain(self, price_checker, tmp_db, mocker):
        _add_purchase(tmp_db, gmail_message_id="msg_nodom")
        mocker.patch.object(price_checker, "discover_product_urls", return_value=0)
        price_checker.policy_lookup.extract_domain.return_value = None

        mock_check = mocker.patch.object(price_checker, "check_price")
        price_checker.check_all_prices()
        mock_check.assert_not_called()


# ---------- discover_product_urls ----------


class TestDiscoverProductUrls:
    def test_finds_url_and_updates_db(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_disco",
                                 item_name="New Shoe", retailer="Nike", product_url=None)
        mocker.patch("time.sleep")
        mocker.patch.object(
            price_checker, "_find_product_url",
            return_value=("https://nike.com/shoe/123", False),
        )

        found = price_checker.discover_product_urls()
        assert found == 1
        updated = tmp_db.get_purchase(purchase["id"])
        assert updated["product_url"] == "https://nike.com/shoe/123"
        assert updated["url_search_attempted"] == 1

    def test_marks_attempted_when_not_found(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_nf",
                                 item_name="Mystery", product_url=None)
        mocker.patch("time.sleep")
        mocker.patch.object(price_checker, "_find_product_url", return_value=(None, False))

        found = price_checker.discover_product_urls()
        assert found == 0
        updated = tmp_db.get_purchase(purchase["id"])
        assert updated["url_search_attempted"] == 1

    def test_does_not_mark_on_rate_limit(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_rl",
                                 item_name="Rate Limited", product_url=None)
        mocker.patch("time.sleep")
        mocker.patch.object(price_checker, "_find_product_url", return_value=(None, True))

        found = price_checker.discover_product_urls()
        assert found == 0
        updated = tmp_db.get_purchase(purchase["id"])
        assert updated["url_search_attempted"] == 0  # Not marked — will retry

    def test_skips_items_without_name_or_retailer(self, price_checker, tmp_db, mocker):
        purchase = _add_purchase(tmp_db, gmail_message_id="msg_empty",
                                 item_name="", retailer="", product_url=None)
        mocker.patch("time.sleep")
        mock_find = mocker.patch.object(price_checker, "_find_product_url")

        price_checker.discover_product_urls()
        mock_find.assert_not_called()
        # Should still mark as attempted
        updated = tmp_db.get_purchase(purchase["id"])
        assert updated["url_search_attempted"] == 1


# ---------- PRICE_EXTRACTION_PROMPT ----------


class TestPriceExtractionPrompt:
    def test_prompt_structure(self):
        formatted = PRICE_EXTRACTION_PROMPT.format(
            item_name="Blue Tee",
            content="Page content here",
        )
        assert "Blue Tee" in formatted
        assert "Page content here" in formatted
        assert "selling price" in formatted.lower()
        assert "null" in formatted
        assert "sale" in formatted.lower() or "clearance" in formatted.lower()
