-- Rover SaaS schema for Supabase Postgres
-- Run this migration against your Supabase database

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- Users & Auth
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    supabase_auth_id UUID UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_gmail_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    encrypted_access_token BYTEA,
    encrypted_refresh_token BYTEA,
    token_expiry TIMESTAMPTZ,
    gmail_email TEXT,
    connected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id)
);

-- ============================================================
-- Core tables (multi-tenant)
-- ============================================================

CREATE TABLE IF NOT EXISTS purchases (
    id SERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gmail_message_id TEXT,
    item_name TEXT,
    price_paid DOUBLE PRECISION,
    product_url TEXT,
    retailer TEXT,
    purchase_date TEXT,
    currency TEXT DEFAULT 'USD',
    order_number TEXT,
    raw_email_snippet TEXT,
    url_search_attempted INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, gmail_message_id)
);

CREATE TABLE IF NOT EXISTS price_checks (
    id SERIAL PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    current_price DOUBLE PRECISION,
    checked_at TIMESTAMPTZ DEFAULT now(),
    status TEXT CHECK(status IN ('success', 'scrape_failed', 'parse_failed')),
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS savings (
    id SERIAL PRIMARY KEY,
    purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    price_check_id INTEGER NOT NULL REFERENCES price_checks(id) ON DELETE CASCADE,
    original_price DOUBLE PRECISION,
    dropped_price DOUBLE PRECISION,
    savings_amount DOUBLE PRECISION,
    detected_at TIMESTAMPTZ DEFAULT now(),
    status TEXT DEFAULT 'new' CHECK(status IN ('new', 'notified', 'claimed'))
);

CREATE TABLE IF NOT EXISTS metadata (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, key)
);

-- ============================================================
-- Shared tables (global, not per-user)
-- ============================================================

CREATE TABLE IF NOT EXISTS retailers (
    id SERIAL PRIMARY KEY,
    name TEXT,
    domain TEXT UNIQUE,
    refund_window_days INTEGER,
    support_email TEXT,
    support_url TEXT,
    policy_url TEXT,
    source TEXT DEFAULT 'manual' CHECK(source IN ('manual', 'scraped')),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_purchases_user_id ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_user_retailer ON purchases(user_id, retailer);
CREATE INDEX IF NOT EXISTS idx_price_checks_purchase_id ON price_checks(purchase_id);
CREATE INDEX IF NOT EXISTS idx_savings_purchase_id ON savings(purchase_id);
CREATE INDEX IF NOT EXISTS idx_savings_status ON savings(status);
CREATE INDEX IF NOT EXISTS idx_metadata_user_id ON metadata(user_id);
CREATE INDEX IF NOT EXISTS idx_user_gmail_tokens_user_id ON user_gmail_tokens(user_id);

-- ============================================================
-- Row-Level Security (Supabase)
-- ============================================================

ALTER TABLE purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_gmail_tokens ENABLE ROW LEVEL SECURITY;

-- Users can only see their own purchases
CREATE POLICY purchases_user_isolation ON purchases
    FOR ALL USING (user_id = auth.uid()::uuid);

-- Users can only see their own metadata
CREATE POLICY metadata_user_isolation ON metadata
    FOR ALL USING (user_id = auth.uid()::uuid);

-- Users can only see their own Gmail tokens
CREATE POLICY gmail_tokens_user_isolation ON user_gmail_tokens
    FOR ALL USING (user_id = auth.uid()::uuid);

-- price_checks and savings are protected transitively through purchases FK
-- (Supabase doesn't enforce FK-based RLS, but our API always filters by user_id through purchases)
