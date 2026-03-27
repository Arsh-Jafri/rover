import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

import anthropic
import yaml

from rover.db import Database
from rover.scraper import Scraper

logger = logging.getLogger(__name__)

POLICY_EXTRACTION_PROMPT = """Analyze this retailer's return/refund policy page and extract the following information.
Respond using the provided tool.

Page content:
{content}
"""


@dataclass
class RetailerInfo:
    """Structured retailer return policy information."""

    name: str
    domain: str
    refund_window_days: int
    support_email: str | None = None
    support_url: str | None = None
    policy_url: str | None = None
    source: str = "manual"


class PolicyLookup:
    """Looks up retailer refund policies from the database, YAML seed data,
    or by scraping retailer websites and using an LLM to extract policy details."""

    POLICY_KEYWORDS = [
        "return", "refund", "exchange", "price adjustment",
        "price match", "price protection",
    ]

    def __init__(
        self,
        db: Database,
        scraper: Scraper,
        llm_client: anthropic.Anthropic,
        llm_model: str,
        retailers_yaml_path: str,
        default_window: int = 14,
    ):
        self.db = db
        self.scraper = scraper
        self.client = llm_client
        self.model = llm_model
        self.default_window = default_window
        self._seed_retailers(retailers_yaml_path)

    def _seed_retailers(self, path: str) -> None:
        """Load retailers.yaml and upsert each entry into the database.

        Only seeds entries that would have source='manual' -- does not
        overwrite retailers that were previously populated via scraping.
        """
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("Retailers YAML not found at %s, skipping seed", path)
            return

        retailers = data.get("retailers", [])
        for entry in retailers:
            domain = entry["domain"]
            existing = self.db.get_retailer_by_domain(domain)
            if existing and existing.get("source") == "scraped":
                logger.debug("Skipping seed for %s (already scraped)", domain)
                continue

            self.db.upsert_retailer(
                name=entry["name"],
                domain=domain,
                refund_window_days=entry["refund_window_days"],
                support_email=entry.get("support_email"),
                support_url=entry.get("support_url"),
                policy_url=entry.get("policy_url"),
                source="manual",
            )
        logger.info("Seeded %d retailers from %s", len(retailers), path)

    def get_retailer_info(self, domain: str) -> RetailerInfo:
        """Look up retailer info by domain.

        Checks the database first, then attempts to scrape the retailer's
        policy page. Falls back to defaults if both fail.
        """
        row = self.db.get_retailer_by_domain(domain)
        if row:
            return RetailerInfo(
                name=row["name"],
                domain=row["domain"],
                refund_window_days=row["refund_window_days"],
                support_email=row.get("support_email"),
                support_url=row.get("support_url"),
                policy_url=row.get("policy_url"),
                source=row.get("source", "manual"),
            )

        scraped = self._scrape_policy(domain)
        if scraped:
            return scraped

        logger.info(
            "No policy found for %s, using default window of %d days",
            domain,
            self.default_window,
        )
        return RetailerInfo(
            name=domain,
            domain=domain,
            refund_window_days=self.default_window,
            source="manual",
        )

    def _scrape_policy(self, domain: str) -> RetailerInfo | None:
        """Attempt to scrape and parse the return policy from a retailer's website.

        Fetches the retailer homepage, scans footer links for return/refund
        policy pages, then uses an LLM to extract structured policy data.
        """
        clean_domain = domain.removeprefix("www.")
        homepage_url = f"https://www.{clean_domain}"
        html = self.scraper.fetch(homepage_url)
        if not html:
            logger.debug("Could not fetch homepage for %s", domain)
            return None

        footer_links = self.scraper.extract_footer_links(html, homepage_url)
        policy_links = self._find_policy_links(footer_links)

        if not policy_links:
            logger.debug("No policy links found in footer for %s", domain)
            return None

        for link in policy_links:
            url = link["href"]
            logger.debug("Trying policy link: %s (%s)", url, link["text"])

            policy_html = self.scraper.fetch(url)
            if not policy_html:
                continue

            cleaned = self.scraper.clean_html(policy_html, url=url)
            if len(cleaned.strip()) < 50:
                continue

            extracted = self._extract_policy_with_llm(cleaned, domain)
            if extracted is None:
                continue

            refund_window = extracted.get("refund_window_days", self.default_window)
            support_email = extracted.get("support_email")
            support_url = extracted.get("support_url")

            self.db.upsert_retailer(
                name=domain.split(".")[0].title(),
                domain=domain,
                refund_window_days=refund_window,
                support_email=support_email,
                support_url=support_url,
                policy_url=url,
                source="scraped",
            )

            logger.info(
                "Scraped policy for %s: %d-day window (from footer link)",
                domain,
                refund_window,
            )
            return RetailerInfo(
                name=domain.split(".")[0].title(),
                domain=domain,
                refund_window_days=refund_window,
                support_email=support_email,
                support_url=support_url,
                policy_url=url,
                source="scraped",
            )

        logger.debug("Could not extract policy for %s from any footer link", domain)
        return None

    def _find_policy_links(self, links: list[dict]) -> list[dict]:
        """Filter and rank footer links that likely point to a return/refund policy."""
        matches = []
        for link in links:
            text = link["text"]
            href = link["href"].lower()
            for keyword in self.POLICY_KEYWORDS:
                if keyword in text or keyword in href:
                    matches.append(link)
                    break
        return matches

    def _extract_policy_with_llm(
        self, content: str, domain: str
    ) -> dict | None:
        """Use Claude with tool_use to extract policy details from page text."""
        tools = [
            {
                "name": "extract_policy",
                "description": "Extract return/refund policy details from the page content.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "refund_window_days": {
                            "type": "integer",
                            "description": "Number of days from purchase within which a return/refund is accepted.",
                        },
                        "support_email": {
                            "type": ["string", "null"],
                            "description": "Customer support email address, or null if not found.",
                        },
                        "support_url": {
                            "type": ["string", "null"],
                            "description": "Customer support or contact page URL, or null if not found.",
                        },
                    },
                    "required": ["refund_window_days"],
                },
            }
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                tools=tools,
                tool_choice={"type": "tool", "name": "extract_policy"},
                messages=[
                    {
                        "role": "user",
                        "content": POLICY_EXTRACTION_PROMPT.format(content=content),
                    }
                ],
            )

            for block in response.content:
                if block.type == "tool_use" and block.name == "extract_policy":
                    return block.input

        except anthropic.APIError as exc:
            logger.error("LLM API error extracting policy for %s: %s", domain, exc)

        return None

    def is_within_refund_window(self, purchase_date: str, domain: str) -> bool:
        """Check whether today falls within the retailer's refund window
        relative to the given purchase date.

        Args:
            purchase_date: Date string in YYYY-MM-DD format.
            domain: Retailer domain (e.g. "amazon.com").

        Returns:
            True if the purchase is still within the refund window.
        """
        info = self.get_retailer_info(domain)

        try:
            purchased = datetime.strptime(purchase_date, "%Y-%m-%d")
        except ValueError:
            logger.error("Invalid purchase_date format: %s", purchase_date)
            return False

        deadline = purchased + timedelta(days=info.refund_window_days)
        return datetime.now() <= deadline

    @staticmethod
    def extract_domain(
        url: str | None = None, email_sender: str | None = None
    ) -> str | None:
        """Extract a clean domain from a URL or email sender address.

        Strips the 'www.' prefix and returns a domain like 'amazon.com'.
        """
        domain = None

        if url:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

        elif email_sender:
            match = re.search(r"@([\w.-]+)", email_sender)
            if match:
                domain = match.group(1).lower()

        if domain:
            if domain.startswith("www."):
                domain = domain[4:]
            return domain

        return None
