<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="RoverWhiteLogo.png">
    <source media="(prefers-color-scheme: light)" srcset="RoverBlackLogo.png">
    <img src="RoverBlackLogo.png" alt="Rover" width="200">
  </picture>
</p>

<p align="center">
  Rover watches your inbox, tracks prices on things you've bought, and alerts you when prices drop within the retailer's refund window so you can claim a price adjustment. Built with Python, Claude API, Gmail API, and a Next.js dashboard.
</p>

<p align="center">
  <strong>Live at <a href="https://tryrover.app">tryrover.app</a></strong>
</p>

## How It Works

Rover runs a continuous background pipeline:

1. **Email Scan** (every 30 min) — Connects to Gmail via OAuth2, searches `category:purchases` for order confirmations and receipts.
2. **Regex Pre-Filter** — Checks for price signals (`$XX.XX`) and order keywords. Filters out shipping updates, refunds, subscriptions, and food delivery.
3. **Receipt Parsing** — Claude (`tool_use`) extracts structured data: items array (name, price), retailer, purchase date, order number. Idempotent on `gmail_message_id`.
4. **URL Discovery** — Searches DuckDuckGo by retailer name + product name, then Claude picks the best product page URL from the retailer's own domain.
5. **Price Checking** (every 6 hr) — For purchases still within refund window: scrapes the product page (requests first, Playwright fallback for bot-protected sites), extracts JSON-LD structured data, and Claude extracts the current price.
6. **Savings Detection** — If current price < price paid, records the drop.
7. **Claim Emails** (every 24 hr) — Sends price adjustment request emails to retailer support on the user's behalf via Gmail API.
8. **Notifications** — Dashboard notifications + consolidated HTML email alerts with savings amounts and days remaining in refund window.

### Refund Policy Lookup (Three-Tier)

1. **Known retailers** — `retailers.yaml` with 22+ major retailers and their refund windows (Amazon 30d, Target 90d, Apple 14d, REI 365d, etc.)
2. **Web scrape** — For unknown retailers, searches for their return policy page, scrapes it, and Claude extracts the window in days.
3. **Default** — Falls back to 14 days.

### LLM Usage

| Task | Method | Why |
|------|--------|-----|
| Receipt parsing | `tool_use` with forced tool choice | Multi-field nested structured output (items, retailer, date, order number) |
| Price extraction | Plain prompt, `temperature=0` | Single number output, no structure needed |
| URL selection | Plain prompt, `temperature=0` | Single URL output |
| Policy extraction | Plain prompt, `temperature=0` | Single number output |

### Scraping

- User-agent rotation (12 real browser UAs)
- Per-domain rate limiting (min 10s between requests)
- Random 2-5s delays, exponential backoff on 429/503
- CAPTCHA detection, content-type validation
- **Playwright headless browser fallback** for sites with strong bot detection (e.g., Abercrombie, Hollister)
- **JSON-LD structured data extraction** from `<script type="application/ld+json">` tags

## Architecture

```
rover/
├── api.py               # FastAPI backend (20+ endpoints, Supabase JWT auth)
├── main.py              # CLI entry point for standalone mode
├── config.py            # YAML config + .env loading
├── db.py                # PostgreSQL (multi-tenant, raw parameterized SQL)
├── deps.py              # FastAPI dependency injection (auth, DB)
├── gmail.py             # Gmail OAuth2 — per-user web redirect flow
├── token_store.py       # Fernet-encrypted Gmail token storage
├── parser.py            # Regex pre-filter + Claude structured extraction
├── scraper.py           # HTTP fetcher + Playwright fallback + JSON-LD
├── price_checker.py     # DuckDuckGo discovery + Claude price extraction
├── policies.py          # Three-tier refund policy lookup
├── notifier.py          # Email notifications (consolidated HTML)
├── claimer.py           # Automated price adjustment claim emails
├── scheduler.py         # APScheduler (standalone/dev mode)
├── celery_app.py        # Celery app config (Redis broker)
├── tasks.py             # Celery tasks (email scan, price check, claims)
├── dev_server.py        # Flask dev UI at localhost:5001
└── logger.py            # Console + file logging

migrations/
└── 001_initial_schema.sql   # PostgreSQL schema with RLS

scripts/
└── seed_mock_data.py        # Test data generator

tests/                       # 161 tests across 9 files
retailers.yaml               # Known retailer database (22+ retailers)
```

