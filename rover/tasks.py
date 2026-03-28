from rover.celery_app import app
from rover.logger import get_logger

logger = get_logger("tasks")


# ---------------------------------------------------------------------------
# Dispatch tasks (triggered by Beat)
# ---------------------------------------------------------------------------


@app.task(name="rover.tasks.dispatch_email_scan")
def dispatch_email_scan():
    """Query all Gmail-connected users and queue a scan task for each."""
    from rover.db import Database

    db = Database()
    try:
        users = db.get_users_with_gmail()
        logger.info("Dispatching email scan for %d users", len(users))
        for user in users:
            scan_emails_for_user.delay(str(user["id"]))
    finally:
        db.close()


@app.task(name="rover.tasks.dispatch_price_check")
def dispatch_price_check():
    """Query all users with trackable purchases and queue price checks."""
    from rover.db import Database

    db = Database()
    try:
        users = db.get_users_with_active_purchases()
        logger.info("Dispatching price check for %d users", len(users))
        for user in users:
            check_prices_for_user.delay(str(user["id"]))
    finally:
        db.close()


@app.task(name="rover.tasks.dispatch_claims")
def dispatch_claims():
    """Query all users with notified savings and queue claim tasks."""
    from rover.db import Database

    db = Database()
    try:
        users = db.get_users_with_notified_savings()
        logger.info("Dispatching claims for %d users", len(users))
        for user in users:
            send_claims_for_user.delay(str(user["id"]))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Per-user tasks
# ---------------------------------------------------------------------------


@app.task(
    name="rover.tasks.scan_emails_for_user",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=300,
)
def scan_emails_for_user(user_id: str):
    """Scan Gmail for new purchase receipts for a single user."""
    from rover.config import load_config
    from rover.db import Database
    from rover.gmail import GmailClient
    from rover.parser import ReceiptParser
    from rover.scheduler import RoverScheduler
    from rover.token_store import GmailTokenStore

    logger.info("Starting email scan for user %s", user_id)
    config = load_config()
    db = Database()
    try:
        token_store = GmailTokenStore(db)
        gmail = GmailClient(
            token_store=token_store,
            search_query=config.get("gmail", {}).get(
                "search_query", "category:purchases"
            ),
        )
        gmail.authenticate(user_id)
        parser = ReceiptParser(config)

        # Reuse scheduler's scan_emails logic — only needs db, gmail, parser, config
        sched = RoverScheduler(
            config=config,
            db=db,
            gmail_client=gmail,
            receipt_parser=parser,
            price_checker=None,
            policy_lookup=None,
        )
        sched.scan_emails(user_id)
        logger.info("Email scan complete for user %s", user_id)
    finally:
        db.close()


@app.task(
    name="rover.tasks.check_prices_for_user",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=600,
    rate_limit="10/m",
)
def check_prices_for_user(user_id: str):
    """Check prices and send notifications for a single user."""
    import anthropic as anthropic_mod

    from rover.config import load_config
    from rover.db import Database
    from rover.notifier import NotificationManager
    from rover.policies import PolicyLookup
    from rover.price_checker import PriceChecker
    from rover.scraper import Scraper

    logger.info("Starting price check for user %s", user_id)
    config = load_config()
    db = Database()
    try:
        scraper = Scraper(config)
        llm_client = anthropic_mod.Anthropic()
        llm_model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")
        policy_lookup = PolicyLookup(
            db=db,
            scraper=scraper,
            llm_client=llm_client,
            llm_model=llm_model,
            retailers_yaml_path=config.get("retailers_yaml", "retailers.yaml"),
            default_window=config.get("default_refund_window_days", 14),
        )
        price_checker = PriceChecker(
            config=config, db=db, scraper=scraper, policy_lookup=policy_lookup
        )
        notifier = NotificationManager(
            config=config, db=db, policy_lookup=policy_lookup
        )

        drops = price_checker.check_all_prices(user_id)
        logger.info("Price check for user %s: %d drops detected", user_id, len(drops))

        if drops:
            try:
                notifier.notify_drops(user_id, drops)
            except Exception:
                logger.exception(
                    "Notification failed for user %s — drops saved in DB", user_id
                )
    finally:
        db.close()


@app.task(
    name="rover.tasks.send_claims_for_user",
    autoretry_for=(Exception,),
    max_retries=2,
    retry_backoff=True,
    retry_backoff_max=300,
)
def send_claims_for_user(user_id: str):
    """Send claim emails to retailers for a single user."""
    import anthropic as anthropic_mod

    from rover.claimer import ClaimManager
    from rover.config import load_config
    from rover.db import Database
    from rover.gmail import GmailClient
    from rover.policies import PolicyLookup
    from rover.scraper import Scraper
    from rover.token_store import GmailTokenStore

    logger.info("Starting claims for user %s", user_id)
    config = load_config()
    db = Database()
    try:
        token_store = GmailTokenStore(db)
        gmail = GmailClient(
            token_store=token_store,
            search_query=config.get("gmail", {}).get(
                "search_query", "category:purchases"
            ),
        )
        gmail.authenticate(user_id)

        scraper = Scraper(config)
        llm_client = anthropic_mod.Anthropic()
        llm_model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")
        policy_lookup = PolicyLookup(
            db=db,
            scraper=scraper,
            llm_client=llm_client,
            llm_model=llm_model,
            retailers_yaml_path=config.get("retailers_yaml", "retailers.yaml"),
            default_window=config.get("default_refund_window_days", 14),
        )
        claimer = ClaimManager(
            config=config, db=db, gmail_client=gmail, policy_lookup=policy_lookup
        )

        result = claimer.send_claims(user_id)
        logger.info("Claims for user %s: %s", user_id, result)
    finally:
        db.close()
