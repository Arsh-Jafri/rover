# Rover — Automated Price Adjustment Agent

## What It Does
Rover is a Python background service that automatically monitors your Gmail for purchase receipts, tracks product prices via web scraping, and detects price drops within retailer return/refund windows. The goal is to save money by catching price adjustments you'd otherwise miss.

## How It Works

### Pipeline
1. **Email Scan** (every 30 min) — Connects to Gmail via OAuth2 API, searches `category:purchases`
2. **Regex Pre-Filter** — Checks for price signals ($XX.XX) + order keywords. Filters out shipping, delivery, refunds, subscriptions, and food delivery (DoorDash, Grubhub, etc.)
3. **Receipt Parsing** — Uses Claude with `tool_use` to extract structured data: items array (name, price per item pre-tax), retailer, purchase date, order number. Email date header passed to prevent wrong year extraction.
4. **URL Discovery** — Searches DuckDuckGo for product page URLs, uses Claude to pick the best match from the retailer's own domain
5. **Price Checking** (every 6 hr) — For purchases with URLs still within refund window, scrapes the product page (with Playwright fallback for bot-protected sites), extracts JSON-LD product data, and uses Claude to extract the current price
6. **Savings Detection** — If current price < price paid, records the drop in the database
7. **Email Notification** — Sends a consolidated HTML email via Gmail API with all price drops, savings amounts, and days remaining in refund window

### Refund Policy System (Three-Tier)
- **Tier 1**: Known retailer database (`retailers.yaml`) with 22 major retailers and their refund windows (e.g., Amazon 30d, Target 90d, Apple 14d, REI 365d)
- **Tier 2**: For unknown retailers, searches DuckDuckGo by retailer name for policy pages, scrapes them, and uses Claude to extract the refund window in days
- **Tier 3**: Falls back to scraping retailer homepage footer links for policy pages, then a 14-day default

### Scraping
- User-agent rotation (12 real browser UAs)
- Per-domain rate limiting (min 10s between requests)
- Random 2-5s delays between requests
- Exponential backoff on 429/503 responses
- CAPTCHA detection on 200 responses
- Content-type validation (rejects binary/video/JSON API responses)
- **Playwright headless browser fallback** for sites with strong bot detection (Abercrombie, Hollister)
- **JSON-LD structured data extraction** — extracts product name, price, availability from `<script type="application/ld+json">` before stripping scripts

### LLM Usage
- **Receipt parsing** (`parser.py`): `tool_use` with forced tool choice — the only call that uses tool_use, justified by multi-field nested structured output
- **Price extraction** (`price_checker.py`): Plain prompt → number or "null". `temperature=0`
- **URL selection** (`price_checker.py`): Plain prompt → URL or "null". `temperature=0`
- **Policy extraction** (`policies.py`): Plain prompt → number of days or "null". `temperature=0`

## Architecture

```
rover/
├── main.py              # Entry point — initializes all components, runs scheduler
├── config.py            # YAML config + .env loading (singleton)
├── db.py                # SQLite (WAL mode) — 5 tables, parameterized SQL
├── gmail.py             # Gmail API OAuth2 + email fetching with pagination
├── parser.py            # Regex pre-filter + LLM structured extraction (tool_use)
├── scraper.py           # HTTP fetcher + Playwright fallback + JSON-LD extraction
├── price_checker.py     # DuckDuckGo URL discovery, LLM price extraction, savings detection
├── scheduler.py         # APScheduler — email scan (30min) + price check (6hr) + notifications
├── notifier.py          # Email notifications via Gmail API — consolidated HTML alerts
├── policies.py          # Retailer policy lookup (DB → YAML → LLM scrape → default)
├── dev_server.py        # Flask dev UI for interactive pipeline testing (localhost:5001)
├── logger.py            # Console + file logging
retailers.yaml           # Known retailer database (22 retailers)
config.yaml              # Runtime config (gitignored)
```

## Database Schema (SQLite)
- **purchases** — Parsed receipt data with per-item prices. `gmail_message_id` UNIQUE for idempotency, `order_number` + `item_name` for dedup across multi-item orders
- **price_checks** — Each scrape attempt with status (success/scrape_failed/parse_failed)
- **savings** — Detected price drops with status tracking (new/notified/claimed)
- **metadata** — Key-value store for state (e.g., last_email_scan_date)
- **retailers** — Known retailer info (seeded from YAML, enriched by scraping). `source` field tracks provenance (manual/scraped/default)

## Key Design Decisions
- **Gmail API over IMAP** — Richer search, OAuth2, structured data
- **Regex pre-filter over LLM classification** — Faster, cheaper, and more reliable. Accept false positives (the LLM parser catches them). Previous LLM classifier was too conservative and missed receipts.
- **tool_use only for receipt parsing** — Multi-field structured output justifies tool_use. All other LLM calls use plain prompts with temperature=0 for simpler, cheaper, more deterministic results.
- **Playwright fallback** — Most retailers render prices in initial HTML, but Abercrombie/Hollister block requests with 403. Playwright handles these.
- **JSON-LD extraction** — Many e-commerce sites embed product data in structured JSON-LD. Extracting this before stripping scripts gives the LLM reliable price data.
- **No ORM** — Raw parameterized SQL; 5 tables don't need SQLAlchemy
- **Idempotent** — `gmail_message_id` UNIQUE prevents duplicate purchases on re-runs. Multi-item orders use `email_id:idx` format.

## Tech Stack
Python 3.11+, anthropic, google-api-python-client, APScheduler, requests, BeautifulSoup, lxml, Playwright, Flask (dev only), SQLite

## Current Status
- Full pipeline functional end-to-end: email scan → receipt parsing → URL discovery → price scraping → savings detection → email notification
- Tested with real Gmail data: 99 emails scanned, 12 receipts parsed, 17 purchases stored, 7 product URLs found, 3 price drops detected ($7.72 + $12.00 + $5.00 savings)
- Email notifications working — consolidated HTML email with price drop table, savings amounts, product links, and days remaining in refund window
- Dev server UI at localhost:5001 for interactive testing of each pipeline step, including test notification sending
- 161 tests covering all modules (db, parser, scraper, policies, price_checker, notifier)
- Landing page deployed at tryrover.app

## Next Steps
- **Automated claim emails** — send price adjustment request emails directly to retailers using support_email from DB
- **Improve generic item matching** — items like "KNITS", "GRAPHICS" need better product URL discovery
- **Rate limit resilience** — DuckDuckGo rate limits during burst operations; consider paid search API for production
- **Email forwarding intake** — allow users to forward receipts to Rover instead of connecting Gmail (for multi-user product)
