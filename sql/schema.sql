-- racunjajan.online — Supabase schema
-- All PKs: UUID with gen_random_uuid()
-- All timestamps: TIMESTAMPTZ with now()

-- 1. accounts
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    threads_token TEXT NOT NULL,
    affiliate_id TEXT NOT NULL,
    post_per_day INTEGER NOT NULL DEFAULT 6,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. niches
CREATE TABLE IF NOT EXISTS niches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    shopee_category_id TEXT,
    keywords TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. niche_adlibs
CREATE TABLE IF NOT EXISTS niche_adlibs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    phrase TEXT NOT NULL,
    angle TEXT NOT NULL CHECK (angle IN ('benefit', 'pain_point', 'urgency', 'social_proof')),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. account_niches (junction table)
CREATE TABLE IF NOT EXISTS account_niches (
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, niche_id)
);

-- 5. seen_products
CREATE TABLE IF NOT EXISTS seen_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    shopee_item_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_seen_products_lookup
    ON seen_products (account_id, shopee_item_id, expires_at);

-- 6. post_queue
CREATE TABLE IF NOT EXISTS post_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    shopee_item_id TEXT NOT NULL,
    product_data JSONB NOT NULL DEFAULT '{}',
    affiliate_url TEXT NOT NULL,
    score FLOAT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'posted', 'skipped', 'failed')),
    fetch_strategy TEXT NOT NULL
        CHECK (fetch_strategy IN ('flash_sale', 'category', 'keyword', 'popular', 'expiry_override')),
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    posted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_post_queue_status
    ON post_queue (account_id, status, queued_at);

-- 7. post_logs
CREATE TABLE IF NOT EXISTS post_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    niche_id UUID NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
    threads_post_id TEXT,
    affiliate_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'retried')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_post_logs_daily
    ON post_logs (account_id, status, posted_at);
