import logging
import random
import re
import threading
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

REMOVE_TAGS = {"script", "style", "nav", "footer", "header", "aside", "iframe", "noscript", "svg"}

CONTENT_SELECTORS = [
    "main",
    "article",
    "[role=main]",
    "#content",
    "#main-content",
    ".product",
    ".product-detail",
]

MAX_CLEANED_LENGTH = 12000


class Scraper:
    """Web scraper with anti-bot measures including rate limiting,
    user-agent rotation, and exponential backoff on retries."""

    def __init__(self, config: dict):
        self.config = config["scraping"]
        self.session = requests.Session()
        self._domain_last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def fetch(self, url: str) -> str | None:
        """Fetch a URL and return the raw HTML, or None on failure.

        Applies per-domain rate limiting, rotates user agents, sets
        realistic browser headers, and retries with exponential backoff
        on 429/503 responses.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        self._rate_limit(domain)

        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": f"{parsed.scheme}://{domain}/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        max_retries = self.config.get("max_retries", 3)
        timeout = self.config.get("timeout", 15)

        for attempt in range(max_retries):
            try:
                delay = random.uniform(
                    self.config.get("min_delay", 2),
                    self.config.get("max_delay", 5),
                )
                time.sleep(delay)

                response = self.session.get(url, headers=headers, timeout=timeout)

                if response.status_code == 200:
                    logger.debug("Successfully fetched %s", url)
                    return response.text

                if response.status_code in (429, 503):
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Received %d from %s, retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        url,
                        backoff,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(backoff)
                    continue

                logger.warning(
                    "Unexpected status %d from %s", response.status_code, url
                )
                return None

            except requests.RequestException as exc:
                backoff = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    "Request error for %s: %s, retrying in %.1fs (attempt %d/%d)",
                    url,
                    exc,
                    backoff,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(backoff)

        logger.error("All %d attempts failed for %s", max_retries, url)
        return None

    def clean_html(self, html: str, url: str | None = None) -> str:
        """Parse HTML and extract the main text content.

        Removes non-content elements (scripts, styles, navigation, etc.),
        attempts to locate the main content area, and returns cleaned text
        truncated to ~12000 characters.
        """
        soup = BeautifulSoup(html, "lxml")

        for tag in soup.find_all(REMOVE_TAGS):
            tag.decompose()

        content_element = None
        for selector in CONTENT_SELECTORS:
            content_element = soup.select_one(selector)
            if content_element:
                break

        if content_element is None:
            content_element = soup.body if soup.body else soup

        text = content_element.get_text(separator="\n", strip=True)

        # Collapse multiple blank lines into a single blank line
        text = re.sub(r"\n{3,}", "\n\n", text)

        if len(text) > MAX_CLEANED_LENGTH:
            text = text[:MAX_CLEANED_LENGTH]

        return text

    def extract_footer_links(self, html: str, base_url: str) -> list[dict]:
        """Extract links from the page footer.

        Returns a list of dicts with 'text' and 'href' keys, with hrefs
        resolved to absolute URLs. Falls back to all page links if no
        footer element is found.
        """
        soup = BeautifulSoup(html, "lxml")

        footer = soup.find("footer") or soup.select_one("[role=contentinfo]")

        if footer:
            anchors = footer.find_all("a", href=True)
        else:
            # No footer found — scan the bottom half of all links as a fallback
            all_anchors = soup.find_all("a", href=True)
            midpoint = len(all_anchors) // 2
            anchors = all_anchors[midpoint:]

        parsed_base = urlparse(base_url)
        links = []
        for a in anchors:
            href = a["href"].strip()
            text = a.get_text(strip=True).lower()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            # Resolve relative URLs
            if href.startswith("/"):
                href = f"{parsed_base.scheme}://{parsed_base.netloc}{href}"
            elif not href.startswith("http"):
                continue
            links.append({"text": text, "href": href})

        return links

    def _rate_limit(self, domain: str) -> None:
        """Ensure a minimum interval between requests to the same domain."""
        with self._lock:
            last = self._domain_last_request.get(domain, 0)
            elapsed = time.time() - last
            min_interval = self.config.get("rate_limit_per_domain", 10)
            if elapsed < min_interval:
                wait = min_interval - elapsed
                logger.debug("Rate limiting %s: sleeping %.1fs", domain, wait)
                time.sleep(wait)
            self._domain_last_request[domain] = time.time()
