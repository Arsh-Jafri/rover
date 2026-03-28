import logging
import re
import time
from urllib.parse import parse_qs, quote_plus, urlparse

import anthropic
import requests
from bs4 import BeautifulSoup

from rover.db import Database
from rover.policies import PolicyLookup
from rover.scraper import Scraper

logger = logging.getLogger(__name__)

_ddg_session: requests.Session | None = None


def _get_ddg_session() -> requests.Session:
    """Get or create a DuckDuckGo session with cookies from the homepage."""
    global _ddg_session
    if _ddg_session is None:
        _ddg_session = requests.Session()
        _ddg_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        _ddg_session.get("https://duckduckgo.com/", timeout=10)
        time.sleep(1)
    return _ddg_session

PRICE_EXTRACTION_PROMPT = """What is the current selling price for this product?

{item_name}

Rules:
- Return the actual selling price (not the "was" or "list" price).
- If there's a sale or clearance price, use that.
- If the page shows multiple colors/sizes at DIFFERENT prices, return the price for
  the specific color/size above. If they're all the same price, return that price.
- If the product is sold out or the price can't be found, respond with "null".

Respond with ONLY the number (e.g. 14.95) or "null". No other text.

Page content:
{content}
"""

_PRICE_RE = re.compile(r"\d+\.?\d*")


