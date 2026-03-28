"""Tests for rover.policies — refund policy lookup, extraction, and domain utils."""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import yaml

from rover.db import Database
from rover.policies import PolicyLookup, RetailerInfo, _EMAIL_PATTERN, _DAYS_RE
from rover.scraper import Scraper


@pytest.fixture
def retailers_yaml(tmp_path):
    """Create a temporary retailers.yaml with test data."""
    data = {
        "retailers": [
            {
                "name": "Amazon",
                "domain": "amazon.com",
                "refund_window_days": 30,
            },
            {
                "name": "Apple",
                "domain": "apple.com",
                "refund_window_days": 14,
                "policy_url": "https://www.apple.com/shop/help/returns_refund",
            },
            {
                "name": "REI",
                "domain": "rei.com",
                "refund_window_days": 365,
            },
        ]
    }
    path = tmp_path / "retailers.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)


@pytest.fixture
def mock_scraper(scraper_config):
    """Create a scraper with mocked fetch methods."""
    scraper = Scraper(scraper_config)
    return scraper


@pytest.fixture
def policy_lookup(tmp_db, mock_scraper, retailers_yaml):
    """Create a PolicyLookup with mocked LLM client."""
    mock_client = MagicMock()
    return PolicyLookup(
        db=tmp_db,
        scraper=mock_scraper,
        llm_client=mock_client,
        llm_model="test-model",
        retailers_yaml_path=retailers_yaml,
        default_window=14,
    )


# ---------- extract_domain ----------


class TestExtractDomain:
    def test_from_url(self):
        assert PolicyLookup.extract_domain(url="https://www.amazon.com/dp/B001") == "amazon.com"

    def test_from_url_strips_www(self):
        assert PolicyLookup.extract_domain(url="https://www.target.com/p/thing") == "target.com"

    def test_from_url_no_www(self):
        assert PolicyLookup.extract_domain(url="https://rei.com/product/123") == "rei.com"

    def test_from_email_sender(self):
        assert PolicyLookup.extract_domain(email_sender="orders@abercrombie.com") == "abercrombie.com"

    def test_from_email_with_name(self):
        assert PolicyLookup.extract_domain(email_sender="Shop <orders@hollisterco.com>") == "hollisterco.com"

    def test_returns_none_for_empty(self):
        assert PolicyLookup.extract_domain() is None
        assert PolicyLookup.extract_domain(url=None, email_sender=None) is None

    def test_url_takes_precedence(self):
        result = PolicyLookup.extract_domain(
            url="https://www.amazon.com/product",
            email_sender="orders@differentsite.com",
        )
        assert result == "amazon.com"


# ---------- Seed retailers ----------


class TestSeedRetailers:
    def test_seeds_from_yaml(self, policy_lookup, tmp_db):
        amazon = tmp_db.get_retailer_by_domain("amazon.com")
        assert amazon is not None
        assert amazon["refund_window_days"] == 30
        assert amazon["source"] == "manual"

        apple = tmp_db.get_retailer_by_domain("apple.com")
        assert apple is not None
        assert apple["refund_window_days"] == 14

    def test_does_not_overwrite_scraped(self, tmp_db, mock_scraper, retailers_yaml):
        # Pre-populate with scraped data
        tmp_db.upsert_retailer("Amazon", "amazon.com", 45, source="scraped")

        mock_client = MagicMock()
        PolicyLookup(
            db=tmp_db,
            scraper=mock_scraper,
            llm_client=mock_client,
            llm_model="test-model",
            retailers_yaml_path=retailers_yaml,
        )
        # Scraped entry should be preserved
        amazon = tmp_db.get_retailer_by_domain("amazon.com")
        assert amazon["refund_window_days"] == 45
        assert amazon["source"] == "scraped"

    def test_missing_yaml_does_not_crash(self, tmp_db, mock_scraper):
        mock_client = MagicMock()
        # Should not raise
        PolicyLookup(
            db=tmp_db,
            scraper=mock_scraper,
            llm_client=mock_client,
            llm_model="test-model",
            retailers_yaml_path="/nonexistent/retailers.yaml",
        )


# ---------- get_retailer_info ----------