### Database (PostgreSQL / Supabase)

Multi-tenant with Row-Level Security. 8 tables:

| Table | Purpose |
|-------|---------|
| `users` | Supabase-linked accounts |
| `user_gmail_tokens` | Encrypted OAuth tokens per user |
| `purchases` | Parsed receipt items (UNIQUE on `user_id` + `gmail_message_id`) |
| `price_checks` | Historical scrape results per purchase |
| `savings` | Detected price drops (status: new / notified / claimed) |
| `notifications` | In-app notification feed per user |
| `retailers` | Known retailer info (seeded from YAML, enriched by scraping) |
| `metadata` | User-scoped key-value store |

### API Endpoints

FastAPI backend with Supabase JWT authentication (ES256).

- **Auth**: `POST /api/auth/check-email`, Gmail OAuth connect/callback/status/disconnect
- **Onboarding**: status + complete
- **Dashboard**: `GET /api/dashboard/summary` (aggregate stats)
- **Purchases**: list, create (manual add), detail with price history
- **Savings**: list all detected price drops
- **Notifications**: list, mark read, mark all read
- **Activity**: recent activity feed (price checks, savings, notifications)
- **Profile**: get, update name, delete account (cascades all data)
- **Health**: `GET /api/health`

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| LLM | Claude (Anthropic API) — Sonnet for all tasks |
| Email | Gmail API (OAuth2), Resend (transactional) |
| Database | PostgreSQL (Supabase) with RLS |
| Task Queue | Celery + Redis (Beat scheduler) |
| Scraping | requests, BeautifulSoup, lxml, Playwright |
| Frontend | Next.js (separate repo), deployed on Vercel |
| Auth | Supabase Auth (JWT) |

## Deployment

Rover runs as three services + managed infrastructure:

### Backend — Railway

The FastAPI server + Celery workers deploy on Railway via `nixpacks.toml`.

**Processes** (defined in `Procfile`):
- `web` — `uvicorn rover.api:app` (API server)
- `worker` — `celery -A rover.celery_app worker` (background task execution)
- `beat` — `celery -A rover.celery_app beat` (periodic task scheduler)

**Celery Beat Schedule**:
- Email scan dispatch: every 30 minutes
- Price check dispatch: every 6 hours
- Claims dispatch: every 24 hours

Each dispatch task fans out per-user tasks with auto-retry and rate limiting.

### Frontend — Vercel

The Next.js dashboard (`rover-web` repo) deploys on Vercel at `rover-web.vercel.app`. Communicates with the Railway backend API. Landing page at `tryrover.app`.

### Database — Supabase

PostgreSQL with connection pooling (session pooler). Row-Level Security policies enforce tenant isolation. Schema managed via `migrations/001_initial_schema.sql`.

### Redis — Railway Add-on

Message broker and result backend for Celery.

### Environment Variables

```
# Core
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=postgresql://...

# Celery
REDIS_URL=redis://...

# Auth
SUPABASE_URL=https://...
SUPABASE_JWT_SECRET=...

# Gmail OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GMAIL_OAUTH_REDIRECT_URI=...
GMAIL_TOKEN_ENCRYPTION_KEY=...

# Email & Frontend
RESEND_API_KEY=...
DASHBOARD_URL=https://...
```

### Docker

A `Dockerfile` is also provided for containerized deployment (Python 3.11-slim base).

## Local Development

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium

# Configure
cp config.example.yaml config.yaml
cp .env.example .env
# Fill in API keys, database URL, etc.

# Run (choose one)
rover                        # Standalone CLI: APScheduler runs all jobs
python -m rover.dev_server   # Dev UI at http://localhost:5001 for step-by-step testing
uvicorn rover.api:app        # API server only (for frontend development)
```

Gmail OAuth requires `credentials.json` from Google Cloud Console with `gmail.readonly` and `gmail.send` scopes.

### Running Tests

```bash
pytest tests/ -v
```

## Known Limitations

- **Ambiguous brand names** — Some brand names can be too short/common for reliable search results
- **Generic item names** — Items like "T-SHIRT" can't find product URLs
- **DuckDuckGo rate limits** — Burst testing hits rate limits; production scheduler spreads requests over hours to avoid this
- **Digital purchases** — YouTube/Google Play (c.gle shortened URLs) can't be price-tracked