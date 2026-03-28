import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote_plus, urlparse

import anthropic
import requests
import yaml
from bs4 import BeautifulSoup

from rover.db import Database
from rover.scraper import Scraper

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_DAYS_RE = re.compile(r"\d+")

POLICY_EXTRACTION_PROMPT = """How many days does this retailer allow for returns or price adjustments after purchase?

If there is a specific price adjustment/price protection window, prefer that over the general return window.
Respond with ONLY the number of days (e.g. 30), or "null" if not found. No other text.

Policy page content:
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
    source: str = "manual"  # "manual" | "scraped" | "default"
    window_type: str = "return"  # "price_adjustment" | "return" | "default"


class PolicyLookup:
    """Looks up retailer refund policies from the database, YAML seed data,
    or by scraping retailer websites and using an LLM to extract policy details."""

    POLICY_KEYWORDS = [
        "return", "refund", "exchange", "price adjustment",
        "price match", "price protection",
    ]

    CONTACT_KEYWORDS = [
        "contact", "help", "support", "customer service",
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

            extracted = self._extract_policy_with_llm(cleaned)
            refund_window = extracted.get("refund_window_days")
            if not refund_window:
                continue

            support_email = extracted.get("support_email")
            support_url = None

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

    def _find_contact_links(self, links: list[dict]) -> list[dict]:
        """Filter footer links that likely point to a contact/support page."""
        matches = []
        for link in links:
            text = link["text"]
            href = link["href"].lower()
            for keyword in self.CONTACT_KEYWORDS:
                if keyword in text or keyword in href:
                    matches.append(link)
                    break
        return matches

    @staticmethod
    def _extract_email_from_text(text: str) -> str | None:
        """Extract a support email from text, skipping noreply addresses."""
        emails = _EMAIL_PATTERN.findall(text)
        for email in emails:
            lower = email.lower()
            if any(skip in lower for skip in ["noreply", "no-reply", "donotreply", "mailer-daemon"]):
                continue
            return email
        return None

    def discover_support_email(self, domain: str) -> str | None:
        """Find a support email for a retailer by scraping contact/help pages.

        Checks the DB first, then scrapes the retailer's contact pages.
        Returns the email address or None.
        """
        existing = self.db.get_retailer_by_domain(domain)
        if existing and existing.get("support_email"):
            return existing["support_email"]

        clean_domain = domain.removeprefix("www.")
        homepage_url = f"https://www.{clean_domain}"
        html = self.scraper.fetch(homepage_url)
        if not html:
            return None

        footer_links = self.scraper.extract_footer_links(html, homepage_url)
        contact_links = self._find_contact_links(footer_links)

        for link in contact_links:
            url = link["href"]
            page_html = self.scraper.fetch(url)
            if not page_html:
                continue
            cleaned = self.scraper.clean_html(page_html, url=url)
            email = self._extract_email_from_text(cleaned)
            if email:
                # Update retailer record with discovered email
                if existing:
                    self.db.upsert_retailer(
                        name=existing["name"],
                        domain=domain,
                        refund_window_days=existing["refund_window_days"],
                        support_email=email,
                        support_url=existing.get("support_url"),
                        policy_url=existing.get("policy_url"),
                        source=existing.get("source", "manual"),
                    )
                logger.info("Discovered support email for %s: %s", domain, email)
                return email

        return None

    def _extract_policy_with_llm(self, content: str) -> dict:
        """Extract refund window from policy page text using an LLM,
        and contact email using regex.

        Returns dict with refund_window_days and support_email.
        """
        result = {"refund_window_days": None, "support_email": None}

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=32,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": POLICY_EXTRACTION_PROMPT.format(content=content),
                    }
                ],
            )
            text = response.content[0].text.strip()
            if "null" not in text.lower():
                match = _DAYS_RE.search(text)
                if match:
                    days = int(match.group())
                    if 1 <= days <= 365:
                        result["refund_window_days"] = days
        except anthropic.APIError as exc:
            logger.error("LLM API error extracting policy: %s", exc)

        result["support_email"] = self._extract_email_from_text(content)
        return result

    def discover_policy(self, domain: str, retailer_name: str | None = None) -> RetailerInfo:
        """Discover refund policy for a retailer by searching for their policy page.

        Uses DuckDuckGo to find the policy page, then LLM to extract the window.
        Falls back to footer link scraping, then defaults.
        """
        # Try DuckDuckGo search for policy page — prefer retailer name over domain
        policy_url, content = self._search_policy_page(domain, retailer_name=retailer_name)
        if content:
            extracted = self._extract_policy_with_llm(content)
            if extracted["refund_window_days"]:
                name = domain.split(".")[0].title()
                self.db.upsert_retailer(
                    name=name, domain=domain,
                    refund_window_days=extracted["refund_window_days"],
                    support_email=extracted["support_email"],
                    policy_url=policy_url,
                    source="scraped",
                )
                logger.info(
                    "Discovered policy for %s: %d-day window",
                    domain, extracted["refund_window_days"],
                )
                return RetailerInfo(
                    name=name, domain=domain,
                    refund_window_days=extracted["refund_window_days"],
                    support_email=extracted["support_email"],
                    policy_url=policy_url,
                    source="scraped",
                )

        # Fall back to footer link scraping
        scraped = self._scrape_policy(domain)
        if scraped:
            return scraped

        # Default: 30 days
        logger.info("No policy found for %s, using default 30-day window", domain)
        return RetailerInfo(
            name=domain, domain=domain,
            refund_window_days=30,
            source="default",
            window_type="default",
        )

    def _search_policy_page(
        self, domain: str, retailer_name: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Search DuckDuckGo for a retailer's return/refund policy page.

        Searches by retailer name (e.g. "On Running return policy") for better
        results than domain-based search. Falls back to domain if no name given.

        Returns (policy_url, cleaned_text) or (None, None).
        """
        from rover.price_checker import _get_ddg_session

        search_term = retailer_name if retailer_name else domain
        query = f"{search_term} return refund policy"
        ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

        session = _get_ddg_session()

        try:
            resp = session.get(ddg_url, timeout=10)
        except requests.RequestException:
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")
        if "bots use DuckDuckGo" in soup.get_text():
            import rover.price_checker as _pc
            logger.warning("DuckDuckGo rate limited during policy search — resetting session")
            _pc._ddg_session = None
            return None, None

        # Find the best policy link from search results
        clean_domain = domain.removeprefix("www.")
        for link in soup.select(".result__a"):
            raw_href = link.get("href", "")
            parsed = urlparse(raw_href)
            qs = parse_qs(parsed.query)
            actual_url = qs.get("uddg", [None])[0] or raw_href
            if not actual_url or not actual_url.startswith("http"):
                continue
            if "duckduckgo.com" in actual_url:
                continue
            # Prefer results from the retailer's own site, but also accept
            # results where the domain or retailer name appears in the URL
            result_domain = urlparse(actual_url).netloc.lower()
            name_parts = [p.lower() for p in (retailer_name or "").split() if len(p) > 2]
            domain_match = clean_domain in result_domain
            name_match = any(part in result_domain for part in name_parts)
            if not domain_match and not name_match:
                continue
            title = link.get_text(strip=True).lower()
            url_lower = actual_url.lower()
            if any(kw in title or kw in url_lower for kw in ["return", "refund", "policy", "exchange"]):
                # Found a policy page — fetch and extract
                policy_html = self.scraper.fetch(actual_url)
                if policy_html:
                    cleaned = self.scraper.clean_html(policy_html, url=actual_url)
                    if len(cleaned.strip()) > 50:
                        return actual_url, cleaned
        return None, None

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
