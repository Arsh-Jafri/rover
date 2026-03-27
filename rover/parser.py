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
            "order_number": {
                "type": ["string", "null"],
                "description": "Order number or order ID from the email, if present",
            },
            "email_type": {
                "type": "string",
                "enum": ["receipt", "shipping", "refund", "subscription", "other"],
                "description": (
                    "Type of email: 'receipt' for one-time purchase receipts/order confirmations, "
                    "'shipping' for shipping/delivery notifications, "
                    "'refund' for refund/return confirmations, "
                    "'subscription' for recurring subscription/membership/renewal charges, "
                    "'other' for anything else"
                ),
            },
        },
        "required": ["item_name", "price_paid", "retailer", "purchase_date", "currency", "email_type"],
    },
}

_MAX_INPUT_CHARS = 8000

# Matches common price patterns: $19.99, $ 19.99, USD 19.99, 19.99 USD, etc.
_PRICE_PATTERN = re.compile(
    r"(?:\$\s?\d+(?:[.,]\d{2})?)"           # $19.99 or $19
    r"|(?:\d+(?:[.,]\d{2})?\s?(?:USD|EUR|GBP|CAD))"  # 19.99 USD
    r"|(?:(?:USD|EUR|GBP|CAD)\s?\d+(?:[.,]\d{2})?)"  # USD 19.99
    r"|(?:(?:total|amount|paid|price|subtotal|order total)\s*:?\s*\$?\s?\d+(?:[.,]\d{2})?)"  # Total: 19.99
)


_FILLER_RE = re.compile(r"[\s\u00ad\u034f\u200b-\u200f\u2028\u2029\ufeff\u00a0]+")


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
    # Strip invisible filler characters that waste token budget
    text = _FILLER_RE.sub(" ", text).strip()
    # Remove long tracking URLs (>200 chars) that eat up the token budget
    text = re.sub(r"\(https?://[^\)]{200,}\)", "", text)
    text = re.sub(r"https?://\S{200,}", "", text)
    return text


# Senders whose receipts aren't worth tracking (food delivery, ride-share, etc.)
_IGNORED_SENDERS = {
    "doordash", "instacart", "ubereats", "uber eats", "grubhub",
    "seamless", "postmates", "caviar", "gopuff",
}

# Matches order-related keywords that suggest a transactional email
_ORDER_PATTERN = re.compile(
    r"order|receipt|e-receipt|confirmation|invoice|purchase|transaction",
    re.IGNORECASE,
)

# Subject-only patterns for shipping/delivery emails we want to skip
_SHIPPING_PATTERN = re.compile(
    r"shipped|on its way|on the way|in transit|out for delivery"
    r"|(?:has been |been )delivered|delivered!|shipment update",
    re.IGNORECASE,
)

# Subject-only patterns for refund/return emails we want to skip
_REFUND_PATTERN = re.compile(
    r"refund|refunded|your return|return confirmed|return processed|return details|credit issued|money back",
    re.IGNORECASE,
)

# If these appear in the subject, override shipping/refund rejection
_RECEIPT_OVERRIDE_PATTERN = re.compile(
    r"(?<!return )receipt|e-receipt",
    re.IGNORECASE,
)

# Subject-only patterns for subscription/renewal emails we want to skip
_SUBSCRIPTION_PATTERN = re.compile(
    r"renewal|renew your|subscription",
    re.IGNORECASE,
)


class ReceiptParser:
    """LLM-based receipt parser using Claude API with tool_use for structured output."""

    def __init__(self, config: dict):
        self.client = anthropic.Anthropic()
        self.model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")

    @staticmethod
    def is_likely_receipt(subject: str, body_text: str, body_html: str = "", sender: str = "") -> bool:
        """Check if an email looks like a purchase receipt using regex only.

        Returns True when the email contains both a price pattern AND an
        order-related keyword — broad enough to catch real receipts while
        filtering out non-transactional spam.
        """
        sender_lower = sender.lower()
        if any(name in sender_lower for name in _IGNORED_SENDERS):
            return False

        # Subject-level rejection for shipping/refund/subscription emails
        if _SUBSCRIPTION_PATTERN.search(subject):
            return False
        if (_SHIPPING_PATTERN.search(subject) or _REFUND_PATTERN.search(subject)):
            if not _RECEIPT_OVERRIDE_PATTERN.search(subject):
                return False

        text = subject
        if body_text:
            text += "\n" + body_text[:3000]
        elif body_html:
            stripped = BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True)
            text += "\n" + _FILLER_RE.sub(" ", stripped)[:5000]
        return bool(_PRICE_PATTERN.search(text) and _ORDER_PATTERN.search(text))

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
            "Analyze this email and extract purchase details using the extract_receipt tool.\n\n"
            "First, determine the email_type:\n"
            "- 'receipt' for one-time purchase receipts or order confirmations with item prices\n"
            "- 'shipping' for shipping/delivery/tracking notifications\n"
            "- 'refund' for refund or return confirmations\n"
            "- 'subscription' for recurring subscription, membership, or renewal charges\n"
            "- 'other' for anything else\n\n"
            "Extract all fields including order_number if present.\n\n"
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
                    email_type = receipt.get("email_type", "other")
                    if email_type != "receipt":
                        logger.info(
                            "Skipping non-receipt email (type=%s): %s", email_type, subject
                        )
                        return None
                    try:
                        receipt["price_paid"] = float(receipt["price_paid"])
                    except (ValueError, TypeError, KeyError):
                        logger.warning("Invalid price_paid in receipt: %s", receipt.get("price_paid"))
                        return None
                    if receipt["price_paid"] <= 0:
                        logger.info("Skipping non-positive price (likely refund): $%.2f for %s", receipt["price_paid"], subject)
                        return None
                    logger.info(
                        "Parsed receipt: %s from %s — $%.2f (order: %s)",
                        receipt.get("item_name"),
                        receipt.get("retailer"),
                        receipt["price_paid"],
                        receipt.get("order_number", "N/A"),
                    )
                    return receipt

            logger.warning("No tool_use block in response for: %s", subject)
            return None

        except Exception:
            logger.exception("Failed to parse receipt from email: %s", subject)
            return None
