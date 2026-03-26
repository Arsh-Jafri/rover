import re

import anthropic
from bs4 import BeautifulSoup

from rover.logger import get_logger

logger = get_logger("parser")

_RECEIPT_TOOL = {
    "name": "extract_receipt",
    "description": (
        "Extract structured purchase receipt details from an email. "
        "Call this tool with the parsed fields from the email content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "item_name": {
                "type": "string",
                "description": "Name of the purchased product or service",
            },
            "price_paid": {
                "type": "number",
                "description": "Total price paid for the item including tax",
            },
            "product_url": {
                "type": ["string", "null"],
                "description": "Direct URL to the product page, if available",
            },
            "retailer": {
                "type": "string",
                "description": "Name of the retailer or seller",
            },
            "purchase_date": {
                "type": "string",
                "description": "Date of purchase in YYYY-MM-DD format",
            },
            "currency": {
                "type": "string",
                "description": "ISO 4217 currency code (e.g. USD, EUR, GBP)",
                "default": "USD",
            },
            "support_email": {
                "type": ["string", "null"],
                "description": "Customer support email found in the email footer",
            },
            "support_url": {
                "type": ["string", "null"],
                "description": "Customer support or help center URL found in the email",
            },
        },
        "required": ["item_name", "price_paid", "retailer", "purchase_date", "currency"],
    },
}

_MAX_INPUT_CHARS = 8000


def _clean_html_to_text(html: str) -> str:
    """Strip HTML tags and return readable text, preserving links."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    # Preserve href URLs inline so LLM can see product links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if text and href and href.startswith("http"):
            a.replace_with(f"{text} ({href})")
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class ReceiptParser:
    """LLM-based receipt parser using Claude API with tool_use for structured output."""

    def __init__(self, config: dict):
        self.client = anthropic.Anthropic()
        self.model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")

    def classify_email(self, subject: str, sender: str, body_text: str) -> bool:
        """Determine whether an email is a purchase receipt or order confirmation
        that contains a price/total amount.

        Returns:
            True if the email is a receipt with pricing information.
        """
        snippet = body_text[:1500] if body_text else ""
        prompt = (
            "You are classifying emails. Determine if this email is a purchase receipt "
            "or order confirmation that contains a PRICE or TOTAL AMOUNT paid.\n\n"
            "Reply YES only if the email includes a dollar amount for a purchase. "
            "Reply NO for shipping notifications, delivery updates, order status updates, "
            "or any email that does not contain a price.\n\n"
            f"Subject: {subject}\n"
            f"From: {sender}\n"
            f"Body (first 1500 chars):\n{snippet}\n\n"
            "Reply with exactly YES or NO. Nothing else."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = response.content[0].text.strip().upper()
            is_receipt = answer.startswith("YES")
            logger.debug(
                "Classified email '%s' from %s as receipt=%s", subject, sender, is_receipt
            )
            return is_receipt
        except Exception:
            logger.exception("Failed to classify email: %s", subject)
            return False

    def parse_receipt(
        self,
        subject: str,
        sender: str,
        body_text: str,
        body_html: str | None = None,
    ) -> dict | None:
        """Extract structured receipt data from an email using Claude tool_use.

        Cleans HTML to readable text before sending to the LLM.
        Input is truncated to ~8000 characters to control API costs.

        Returns:
            Dict with keys matching the extract_receipt tool schema, or None
            if extraction fails.
        """
        if body_html:
            body = _clean_html_to_text(body_html)
        else:
            body = body_text

        if not body:
            logger.warning("No email body to parse for: %s", subject)
            return None

        body = body[:_MAX_INPUT_CHARS]

        prompt = (
            "Extract the purchase details from this email receipt. "
            "Use the extract_receipt tool to return the structured data.\n\n"
            f"Subject: {subject}\n"
            f"From: {sender}\n"
            f"Email body:\n{body}"
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                tools=[_RECEIPT_TOOL],
                tool_choice={"type": "tool", "name": "extract_receipt"},
                messages=[{"role": "user", "content": prompt}],
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == "extract_receipt":
                    receipt = block.input
                    try:
                        receipt["price_paid"] = float(receipt["price_paid"])
                    except (ValueError, TypeError, KeyError):
                        logger.warning("Invalid price_paid in receipt: %s", receipt.get("price_paid"))
                        return None
                    logger.info(
                        "Parsed receipt: %s from %s — $%.2f",
                        receipt.get("item_name"),
                        receipt.get("retailer"),
                        receipt["price_paid"],
                    )
                    return receipt

            logger.warning("No tool_use block in response for: %s", subject)
            return None

        except Exception:
            logger.exception("Failed to parse receipt from email: %s", subject)
            return None