class TestGetRetailerInfo:
    def test_returns_known_retailer(self, policy_lookup):
        info = policy_lookup.get_retailer_info("amazon.com")
        assert isinstance(info, RetailerInfo)
        assert info.name == "Amazon"
        assert info.refund_window_days == 30
        assert info.source == "manual"

    def test_returns_default_for_unknown(self, policy_lookup, mocker):
        mocker.patch.object(policy_lookup, "_scrape_policy", return_value=None)
        info = policy_lookup.get_retailer_info("unknown-shop.com")
        assert info.refund_window_days == 14  # default_window
        assert info.domain == "unknown-shop.com"

    def test_tries_scraping_for_unknown(self, policy_lookup, mocker):
        scraped = RetailerInfo(
            name="NewShop",
            domain="newshop.com",
            refund_window_days=60,
            source="scraped",
        )
        mock_scrape = mocker.patch.object(policy_lookup, "_scrape_policy", return_value=scraped)
        info = policy_lookup.get_retailer_info("newshop.com")
        assert info.refund_window_days == 60
        mock_scrape.assert_called_once_with("newshop.com")


# ---------- is_within_refund_window ----------


class TestIsWithinRefundWindow:
    def test_within_window(self, policy_lookup):
        # Amazon has 30 days, purchase 5 days ago
        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        assert policy_lookup.is_within_refund_window(recent, "amazon.com")

    def test_outside_window(self, policy_lookup):
        # Amazon has 30 days, purchase 60 days ago
        old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        assert not policy_lookup.is_within_refund_window(old, "amazon.com")

    def test_edge_of_window(self, policy_lookup):
        # 29 days ago should still be within a 30-day window
        boundary = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
        assert policy_lookup.is_within_refund_window(boundary, "amazon.com")

    def test_rei_365_day_window(self, policy_lookup):
        # REI has 365 days
        months_ago = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        assert policy_lookup.is_within_refund_window(months_ago, "rei.com")

    def test_invalid_date_returns_false(self, policy_lookup):
        assert not policy_lookup.is_within_refund_window("not-a-date", "amazon.com")

    def test_uses_default_for_unknown(self, policy_lookup, mocker):
        mocker.patch.object(policy_lookup, "_scrape_policy", return_value=None)
        # Default is 14 days, purchase 10 days ago
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        assert policy_lookup.is_within_refund_window(recent, "unknown.com")

        # Default is 14 days, purchase 20 days ago
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
        assert not policy_lookup.is_within_refund_window(old, "unknown.com")


# ---------- _extract_policy_with_llm ----------


class TestExtractPolicyWithLlm:
    def _mock_llm_response(self, mocker, text):
        mock_block = mocker.Mock()
        mock_block.text = text
        mock_response = mocker.Mock()
        mock_response.content = [mock_block]
        return mock_response

    def test_extracts_days(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "30")
        result = policy_lookup._extract_policy_with_llm("Returns accepted within 30 days of purchase.")
        assert result["refund_window_days"] == 30

    def test_extracts_large_window(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "365")
        result = policy_lookup._extract_policy_with_llm("1 year return policy (365 days)")
        assert result["refund_window_days"] == 365

    def test_null_response(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "null")
        result = policy_lookup._extract_policy_with_llm("No clear policy found on this page.")
        assert result["refund_window_days"] is None

    def test_rejects_unreasonable_days(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "999")
        result = policy_lookup._extract_policy_with_llm("Return within 999 days")
        assert result["refund_window_days"] is None  # > 365 rejected

    def test_rejects_zero_days(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "0")
        result = policy_lookup._extract_policy_with_llm("No returns accepted")
        assert result["refund_window_days"] is None

    def test_extracts_email(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "14")
        content = "Contact us at support@shop.com for returns. 14 day window."
        result = policy_lookup._extract_policy_with_llm(content)
        assert result["support_email"] == "support@shop.com"

    def test_skips_noreply_email(self, policy_lookup, mocker):
        policy_lookup.client.messages.create.return_value = self._mock_llm_response(mocker, "30")
        content = "From: noreply@shop.com. Contact help@shop.com for returns."
        result = policy_lookup._extract_policy_with_llm(content)
        assert result["support_email"] == "help@shop.com"

    def test_api_error_returns_none_days(self, policy_lookup, mocker):
        import anthropic
        policy_lookup.client.messages.create.side_effect = anthropic.APIError(
            message="rate limit", request=mocker.Mock(), body=None,
        )
        result = policy_lookup._extract_policy_with_llm("Some policy text")
        assert result["refund_window_days"] is None


