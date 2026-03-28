"""Tests for rover.scraper — HTML fetching, cleaning, and JSON-LD extraction."""

import json

import pytest
from bs4 import BeautifulSoup

from rover.scraper import Scraper, CONTENT_SELECTORS, MAX_CLEANED_LENGTH


@pytest.fixture
def scraper(scraper_config):
    return Scraper(scraper_config)


# ---------- JSON-LD extraction ----------


class TestExtractJsonLd:
    def test_basic_product(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Classic Tee", "offers": {"price": "14.95", "priceCurrency": "USD", "availability": "https://schema.org/InStock"}}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert result is not None
        assert "Classic Tee" in result
        assert "14.95" in result
        assert "In Stock" in result

    def test_product_in_graph(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage", "name": "Shop"},
            {"@type": "Product", "name": "Sneaker", "offers": {"price": "99.00", "priceCurrency": "USD"}}
        ]}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert result is not None
        assert "Sneaker" in result
        assert "99.00" in result

    def test_product_in_list(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        [{"@type": "Product", "name": "Hat", "offers": {"price": "25.00"}}]
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert "Hat" in result

    def test_out_of_stock(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Sold Out Shirt", "offers": {"price": "30.00", "availability": "https://schema.org/OutOfStock"}}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert "Out of Stock" in result

    def test_no_product_returns_none(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "name": "Acme Corp"}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        assert Scraper._extract_json_ld(soup) is None

    def test_no_json_ld_returns_none(self, scraper):
        html = "<html><head></head><body><p>Hello</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert Scraper._extract_json_ld(soup) is None

    def test_malformed_json_skipped(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">{broken json}</script>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Good Product", "offers": {"price": "10.00"}}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert result is not None
        assert "Good Product" in result

    def test_product_type_as_list(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": ["Product", "IndividualProduct"], "name": "Multi-Type", "offers": {"price": "50.00"}}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert "Multi-Type" in result

    def test_offers_as_list(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Multi-Offer", "offers": [{"price": "19.99", "priceCurrency": "USD"}]}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert "19.99" in result

    def test_low_price_fallback(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Range Product", "offers": {"lowPrice": "15.00", "priceCurrency": "USD"}}
        </script></head><body></body></html>"""
        soup = BeautifulSoup(html, "lxml")
        result = Scraper._extract_json_ld(soup)
        assert "15.00" in result


# ---------- clean_html ----------


class TestCleanHtml:
    def test_basic_cleaning(self, scraper):
        html = """<html><body>
        <nav>Nav stuff</nav>
        <main><p>Product: Cool Shirt - $29.99</p></main>
        <footer>Footer stuff</footer>
        </body></html>"""
        cleaned = scraper.clean_html(html)
        assert "Cool Shirt" in cleaned
        assert "29.99" in cleaned
        assert "Nav stuff" not in cleaned
        assert "Footer stuff" not in cleaned

    def test_removes_scripts_and_styles(self, scraper):
        html = """<html><head><style>.x{color:red}</style></head><body>
        <script>alert('xss')</script>
        <p>Real content</p>
        </body></html>"""
        cleaned = scraper.clean_html(html)
        assert "Real content" in cleaned
        assert "alert" not in cleaned
        assert "color:red" not in cleaned

    def test_json_ld_prepended(self, scraper):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type": "Product", "name": "Test Shoe", "offers": {"price": "89.00", "priceCurrency": "USD"}}
        </script></head><body><main><p>Page text here</p></main></body></html>"""
        cleaned = scraper.clean_html(html)
        assert cleaned.startswith("[JSON-LD Product Data]")
        assert "Test Shoe" in cleaned
        assert "89.00" in cleaned
        assert "Page text here" in cleaned

    def test_truncation(self, scraper):
        html = "<html><body><p>" + "x" * 20000 + "</p></body></html>"
        cleaned = scraper.clean_html(html)
        assert len(cleaned) <= MAX_CLEANED_LENGTH

    def test_content_selectors(self, scraper):
        html = """<html><body>
        <div>Noise</div>
        <div class="product-detail"><p>The real product info $49.99</p></div>
        <div>More noise</div>
        </body></html>"""
        cleaned = scraper.clean_html(html)
        assert "real product info" in cleaned

    def test_collapses_blank_lines(self, scraper):
        html = "<html><body><p>A</p><br><br><br><br><p>B</p></body></html>"
        cleaned = scraper.clean_html(html)
        assert "\n\n\n" not in cleaned

    def test_falls_back_to_body(self, scraper):
        html = "<html><body><p>Just body content</p></body></html>"
        cleaned = scraper.clean_html(html)
        assert "Just body content" in cleaned


# ---------- extract_footer_links ----------


class TestExtractFooterLinks:
    def test_extracts_footer_links(self, scraper):
        html = """<html><body>
        <main><a href="/products">Products</a></main>
        <footer>
            <a href="/returns">Return Policy</a>
            <a href="/privacy">Privacy</a>
        </footer>
        </body></html>"""
        links = scraper.extract_footer_links(html, "https://example.com")
        texts = [l["text"] for l in links]
        assert "return policy" in texts
        assert "privacy" in texts

    def test_resolves_relative_urls(self, scraper):
        html = """<html><body><footer>
        <a href="/help/returns">Returns</a>
        </footer></body></html>"""
        links = scraper.extract_footer_links(html, "https://shop.example.com")
        assert links[0]["href"] == "https://shop.example.com/help/returns"

    def test_skips_javascript_and_mailto(self, scraper):
        html = """<html><body><footer>
        <a href="javascript:void(0)">JS Link</a>
        <a href="mailto:help@example.com">Email</a>
        <a href="/real">Real Link</a>
        </footer></body></html>"""
        links = scraper.extract_footer_links(html, "https://example.com")
        assert len(links) == 1
        assert links[0]["text"] == "real link"

    def test_no_footer_uses_bottom_half(self, scraper):
        html = """<html><body>
        <a href="/1">First</a>
        <a href="/2">Second</a>
        <a href="/3">Third</a>
        <a href="/4">Fourth</a>
        </body></html>"""
        links = scraper.extract_footer_links(html, "https://example.com")
        # Should get bottom half (links 3 and 4)
        assert len(links) == 2


# ---------- Content-type validation (via fetch mocking) ----------


class TestFetch:
    def test_rejects_non_html_content_type(self, scraper, mocker):
        """fetch() should return None for non-HTML responses."""
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "video/mp4"}
        mock_response.text = "binary garbage"
        mocker.patch.object(scraper.session, "get", return_value=mock_response)

        result = scraper.fetch("https://example.com/video.mp4")
        assert result is None

    def test_accepts_html_content_type(self, scraper, mocker):
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.text = "<html><body>Hello</body></html>"
        mocker.patch.object(scraper.session, "get", return_value=mock_response)

        result = scraper.fetch("https://example.com/page")
        assert result == "<html><body>Hello</body></html>"

    def test_accepts_xhtml_content_type(self, scraper, mocker):
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "application/xhtml+xml"}
        mock_response.text = "<html><body>XHTML</body></html>"
        mocker.patch.object(scraper.session, "get", return_value=mock_response)

        result = scraper.fetch("https://example.com/page")
        assert result == "<html><body>XHTML</body></html>"

    def test_captcha_detection_triggers_browser_fallback(self, scraper, mocker):
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        mock_response = mocker.Mock()
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html><body>Please complete the captcha to continue</body></html>"
        mocker.patch.object(scraper.session, "get", return_value=mock_response)

        mock_browser = mocker.patch.object(scraper, "_fetch_with_browser", return_value=None)
        result = scraper.fetch("https://example.com/protected")
        mock_browser.assert_called_once()

    def test_403_triggers_browser_fallback(self, scraper, mocker):
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        mock_response = mocker.Mock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mocker.patch.object(scraper.session, "get", return_value=mock_response)

        mock_browser = mocker.patch.object(scraper, "_fetch_with_browser", return_value="<html>browser content</html>")
        result = scraper.fetch("https://example.com/blocked")
        assert result == "<html>browser content</html>"
        mock_browser.assert_called_once()

    def test_retry_on_429(self, scraper, mocker):
        mocker.patch.object(scraper, "_rate_limit")
        mocker.patch("time.sleep")

        resp_429 = mocker.Mock()
        resp_429.status_code = 429
        resp_429.headers = {}
        mocker.patch.object(scraper.session, "get", return_value=resp_429)
        mocker.patch.object(scraper, "_fetch_with_browser", return_value=None)

        result = scraper.fetch("https://example.com/limited")
        # With max_retries=1, should attempt once, then fallback
        assert result is None
