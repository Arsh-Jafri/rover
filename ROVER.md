# Rover — Automated Price Adjustment Agent

## What It Does
Rover is a Python background service that automatically monitors your Gmail for purchase receipts, tracks product prices via web scraping, and detects price drops within retailer return/refund windows. The goal is to save money by catching price adjustments you'd otherwise miss.

## How It Works

### Pipeline
1. **Email Scan** (every 30 min) — Connects to Gmail via OAuth2 API, searches for purchase-related emails
2. **Classification** — Uses Claude (LLM) to filter out shipping/delivery notifications, keeping only emails with actual prices
3. **Receipt Parsing** — Uses Claude with tool_use to extract structured data: item name, price paid, product URL, retailer, purchase date
4. **Price Checking** (every 6 hr) — For purchases with product URLs still within their refund window, scrapes the product page and uses Claude to extract the current price
5. **Savings Detection** — If current price < price paid, records the drop in the database

### Refund Policy System (Two-Tier)
- **Tier 1**: Known retailer database (`retailers.yaml`) with 18 major retailers and their refund windows (e.g., Amazon 30d, Target 90d, REI 365d)
- **Tier 2**: For unknown retailers, scrapes common policy page paths (`/return-policy`, `/returns`, etc.) and uses Claude to extract the refund window. Falls back to a conservative 14-day default.

### Anti-Bot Measures
- User-agent rotation (12 real browser UAs)
- Per-domain rate limiting (min 10s between requests)
- Random 2-5s delays between requests
- Exponential backoff on 429/503 responses
- Realistic browser headers and cookie persistence

## Architecture

```
rover/
├── main.py              # Entry point — initializes all components, runs scheduler
├── config.py            # YAML config + .env loading (singleton)
├── db.py                # SQLite (WAL mode) — 5 tables, parameterized SQL
├── gmail.py             # Gmail API OAuth2 + email fetching with pagination
├── parser.py            # LLM receipt classification + structured extraction
├── scraper.py           # HTTP fetcher + BeautifulSoup HTML cleaning
├── price_checker.py     # LLM price extraction, savings detection
├── scheduler.py         # APScheduler — email scan + price check jobs
├── policies.py          # Retailer policy lookup (DB → YAML → scrape → default)
├── logger.py            # Console + file logging
retailers.yaml           # Known retailer database (18 retailers)
config.yaml              # Runtime config (gitignored)
```

## Database Schema (SQLite)
- **purchases** — Parsed receipt data (gmail_message_id is UNIQUE for idempotency)
- **price_checks** — Each scrape attempt with status (success/scrape_failed/parse_failed)
- **savings** — Detected price drops with status tracking (new/notified/claimed)
- **metadata** — Key-value store for state (e.g., last_email_scan_date)
- **retailers** — Known retailer info (seeded from YAML, enriched by scraping)

## Key Design Decisions
- **Gmail API over IMAP** — Richer search, OAuth2, structured data
- **Two-tier email filtering** — Gmail search for candidates, then LLM classification to eliminate false positives
- **Claude Sonnet for all LLM calls** — Tool_use for structured output
- **Plain requests (no Playwright)** — Most retailers render prices in initial HTML for SEO
- **No ORM** — Raw parameterized SQL; 5 tables don't need SQLAlchemy
- **Idempotent** — gmail_message_id UNIQUE prevents duplicate purchases on re-runs

## Tech Stack
Python 3.11+, anthropic, google-api-python-client, APScheduler, requests, BeautifulSoup, SQLite

## Current Status
- MVP functional: Gmail OAuth, receipt parsing, retailer policy seeding all working
- Price checking pipeline complete but needs product purchases with URLs to exercise
- Contact-company mechanism deferred to a later phase
