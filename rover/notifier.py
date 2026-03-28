"""Email notification manager for Rover price drop alerts."""

from datetime import datetime, timedelta

from rover.db import Database
from rover.gmail import GmailClient
from rover.logger import get_logger
from rover.policies import PolicyLookup

logger = get_logger("notifier")


class NotificationManager:
    """Sends consolidated email notifications when price drops are detected."""

    def __init__(
        self,
        config: dict,
        db: Database,
        gmail_client: GmailClient,
        policy_lookup: PolicyLookup,
    ):
        notif_config = config.get("notifications", {})
        self.enabled = notif_config.get("enabled", False)
        self.recipient = notif_config.get("recipient_email")
        self.db = db
        self.gmail = gmail_client
        self.policy_lookup = policy_lookup

    def notify_drops(self, drops: list[dict]) -> bool:
        """Send a single consolidated email for all price drops.

        Args:
            drops: List of drop dicts from PriceChecker.check_all_prices().

        Returns:
            True if notification sent successfully, False otherwise.
        """
        if not self.enabled:
            logger.debug("Notifications disabled — skipping")
            return False

        if not self.recipient:
            logger.warning("No recipient_email configured — skipping notification")
            return False

        if not drops:
            return False

        enriched = self._enrich_drops(drops)
        if not enriched:
            return False

        total_savings = sum(d["savings_amount"] for d in enriched)
        count = len(enriched)
        subject = f"Rover: ${total_savings:.2f} in price drops detected ({count} item{'s' if count != 1 else ''})"
        html = self._build_html(enriched, total_savings)

        try:
            self.gmail.send_email(self.recipient, subject, html)
        except Exception:
            logger.exception("Failed to send notification email")
            return False

        # Mark all as notified only after successful send
        for drop in enriched:
            saving_id = drop.get("saving_id")
            if saving_id:
                try:
                    self.db.update_saving_status(saving_id, "notified")
                except Exception:
                    logger.exception("Failed to update saving %s status", saving_id)

        logger.info(
            "Notification sent: %d drops, $%.2f total savings",
            count, total_savings,
        )
        return True

    def send_test_notification(self) -> bool:
        """Send a test notification with sample data for dev/testing."""
        if not self.recipient:
            logger.warning("No recipient_email configured — can't send test")
            return False

        fake_drops = [{
            "item_name": "Test Product - Classic Tee",
            "retailer": "Example Store",
            "price_paid": 49.99,
            "current_price": 34.99,
            "savings_amount": 15.00,
            "product_url": "https://www.example.com/classic-tee",
            "order_number": "TEST-001",
            "currency": "USD",
            "days_remaining": 7,
            "saving_id": None,
        }]

        subject = "Rover Test: Price Drop Notification"
        html = self._build_html(fake_drops, 15.00)

        try:
            self.gmail.send_email(self.recipient, subject, html)
            logger.info("Test notification sent to %s", self.recipient)
            return True
        except Exception:
            logger.exception("Failed to send test notification")
            return False

    def _enrich_drops(self, drops: list[dict]) -> list[dict]:
        """Add purchase and retailer details to each drop dict."""
        enriched = []
        for drop in drops:
            purchase = self.db.get_purchase(drop["purchase_id"])
            if not purchase:
                continue

            domain = self.policy_lookup.extract_domain(url=purchase.get("product_url"))
            retailer_info = self.policy_lookup.get_retailer_info(domain) if domain else None

            days_remaining = None
            if purchase.get("purchase_date") and retailer_info:
                try:
                    purchased = datetime.strptime(purchase["purchase_date"], "%Y-%m-%d")
                    deadline = purchased + timedelta(days=retailer_info.refund_window_days)
                    days_remaining = max(0, (deadline - datetime.now()).days)
                except ValueError:
                    pass

            enriched.append({
                "item_name": drop.get("item_name", purchase.get("item_name", "Unknown")),
                "retailer": purchase.get("retailer", "Unknown"),
                "price_paid": drop.get("price_paid", purchase.get("price_paid")),
                "current_price": drop["current_price"],
                "savings_amount": drop["savings_amount"],
                "product_url": purchase.get("product_url", ""),
                "order_number": purchase.get("order_number", ""),
                "currency": purchase.get("currency", "USD"),
                "days_remaining": days_remaining,
                "saving_id": drop.get("saving_id"),
            })

        return enriched

    @staticmethod
    def _build_html(drops: list[dict], total_savings: float) -> str:
        """Build HTML email body with a table of price drops."""
        count = len(drops)

        rows = ""
        for d in drops:
            item_name = d["item_name"]
            product_url = d.get("product_url", "")
            if product_url:
                item_cell = f'<a href="{product_url}" style="color:#1a73e8;text-decoration:none">{item_name}</a>'
            else:
                item_cell = item_name

            days = d.get("days_remaining")
            if days is None:
                days_cell = "—"
            elif days <= 3:
                days_cell = f'<span style="color:#d32f2f;font-weight:bold">{days}d</span>'
            elif days <= 7:
                days_cell = f'<span style="color:#f57c00;font-weight:bold">{days}d</span>'
            else:
                days_cell = f"{days}d"

            rows += f"""<tr>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0">{item_cell}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0">{d['retailer']}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0;text-align:right">${d['price_paid']:.2f}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0;text-align:right">${d['current_price']:.2f}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0;text-align:right;color:#2e7d32;font-weight:bold">${d['savings_amount']:.2f}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #e0e0e0;text-align:center">{days_cell}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<div style="max-width:640px;margin:20px auto;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">

    <div style="background:#1a73e8;padding:24px 28px">
        <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:600">Rover Price Alert</h1>
    </div>

    <div style="padding:24px 28px">
        <p style="margin:0 0 20px 0;font-size:16px;color:#333">
            <strong style="color:#2e7d32;font-size:20px">${total_savings:.2f}</strong>
            in potential savings across <strong>{count}</strong> item{'s' if count != 1 else ''}.
        </p>

        <table style="width:100%;border-collapse:collapse;font-size:14px">
            <thead>
                <tr style="background:#f8f9fa">
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e0e0e0;color:#666">Item</th>
                    <th style="padding:10px 12px;text-align:left;border-bottom:2px solid #e0e0e0;color:#666">Retailer</th>
                    <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e0e0e0;color:#666">Paid</th>
                    <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e0e0e0;color:#666">Now</th>
                    <th style="padding:10px 12px;text-align:right;border-bottom:2px solid #e0e0e0;color:#666">You Save</th>
                    <th style="padding:10px 12px;text-align:center;border-bottom:2px solid #e0e0e0;color:#666">Days Left</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>

        <p style="margin:20px 0 0 0;font-size:13px;color:#888">
            Visit the retailer to request a price adjustment or contact their support for a refund of the difference.
        </p>
    </div>

    <div style="background:#f8f9fa;padding:16px 28px;border-top:1px solid #e0e0e0">
        <p style="margin:0;font-size:12px;color:#999">
            Sent by Rover — Automated Price Adjustment Agent
        </p>
    </div>

</div>
</body>
</html>"""

        return html
