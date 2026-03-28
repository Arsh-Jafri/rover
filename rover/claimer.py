"""Automated price adjustment claim emails to retailers."""

from collections import defaultdict
from datetime import datetime, timedelta

from rover.db import Database
from rover.gmail import GmailClient
from rover.logger import get_logger
from rover.policies import PolicyLookup

logger = get_logger("claimer")


class ClaimManager:
    """Sends price adjustment claim emails to retailers for detected price drops."""

    def __init__(
        self,
        config: dict,
        db: Database,
        gmail_client: GmailClient,
        policy_lookup: PolicyLookup,
    ):
        claims_config = config.get("claims", {})
        self.enabled = claims_config.get("enabled", False)
        self.db = db
        self.gmail = gmail_client
        self.policy_lookup = policy_lookup

    def send_claims(self, user_id: str) -> dict:
        """Send claim emails to retailers for all notified savings.

        Groups items by retailer, discovers support emails, and sends
        one claim per retailer. Skips retailers without a support email.

        Returns:
            Summary dict with sent, skipped, and failed counts.
        """
        result = {"sent": 0, "skipped": 0, "failed": 0}

        if not self.enabled:
            logger.debug("Claims disabled — skipping")
            return result

        user = self.db.get_user(user_id)
        if not user:
            logger.warning("User %s not found — skipping claims", user_id)
            return result

        customer_name = user.get("name", "")
        if not customer_name:
            logger.warning("No name set for user %s — skipping claims", user_id)
            return result

        savings = self.db.get_notified_savings(user_id)
        if not savings:
            logger.debug("No notified savings to claim")
            return result

        enriched = self._enrich_savings(savings)
        if not enriched:
            return result

        grouped = self._group_by_retailer(enriched)

        for domain, items in grouped.items():
            support_email = self.policy_lookup.discover_support_email(domain)
            retailer_name = items[0].get("retailer", domain)

            if not support_email:
                logger.info(
                    "No support email for %s — skipping %d item(s)",
                    domain, len(items),
                )
                result["skipped"] += len(items)
                continue

            subject = self._build_subject(items)
            html = self._build_claim_html(customer_name, items, retailer_name)

            try:
                self.gmail.send_email(support_email, subject, html)
            except Exception:
                logger.exception("Failed to send claim to %s for %s", support_email, domain)
                result["failed"] += len(items)
                continue

            for item in items:
                saving_id = item.get("saving_id")
                if saving_id:
                    try:
                        self.db.update_saving_status(saving_id, "claimed")
                    except Exception:
                        logger.exception("Failed to update saving %s status", saving_id)

            result["sent"] += len(items)
            logger.info(
                "Claim sent to %s (%s) for %d item(s)",
                support_email, retailer_name, len(items),
            )

        logger.info("Claims complete: %s", result)
        return result

    def send_test_claim(self, user_id: str) -> bool:
        """Send a test claim email to the user's own email for verification."""
        user = self.db.get_user(user_id)
        if not user:
            logger.warning("User %s not found — can't send test claim", user_id)
            return False

        recipient = user["email"]
        customer_name = user.get("name", "Test Customer")

        fake_items = [{
            "item_name": "Test Product - Classic Tee (Black, M)",
            "retailer": "Example Store",
            "order_number": "TEST-12345",
            "purchase_date": "2026-03-15",
            "price_paid": 49.99,
            "current_price": 34.99,
            "savings_amount": 15.00,
            "product_url": "https://www.example.com/classic-tee",
        }]

        subject = "Rover Test: Price Adjustment Claim Email"
        html = self._build_claim_html(customer_name, fake_items, "Example Store")

        try:
            self.gmail.send_email(recipient, subject, html)
            logger.info("Test claim sent to %s", recipient)
            return True
        except Exception:
            logger.exception("Failed to send test claim")
            return False

    def _enrich_savings(self, savings: list[dict]) -> list[dict]:
        """Add purchase and retailer details to each savings row."""
        enriched = []
        for saving in savings:
            purchase = self.db.get_purchase(saving["purchase_id"])
            if not purchase:
                continue

            domain = self.policy_lookup.extract_domain(url=purchase.get("product_url"))

            enriched.append({
                "item_name": purchase.get("item_name", "Unknown"),
                "retailer": purchase.get("retailer", "Unknown"),
                "order_number": purchase.get("order_number", ""),
                "purchase_date": purchase.get("purchase_date", ""),
                "price_paid": saving["original_price"],
                "current_price": saving["dropped_price"],
                "savings_amount": saving["savings_amount"],
                "product_url": purchase.get("product_url", ""),
                "domain": domain or "",
                "saving_id": saving["id"],
            })

        return enriched

    @staticmethod
    def _group_by_retailer(enriched: list[dict]) -> dict[str, list[dict]]:
        """Group enriched savings by retailer domain."""
        grouped = defaultdict(list)
        for item in enriched:
            domain = item.get("domain", "unknown")
            grouped[domain].append(item)
        return dict(grouped)

    @staticmethod
    def _build_subject(items: list[dict]) -> str:
        """Build email subject line from claim items."""
        order_numbers = list({
            item["order_number"] for item in items
            if item.get("order_number")
        })
        if len(order_numbers) == 1:
            return f"Price Adjustment Request — Order #{order_numbers[0]}"
        elif order_numbers:
            joined = ", #".join(order_numbers[:3])
            return f"Price Adjustment Request — Orders #{joined}"
        return "Price Adjustment Request"

    @staticmethod
    def build_claim_message(customer_name: str, items: list[dict], retailer_name: str) -> str:
        """Build a plain text claim message for copy-paste use.

        This is reused by the notification email for retailers without
        a support email (user copy-pastes into web form).
        """
        lines = [
            f"Dear {retailer_name} Customer Service,",
            "",
            f"My name is {customer_name}. I recently purchased the following item(s) "
            "from your store, and I noticed the price has since dropped. I would like "
            "to request a price adjustment for the difference.",
            "",
        ]

        total = 0.0
        for item in items:
            order_str = f"Order #{item['order_number']}, " if item.get("order_number") else ""
            lines.append(
                f"- {item['item_name']} ({order_str}purchased {item.get('purchase_date', 'recently')}): "
                f"paid ${item['price_paid']:.2f}, now ${item['current_price']:.2f}"
            )
            if item.get("product_url"):
                lines.append(f"  Current price: {item['product_url']}")
            total += item["savings_amount"]

        lines.extend([
            "",
            f"Total adjustment requested: ${total:.2f}",
            "",
            "I would appreciate it if you could process this adjustment to my original "
            "payment method. Please let me know if you need any additional information.",
            "",
            "Thank you,",
            customer_name,
        ])

        return "\n".join(lines)

    @staticmethod
    def _build_claim_html(customer_name: str, items: list[dict], retailer_name: str) -> str:
        """Build HTML claim email for sending to retailer support."""
        message = ClaimManager.build_claim_message(customer_name, items, retailer_name)

        # Convert plain text to HTML paragraphs
        html_body = ""
        for line in message.split("\n"):
            if line.startswith("- "):
                html_body += f"<li style=\"margin-bottom:4px\">{line[2:]}</li>\n"
            elif line.strip() == "" and html_body.endswith("</li>\n"):
                html_body = html_body.rstrip("\n") + "</ul>\n"
            elif line.startswith("  Current price:"):
                url = line.replace("  Current price: ", "")
                html_body += f"<div style=\"margin-left:20px;margin-bottom:8px;font-size:13px\">"
                html_body += f"Current price: <a href=\"{url}\" style=\"color:#1a73e8\">{url}</a></div>\n"
            else:
                if html_body and not html_body.endswith("</li>\n") and not html_body.endswith("</ul>\n") and line.startswith("- "):
                    html_body += "<ul style=\"margin:8px 0;padding-left:20px\">\n"
                html_body += f"<p style=\"margin:0 0 8px 0\">{line}</p>\n" if line.strip() else ""

        # Wrap bullet items in <ul> if not already closed
        if "</li>\n" in html_body and "</ul>" not in html_body.split("</li>\n")[-1]:
            html_body += "</ul>\n"

        # Simpler approach: just use the plain text in a clean HTML wrapper
        escaped = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Re-link URLs
        import re
        escaped = re.sub(
            r"(https?://\S+)",
            r'<a href="\1" style="color:#1a73e8">\1</a>',
            escaped,
        )
        paragraphs = escaped.split("\n\n")
        body_html = "".join(f"<p style=\"margin:0 0 14px 0;line-height:1.5\">{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#333;line-height:1.6">
{body_html}
</body>
</html>"""
