"""Rover — Automated Price Adjustment Agent"""

import sys

import anthropic

from rover.claimer import ClaimManager
from rover.config import get_config
from rover.db import Database
from rover.gmail import GmailClient
from rover.logger import get_logger, setup_logging
from rover.notifier import NotificationManager
from rover.parser import ReceiptParser
from rover.policies import PolicyLookup
from rover.price_checker import PriceChecker
from rover.scheduler import RoverScheduler
from rover.scraper import Scraper

logger = get_logger("main")


def main():
    config = get_config()
    setup_logging(config)

    logger.info("Initializing Rover")

    db = Database(config.get("database", {}).get("path", "rover.db"))

    gmail = GmailClient(config)
    gmail.authenticate()

    parser = ReceiptParser(config)
    scraper = Scraper(config)

    llm_client = anthropic.Anthropic()
    llm_model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")

    retailers_yaml = config.get("retailers_yaml", "retailers.yaml")
    default_window = config.get("default_refund_window_days", 14)

    policy_lookup = PolicyLookup(
        db=db,
        scraper=scraper,
        llm_client=llm_client,
        llm_model=llm_model,
        retailers_yaml_path=retailers_yaml,
        default_window=default_window,
    )

    price_checker = PriceChecker(
        config=config,
        db=db,
        scraper=scraper,
        policy_lookup=policy_lookup,
    )

    notifier = NotificationManager(
        config=config,
        db=db,
        policy_lookup=policy_lookup,
    )

    claimer = ClaimManager(
        config=config,
        db=db,
        gmail_client=gmail,
        policy_lookup=policy_lookup,
    )

    scheduler = RoverScheduler(config, db, gmail, parser, price_checker, policy_lookup, notifier=notifier, claimer=claimer)

    logger.info("Running initial email scan")
    scheduler.scan_emails()

    logger.info("Running initial price check")
    scheduler.check_prices()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down Rover")
        sys.exit(0)


if __name__ == "__main__":
    main()
