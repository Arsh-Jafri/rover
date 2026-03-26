import logging

import anthropic

from rover.db import Database
from rover.policies import PolicyLookup
from rover.scraper import Scraper

logger = logging.getLogger(__name__)

PRICE_EXTRACTION_PROMPT = """You are extracting the current selling price from a product page.

Product name (for context): {item_name}

Page content:
{content}

Use the extract_price tool to report the current price. If you cannot determine
the price, set current_price to null. Look for the actual selling price
(not the "was" or "list" price). If there is a sale or discounted price, use that.
"""


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

    def check_all_prices(self) -> list[dict]:
        """Check prices for all purchases that have URLs and are still
        within their retailer's refund window.

        Returns:
            A list of price check result dicts for purchases where a
            price drop was detected.
        """
        purchases = self.db.get_purchases_with_url()
        drops: list[dict] = []

        for purchase in purchases:
            domain = self.policy_lookup.extract_domain(url=purchase.get("product_url"))
            if not domain:
                logger.debug(
                    "Skipping purchase %d: could not extract domain", purchase["id"]
                )
                continue

            purchase_date = purchase.get("purchase_date")
            if not purchase_date:
                logger.debug(
                    "Skipping purchase %d: no purchase date", purchase["id"]
                )
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
        """Use Claude with tool_use to extract the current price from page text.

        Args:
            cleaned_html: Cleaned text content from the product page.
            item_name: Name of the product for context.

        Returns:
            The current price as a float, or None if extraction fails.
        """
        tools = [
            {
                "name": "extract_price",
                "description": "Extract the current selling price from a product page.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "current_price": {
                            "type": ["number", "null"],
                            "description": "The current selling price in the page's currency, or null if not found.",
                        },
                        "currency": {
                            "type": "string",
                            "description": "The currency code (e.g. USD, EUR, GBP).",
                        },
                        "in_stock": {
                            "type": "boolean",
                            "description": "Whether the product appears to be in stock.",
                        },
                    },
                    "required": ["current_price", "currency", "in_stock"],
                },
            }
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                tools=tools,
                tool_choice={"type": "tool", "name": "extract_price"},
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

            for block in response.content:
                if block.type == "tool_use" and block.name == "extract_price":
                    price = block.input.get("current_price")
                    if price is not None:
                        return float(price)
                    return None

        except anthropic.APIError as exc:
            logger.error("LLM API error extracting price for '%s': %s", item_name, exc)

        return None