# ---------- _find_policy_links ----------


class TestFindPolicyLinks:
    def test_matches_return_link(self, policy_lookup):
        links = [
            {"text": "about us", "href": "https://shop.com/about"},
            {"text": "return policy", "href": "https://shop.com/returns"},
            {"text": "careers", "href": "https://shop.com/careers"},
        ]
        matches = policy_lookup._find_policy_links(links)
        assert len(matches) == 1
        assert matches[0]["text"] == "return policy"

    def test_matches_multiple_keywords(self, policy_lookup):
        links = [
            {"text": "refund info", "href": "https://shop.com/refund"},
            {"text": "price adjustment", "href": "https://shop.com/price-adjust"},
            {"text": "faq", "href": "https://shop.com/faq"},
        ]
        matches = policy_lookup._find_policy_links(links)
        assert len(matches) == 2

    def test_matches_keyword_in_url(self, policy_lookup):
        links = [
            {"text": "help center", "href": "https://shop.com/help/return-policy"},
        ]
        matches = policy_lookup._find_policy_links(links)
        assert len(matches) == 1

    def test_no_matches(self, policy_lookup):
        links = [
            {"text": "home", "href": "https://shop.com/"},
            {"text": "shop", "href": "https://shop.com/shop"},
        ]
        matches = policy_lookup._find_policy_links(links)
        assert len(matches) == 0


# ---------- discover_policy ----------


class TestDiscoverPolicy:
    def test_returns_default_when_all_fails(self, policy_lookup, mocker):
        mocker.patch.object(policy_lookup, "_search_policy_page", return_value=(None, None))
        mocker.patch.object(policy_lookup, "_scrape_policy", return_value=None)

        info = policy_lookup.discover_policy("mystery.com")
        assert info.refund_window_days == 30
        assert info.source == "default"

    def test_uses_search_result(self, policy_lookup, mocker):
        mocker.patch.object(
            policy_lookup, "_search_policy_page",
            return_value=("https://shop.com/policy", "Returns within 45 days"),
        )
        mocker.patch.object(
            policy_lookup, "_extract_policy_with_llm",
            return_value={"refund_window_days": 45, "support_email": None},
        )

        info = policy_lookup.discover_policy("shop.com", retailer_name="The Shop")
        assert info.refund_window_days == 45
        assert info.source == "scraped"

    def test_falls_back_to_scrape_policy(self, policy_lookup, mocker):
        mocker.patch.object(policy_lookup, "_search_policy_page", return_value=(None, None))
        scraped = RetailerInfo(
            name="Scraped", domain="scraped.com",
            refund_window_days=60, source="scraped",
        )
        mocker.patch.object(policy_lookup, "_scrape_policy", return_value=scraped)

        info = policy_lookup.discover_policy("scraped.com")
        assert info.refund_window_days == 60

    def test_passes_retailer_name_to_search(self, policy_lookup, mocker):
        mock_search = mocker.patch.object(
            policy_lookup, "_search_policy_page", return_value=(None, None),
        )
        mocker.patch.object(policy_lookup, "_scrape_policy", return_value=None)

        policy_lookup.discover_policy("on.com", retailer_name="On Running")
        mock_search.assert_called_once_with("on.com", retailer_name="On Running")


# ---------- Regex patterns ----------


class TestRegexPatterns:
    def test_email_pattern(self):
        text = "Contact support@example.com or noreply@mail.example.com"
        matches = _EMAIL_PATTERN.findall(text)
        assert "support@example.com" in matches
        assert "noreply@mail.example.com" in matches

    def test_days_regex(self):
        assert _DAYS_RE.search("30").group() == "30"
        assert _DAYS_RE.search("365 days").group() == "365"
        assert _DAYS_RE.search("null") is None