class PriceChecker:
    """Checks current prices for tracked purchases by scraping product pages
    and using an LLM to extract pricing information."""

    def __init__(
        self,
        config: dict,
        db: Database,
        scraper: Scraper,
        policy_lookup: PolicyLookup,
    ):
        self.client = anthropic.Anthropic()
        self.model = config["anthropic"]["model"]
        self.db = db
        self.scraper = scraper
        self.policy_lookup = policy_lookup

    def discover_product_urls(self) -> int:
        """Find product URLs for purchases that don't have one yet.

        Returns:
            Number of URLs successfully found.
        """
        purchases = self.db.get_purchases_needing_url()
        found = 0

        for purchase in purchases:
            item_name = purchase.get("item_name", "")
            retailer = purchase.get("retailer", "")
            purchase_id = purchase["id"]

            # Skip items that can't be price-tracked
            if not item_name or not retailer:
                self.db.mark_url_search_attempted(purchase_id)
                continue

            url, rate_limited = self._find_product_url(item_name, retailer)
            # Rate limit between searches
            time.sleep(3)

            if url:
                self.db.update_purchase_url(purchase_id, url)
                self.db.mark_url_search_attempted(purchase_id)
                found += 1
                logger.info("Found URL for '%s': %s", item_name, url)
            elif rate_limited:
                # Don't mark as attempted — retry next run
                logger.warning("Rate limited searching for '%s' — will retry later", item_name)
            else:
                self.db.mark_url_search_attempted(purchase_id)
                logger.info("No URL found for '%s' from %s", item_name, retailer)

        if purchases:
            logger.info("URL discovery: found %d/%d", found, len(purchases))
        return found

    def _find_product_url(self, item_name: str, retailer: str) -> tuple[str | None, bool]:
        """Search DuckDuckGo for the product and pick the best matching URL.

        Returns:
            (url, rate_limited) — url is the product URL or None,
            rate_limited is True if search was blocked by CAPTCHA.
        """
        query = f"{retailer} {item_name}"
        ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        session = _get_ddg_session()

        try:
            resp = session.get(ddg_url, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("DuckDuckGo search failed for '%s': %s", query, exc)
            return None, False

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check for CAPTCHA / rate limit — reset session and signal retry
        if "bots use DuckDuckGo" in soup.get_text():
            logger.warning("DuckDuckGo rate limited — resetting session, waiting 10s")
            global _ddg_session
            _ddg_session = None
            time.sleep(10)
            return None, True

        results = []
        for link in soup.select(".result__a"):
            raw_href = link.get("href", "")
            # DuckDuckGo sometimes wraps URLs in a redirect, sometimes not
            parsed = urlparse(raw_href)
            qs = parse_qs(parsed.query)
            actual_url = qs.get("uddg", [None])[0] or raw_href
            if not actual_url or not actual_url.startswith("http"):
                continue
            # Skip ad/tracking URLs
            if "duckduckgo.com" in actual_url:
                continue
            title = link.get_text(strip=True)
            results.append({"title": title, "url": actual_url})

        if not results:
            return None, False

        # Prefer results from the retailer's own domain
        retailer_lower = retailer.lower().split("&")[0].strip()  # "Abercrombie & Fitch" -> "abercrombie"
        retailer_results = [
            r for r in results
            if any(part in urlparse(r["url"]).netloc.lower()
                   for part in retailer_lower.split() if len(part) > 3)
        ]
        # Use retailer-only results if we have any, otherwise fall back to all
        candidates = retailer_results if retailer_results else results

        # Build a compact list for Claude to pick from
        listing = "\n".join(
            f"{i+1}. {r['title']}\n   {r['url']}" for i, r in enumerate(candidates[:10])
        )

        prompt = (
            f"Which search result is the product page for '{item_name}' from '{retailer}'?\n"
            f"ONLY pick URLs from {retailer}'s own website. Do not pick third-party reseller sites.\n"
            f"Return the direct product page URL (not a search, category, or ad page).\n"
            f"If none match, respond with \"null\".\n"
            f"Respond with ONLY the URL or \"null\". No other text.\n\n{listing}"
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            if text.startswith("http"):
                return text, False
        except anthropic.APIError as exc:
            logger.error("LLM API error finding URL for '%s': %s", item_name, exc)

        return None, False

    def check_all_prices(self) -> list[dict]:
        """Check prices for all purchases that have URLs and are still
        within their retailer's refund window.

        Returns:
            A list of price check result dicts for purchases where a
            price drop was detected.
        """
        # First, try to find URLs for purchases that don't have one
        self.discover_product_urls()

        purchases = self.db.get_purchases_with_url()
        drops: list[dict] = []

        for purchase in purchases:
            domain = self.policy_lookup.extract_domain(url=purchase.get("product_url"))
            if not domain:
                continue

            purchase_date = purchase.get("purchase_date")
            if not purchase_date:
                continue

            if not self.policy_lookup.is_within_refund_window(purchase_date, domain):
                logger.debug(
                    "Skipping purchase %d: outside refund window", purchase["id"]
                )
                continue

            result = self.check_price(purchase)
            if result and result.get("price_dropped"):
                drops.append(result)

        logger.info(
            "Price check complete: %d/%d purchases had drops",
            len(drops),
            len(purchases),
        )
        return drops

    def check_price(self, purchase: dict) -> dict | None:
        """Check the current price for a single purchase.

        Fetches the product page, extracts the price via LLM, records
        the result, and creates a savings record if a drop is detected.

        Args:
            purchase: A purchase dict from the database.

        Returns:
            A result dict with price check details, or None on failure.
        """
        url = purchase.get("product_url")
        purchase_id = purchase["id"]
        item_name = purchase.get("item_name", "Unknown item")
        price_paid = purchase.get("price_paid")

        if not url:
            return None

        html = self.scraper.fetch(url)
        if not html:
            check_id = self.db.add_price_check(
                purchase_id=purchase_id,
                current_price=None,
                status="scrape_failed",
                error_detail="Failed to fetch product page",
            )
            logger.warning("Scrape failed for purchase %d: %s", purchase_id, url)
            return {
                "purchase_id": purchase_id,
                "price_check_id": check_id,
                "status": "scrape_failed",
                "price_dropped": False,
            }

        cleaned = self.scraper.clean_html(html, url=url)

        # Skip very short content — likely sold out, blocked, or broken page
        if len(cleaned.strip()) < 100:
            detail = "Page content too short" if cleaned.strip() else "No content extracted"
            if "out of stock" in cleaned.lower() or "sold out" in cleaned.lower():
                detail = "Product is sold out"
            check_id = self.db.add_price_check(
                purchase_id=purchase_id,
                current_price=None,
                status="scrape_failed",
                error_detail=detail,
            )
            logger.warning("Insufficient content for purchase %d: '%s'", purchase_id, cleaned.strip()[:100])
            return {
                "purchase_id": purchase_id,
                "price_check_id": check_id,
                "status": "scrape_failed",
                "price_dropped": False,
            }

        current_price = self._extract_price(cleaned, item_name)

        if current_price is None:
            check_id = self.db.add_price_check(
                purchase_id=purchase_id,
                current_price=None,
                status="parse_failed",
                error_detail="Could not extract price from page",
            )
            logger.warning("Price extraction failed for purchase %d", purchase_id)
            return {
                "purchase_id": purchase_id,
                "price_check_id": check_id,
                "status": "parse_failed",
                "price_dropped": False,
            }

        check_id = self.db.add_price_check(
            purchase_id=purchase_id,
            current_price=current_price,
            status="success",
        )

        result = {
            "purchase_id": purchase_id,
            "price_check_id": check_id,
            "item_name": item_name,
            "price_paid": price_paid,
            "current_price": current_price,
            "status": "success",
            "price_dropped": False,
        }

        if price_paid is not None and current_price < price_paid:
            savings_amount = round(price_paid - current_price, 2)
            saving_id = self.db.add_saving(
                purchase_id=purchase_id,
                price_check_id=check_id,
                original_price=price_paid,
                dropped_price=current_price,
                savings_amount=savings_amount,
            )
            result["price_dropped"] = True
            result["savings_amount"] = savings_amount
            result["saving_id"] = saving_id
            logger.info(
                "Price drop detected for purchase %d (%s): $%.2f -> $%.2f (save $%.2f)",
                purchase_id,
                item_name,
                price_paid,
                current_price,
                savings_amount,
            )

        return result

    def _extract_price(self, cleaned_html: str, item_name: str) -> float | None:
        """Use Claude to extract the current price from page text.

        Args:
            cleaned_html: Cleaned text content from the product page.
            item_name: Name of the product for context.

        Returns:
            The current price as a float, or None if extraction fails.
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=32,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": PRICE_EXTRACTION_PROMPT.format(
                            item_name=item_name,
                            content=cleaned_html,
                        ),
                    }
                ],
            )

            text = response.content[0].text.strip()
            logger.debug("LLM price response for '%s': '%s'", item_name, text)
            # Try to extract a number first — only treat as null if no number found
            match = _PRICE_RE.search(text)
            if match:
                return float(match.group())
            return None

        except anthropic.APIError as exc:
            logger.error("LLM API error extracting price for '%s': %s", item_name, exc)

        return None
