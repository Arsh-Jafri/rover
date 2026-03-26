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

        for email in emails:
            subject = email.get("subject", "")
            sender = email.get("from", "")
            body_text = email.get("body_text", "")
            body_html = email.get("body_html", "")

            is_receipt = self.parser.classify_email(subject, sender, body_text)
            if not is_receipt:
                continue

            receipts_found += 1
            receipt = self.parser.parse_receipt(subject, sender, body_text, body_html)
            if not receipt:
                logger.warning("Failed to parse receipt from: %s", subject)
                continue

            purchase_id = self.db.add_purchase(
                gmail_message_id=email["id"],
                item_name=receipt["item_name"],
                price_paid=receipt["price_paid"],
                product_url=receipt.get("product_url"),
                retailer=receipt["retailer"],
                purchase_date=receipt["purchase_date"],
                currency=receipt.get("currency", "USD"),
                raw_email_snippet=body_text[:500] if body_text else None,
            )
            if purchase_id:
                purchases_stored += 1

            self._enrich_retailer(receipt)

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
