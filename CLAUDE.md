# Rover

Automated price adjustment agent. Monitors Gmail for purchase receipts, tracks prices via web scraping, and detects price drops within retailer refund windows.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium  # for scraping bot-protected sites
```

Requires `.env` with `ANTHROPIC_API_KEY` and a `config.yaml` (see `config.example.yaml`). Gmail OAuth needs `credentials.json` from Google Cloud Console.

## Running

```bash
rover                        # production: scheduler runs email scan (30min) + price check (6hr)
python -m rover.dev_server   # dev UI at http://localhost:5001 for step-by-step testing
```

## Architecture

Single Python package (`rover/`) with no tests yet. SQLite database (WAL mode), no ORM — raw parameterized SQL.

**Pipeline:** Gmail fetch -> regex pre-filter -> LLM receipt parsing (tool_use) -> DuckDuckGo URL discovery -> price scraping (requests + Playwright fallback) -> LLM price extraction -> savings detection

Key modules:
- `main.py` — entry point, wires everything together
- `gmail.py` — Gmail API OAuth2 + email fetching
- `parser.py` — regex pre-filter (`is_likely_receipt`) + LLM structured extraction (`tool_use` for multi-field output)
- `scraper.py` — HTTP fetcher with Playwright fallback, JSON-LD extraction, content-type validation
- `price_checker.py` — DuckDuckGo URL discovery, LLM price extraction (plain prompt, no tool_use), savings detection
- `policies.py` — Retailer refund window lookup (DB -> YAML seed -> LLM extraction from scraped policy pages -> default)
- `scheduler.py` — APScheduler jobs (email scan 30min, price check 6hr)
- `db.py` — SQLite with 5 tables: purchases, price_checks, savings, metadata, retailers
- `dev_server.py` — Flask dev UI for interactive pipeline testing
- `config.py` — YAML config + .env loading

## LLM usage

- **Receipt parsing** (`parser.py`): `tool_use` with forced tool choice — extracts structured multi-field data (items array, retailer, date, order_number, email_type). This is the only place tool_use is justified.
- **Price extraction** (`price_checker.py`): Plain prompt, responds with just a number or "null". `temperature=0`.
- **URL selection** (`price_checker.py`): Plain prompt, responds with just a URL or "null". `temperature=0`.
- **Policy extraction** (`policies.py`): Plain prompt, responds with number of days or "null". `temperature=0`.

## Code style

- Python 3.11+, type hints on function signatures
- No ORM, no abstractions beyond what's needed
- Logging via stdlib `logging` module
- No tests currently — be careful with changes

## Key files not to commit

- `.env`, `config.yaml`, `credentials.json`, `token.json`, `rover.db*`

## Known limitations

- "On Running" brand name is too ambiguous for reliable URL/policy discovery (short word "On" confuses search)
- Generic item names like "KNITS", "GRAPHICS" can't find product URLs
- DuckDuckGo rate limits during burst testing (production scheduler spreads requests over hours)
- YouTube/digital purchases (c.gle shortened URLs) can't be price-tracked
