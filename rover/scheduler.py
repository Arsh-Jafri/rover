from urllib.parse import urlparse

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from rover.logger import get_logger

logger = get_logger("scheduler")


class RoverScheduler:
    def __init__(self, config, db, gmail_client, receipt_parser, price_checker, policy_lookup):
        self.config = config
        self.db = db
        self.gmail = gmail_client
        self.parser = receipt_parser
        self.price_checker = price_checker
        self.policy_lookup = policy_lookup
        self.scheduler = BlockingScheduler()

    def start(self):
        sched_config = self.config.get("scheduler", {})
        email_minutes = sched_config.get("email_scan_interval_minutes", 30)
        price_hours = sched_config.get("price_check_interval_hours", 6)

        self.scheduler.add_job(
            self.scan_emails,
            trigger=IntervalTrigger(minutes=email_minutes),
            id="email_scan",
            name="Scan Gmail for new receipts",
        )
        self.scheduler.add_job(
            self.check_prices,
            trigger=IntervalTrigger(hours=price_hours),
            id="price_check",
            name="Check prices for tracked purchases",
        )

        logger.info(
            "Starting scheduler — email scan every %d min, price check every %d hr",
            email_minutes,
            price_hours,
        )
        self.scheduler.start()

    def scan_emails(self):
        last_scan = self.db.get_metadata("last_email_scan_date")
        logger.info("Scanning emails (after %s)", last_scan or "all time")

        emails = self.gmail.fetch_emails(after_date=last_scan)
        logger.info("Fetched %d emails", len(emails))

        receipts_found = 0
        purchases_stored = 0

        for i, email in enumerate(emails):
            subject = email.get("subject", "")
            sender = email.get("from", "")
            body_text = email.get("body_text", "")
            body_html = email.get("body_html", "")

            if not self.parser.is_likely_receipt(subject, body_text, body_html, sender):
                continue

            items = self.parser.parse_receipt(subject, sender, body_text, body_html)
            if not items:
                continue

            receipts_found += 1
            order_number = items[0].get("order_number")

            for idx, item in enumerate(items):
                if order_number and self.db.has_purchase_for_item(
                    item["retailer"], order_number, item["item_name"]
                ):
                    logger.info("Duplicate item '%s' in order %s — skipping", item["item_name"], order_number)
                    continue
                item_msg_id = f"{email['id']}:{idx}" if len(items) > 1 else email["id"]
                purchase_id = self.db.add_purchase(
                    gmail_message_id=item_msg_id,
                    item_name=item["item_name"],
                    price_paid=item["price_paid"],
                    product_url=item.get("product_url"),
                    retailer=item["retailer"],
                    purchase_date=item["purchase_date"],
                    currency=item.get("currency", "USD"),
                    order_number=order_number,
                    raw_email_snippet=body_text[:500] if body_text else None,
                )
                if purchase_id:
                    purchases_stored += 1

            self._enrich_retailer(items[0])

        from datetime import date
        self.db.set_metadata("last_email_scan_date", date.today().isoformat())

        logger.info(
            "Email scan complete: %d fetched, %d receipts, %d new purchases",
            len(emails),
            receipts_found,
            purchases_stored,
        )

    def check_prices(self):
        if not self.price_checker:
            logger.warning("PriceChecker not available — skipping price check")
            return
        logger.info("Starting price check run")
        drops = self.price_checker.check_all_prices()
        logger.info("Price check complete: %d drops detected", len(drops))

    def _enrich_retailer(self, receipt: dict):
        product_url = receipt.get("product_url")
        if not product_url:
            return

        try:
            domain = urlparse(product_url).netloc.lower()
            if not domain:
                return
            if domain.startswith("www."):
                domain = domain[4:]
        except Exception:
            return

        existing = self.db.get_retailer_by_domain(domain)
        if existing:
            return

        refund_window = self.config.get("default_refund_window_days", 14)
        self.db.upsert_retailer(
            name=receipt.get("retailer", domain),
            domain=domain,
            refund_window_days=refund_window,
            support_email=receipt.get("support_email"),
            support_url=receipt.get("support_url"),
            source="scraped",
        )
        logger.info("Added retailer: %s (%s)", receipt.get("retailer"), domain)
