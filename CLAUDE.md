# Rover

Automated price adjustment agent. Monitors Gmail for purchase receipts, tracks prices via web scraping, and detects price drops within retailer refund windows.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Requires `.env` with `ANTHROPIC_API_KEY` and a `config.yaml` (see `config.example.yaml`). Gmail OAuth needs `credentials.json` from Google Cloud Console.

## Running

```bash
rover          # entry point: rover.main:main
```

## Architecture

Single Python package (`rover/`) with no tests yet. SQLite database (WAL mode), no ORM — raw parameterized SQL.

**Pipeline:** Gmail fetch -> LLM classification -> LLM receipt parsing -> price scraping -> LLM price extraction -> savings detection

Key modules:
- `main.py` — entry point, wires everything together
- `gmail.py` — Gmail API OAuth2 + email fetching
- `parser.py` — LLM receipt classification + structured extraction (tool_use)
- `scraper.py` — HTTP fetcher (requests + BeautifulSoup), anti-bot measures
- `price_checker.py` — LLM price extraction, savings detection
- `policies.py` — Retailer refund window lookup (DB -> YAML -> scrape -> 14d default)
- `scheduler.py` — APScheduler jobs (email scan 30min, price check 6hr)
- `db.py` — SQLite with 5 tables: purchases, price_checks, savings, metadata, retailers
- `config.py` — YAML config + .env loading

## Code style

- Python 3.11+, type hints on function signatures
- No ORM, no abstractions beyond what's needed
- All LLM calls use Claude API `tool_use` with forced tool choice for structured output
- Logging via stdlib `logging` module
- No tests currently — be careful with changes

## Key files not to commit

- `.env`, `config.yaml`, `credentials.json`, `token.json`, `rover.db*`
