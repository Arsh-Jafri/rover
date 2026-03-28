"""Email notification manager for Rover price drop alerts."""

from collections import defaultdict
from datetime import datetime, timedelta

from rover.db import Database
from rover.emailer import send as send_email
from rover.logger import get_logger
from rover.policies import PolicyLookup

logger = get_logger("notifier")


class NotificationManager:
    """Sends consolidated email notifications when price drops are detected."""

    def __init__(
        self,
        config: dict,
        db: Database,
        policy_lookup: PolicyLookup,
    ):
        notif_config = config.get("notifications", {})
        self.enabled = notif_config.get("enabled", False)
        self.sender_email = notif_config.get("sender_email", "rover@tryrover.app")
        self.sender_name = notif_config.get("sender_name", "Rover")
        self.db = db
        self.policy_lookup = policy_lookup
        # Claims config for generating claim drafts in notifications
        claims_config = config.get("claims", {})
        self.claims_enabled = claims_config.get("enabled", False)

    def notify_drops(self, user_id: str, drops: list[dict]) -> bool:
        """Send a single consolidated email for all price drops.

        Args:
            user_id: The user to notify.
            drops: List of drop dicts from PriceChecker.check_all_prices().

        Returns:
            True if notification sent successfully, False otherwise.
        """
        if not self.enabled:
            logger.debug("Notifications disabled — skipping")
            return False

        user = self.db.get_user(user_id)
        if not user:
            logger.warning("User %s not found — skipping notification", user_id)
            return False

        recipient = user["email"]
        customer_name = user.get("name", "")

        if not drops:
            return False

        enriched = self._enrich_drops(drops)
        if not enriched:
            return False

        total_savings = sum(d["savings_amount"] for d in enriched)
        count = len(enriched)
        subject = f"Rover: ${total_savings:.2f} in price drops detected ({count} item{'s' if count != 1 else ''})"
        html = self._build_html(enriched, total_savings, self.claims_enabled, customer_name)

        try:
            send_email(recipient, subject, html, self.sender_email, self.sender_name)
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

    def send_test_notification(self, user_id: str) -> bool:
        """Send a test notification with sample data for dev/testing."""
        user = self.db.get_user(user_id)
        if not user:
            logger.warning("User %s not found — can't send test", user_id)
            return False

        recipient = user["email"]

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
            send_email(recipient, subject, html, self.sender_email, self.sender_name)
            logger.info("Test notification sent to %s", recipient)
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
                "purchase_date": purchase.get("purchase_date", ""),
                "currency": purchase.get("currency", "USD"),
                "days_remaining": days_remaining,
                "saving_id": drop.get("saving_id"),
                "support_email": retailer_info.support_email if retailer_info else None,
                "support_url": retailer_info.support_url if retailer_info else None,
                "domain": domain,
            })

        return enriched

    @staticmethod
    def _days_badge(days: int | None) -> str:
        """Return an HTML badge for refund window days remaining."""
        if days is None:
            return ""
        font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"
        if days <= 3:
            return f'<span style="display:inline-block;background:#fef2f2;color:#D32F2F;font-family:{font};font-size:12px;font-weight:600;padding:2px 10px;border-radius:9999px">{days}d left</span>'
        if days <= 7:
            return f'<span style="display:inline-block;background:#fffbeb;color:#d97706;font-family:{font};font-size:12px;font-weight:600;padding:2px 10px;border-radius:9999px">{days}d left</span>'
        return f'<span style="color:rgba(26,29,30,0.5);font-family:{font};font-size:13px">{days}d left</span>'

    @staticmethod
    def _build_html(
        drops: list[dict],
        total_savings: float,
        claims_enabled: bool = False,
        customer_name: str = "",
    ) -> str:
        """Build branded HTML email body with price drops and claim actions."""
        count = len(drops)
        font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

        # Build item rows
        items_html = ""
        for i, d in enumerate(drops):
            border = "border-bottom:1px solid rgba(0,0,0,0.06);" if i < len(drops) - 1 else ""

            item_name = d["item_name"]
            product_url = d.get("product_url", "")
            if product_url:
                name_el = f'<a href="{product_url}" style="color:#1A1D1E;text-decoration:none;font-family:{font};font-weight:700;font-size:15px;letter-spacing:-0.01em">{item_name}</a>'
            else:
                name_el = f'<span style="color:#1A1D1E;font-family:{font};font-weight:700;font-size:15px;letter-spacing:-0.01em">{item_name}</span>'

            days_badge = NotificationManager._days_badge(d.get("days_remaining"))
            days_row = f'<div style="margin-bottom:6px">{days_badge}</div>' if days_badge else ""

            items_html += f"""
                <div style="padding:16px 0;{border}">
                    {days_row}
                    <table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
                        <td style="vertical-align:middle">
                            {name_el}
                            <div style="color:rgba(26,29,30,0.35);font-family:{font};font-size:13px;margin-top:2px">{d['retailer']} &middot; #{d.get('order_number', '')}</div>
                        </td>
                        <td style="text-align:right;vertical-align:middle;white-space:nowrap">
                            <div style="font-family:{font};font-weight:700;color:#1A1D1E;font-size:16px;letter-spacing:-0.01em">-${d['savings_amount']:.2f}</div>
                            <div style="font-family:{font};font-size:12px;color:rgba(26,29,30,0.25);margin-top:2px">${d['price_paid']:.2f} &rarr; ${d['current_price']:.2f}</div>
                        </td>
                    </tr></table>
                </div>"""

        # Build per-retailer claim draft sections
        next_steps_html = ""
        if claims_enabled and customer_name:
            from rover.claimer import ClaimManager

            retailer_groups = defaultdict(list)
            for d in drops:
                retailer_groups[d.get("retailer", "Unknown")].append(d)

            for retailer, items in retailer_groups.items():
                support_email = items[0].get("support_email")
                support_url = items[0].get("support_url")

                if support_email:
                    next_steps_html += f"""
                    <div style="margin-bottom:12px;padding:12px;background:#ecfdf5;border:1px solid rgba(0,0,0,0.06);border-radius:12px;font-family:{font}">
                        <strong style="color:#059669">{retailer}</strong>
                        <span style="color:rgba(26,29,30,0.5);font-size:13px"> — Claim will be sent automatically to {support_email}</span>
                    </div>"""
                elif support_url:
                    draft = ClaimManager.build_claim_message(customer_name, items, retailer)
                    escaped_draft = draft.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    next_steps_html += f"""
                    <div style="margin-bottom:12px;padding:12px;background:#fffbeb;border:1px solid rgba(0,0,0,0.06);border-radius:12px;font-family:{font}">
                        <strong style="color:#d97706">{retailer}</strong>
                        <span style="color:rgba(26,29,30,0.5);font-size:13px"> — Submit via
                            <a href="{support_url}" style="color:#F55446;text-decoration:none">support portal</a>
                        </span>
                        <details style="margin-top:8px">
                            <summary style="cursor:pointer;color:#F55446;font-size:13px">Copy-paste claim message</summary>
                            <pre style="margin:8px 0 0 0;padding:12px;background:#FAFAFA;border:1px solid rgba(0,0,0,0.06);border-radius:8px;font-size:12px;white-space:pre-wrap;font-family:inherit;color:#1A1D1E">{escaped_draft}</pre>
                        </details>
                    </div>"""
                else:
                    next_steps_html += f"""
                    <div style="margin-bottom:12px;padding:12px;background:#FAFAFA;border:1px solid rgba(0,0,0,0.06);border-radius:12px;font-family:{font}">
                        <strong style="color:rgba(26,29,30,0.5)">{retailer}</strong>
                        <span style="color:rgba(26,29,30,0.35);font-size:13px"> — Contact retailer directly to request a price adjustment</span>
                    </div>"""

        next_steps_section = ""
        if next_steps_html:
            next_steps_section = f"""
            <div style="margin-top:24px;padding-top:20px;border-top:1px solid rgba(0,0,0,0.06)">
                <h2 style="margin:0 0 12px 0;font-family:{font};font-size:16px;font-weight:700;color:#1A1D1E;letter-spacing:-0.01em">Next Steps</h2>
                {next_steps_html}
            </div>"""

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:transparent;font-family:{font};-webkit-font-smoothing:antialiased">
<div style="max-width:600px;margin:20px auto;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid rgba(0,0,0,0.06)">

    <div style="padding:28px 28px 0 28px">
        <img src="https://rover-web.vercel.app/_next/image?url=%2FRoverBlackLogo.png&amp;w=256&amp;q=75" alt="Rover" style="height:36px;width:auto">
    </div>

    <div style="padding:20px 28px">
        <div style="background:#FAFAFA;border:1px solid rgba(0,0,0,0.06);border-radius:16px;padding:28px;padding-bottom:12px;margin-bottom:24px;text-align:center">
            <div style="font-family:{font};font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.15em;color:rgba(26,29,30,0.25);margin-bottom:12px">Rover found savings</div>
            <div style="font-family:{font};font-size:44px;font-weight:800;color:#1A1D1E;letter-spacing:-0.03em">${total_savings:.2f}</div>
            <div style="font-family:{font};font-size:14px;color:rgba(26,29,30,0.5);margin-top:6px">{count} item{'s' if count != 1 else ''} ready for you to claim</div>
            <img src="https://i.ibb.co/qqZn4R5/Untitled-design.png" alt="Rover" style="display:block;width:32%;max-width:120px;height:auto;margin:16px auto 0 auto">
        </div>

        {items_html}

        {next_steps_section}
    </div>

    <div style="background:#FAFAFA;padding:16px 28px;border-top:1px solid rgba(0,0,0,0.06)">
        <div style="font-family:{font};font-size:12px;color:rgba(26,29,30,0.35)">&copy; 2026 Rover. All rights reserved.</div>
    </div>

</div>
</body>
</html>"""

        return html
