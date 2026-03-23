# racunjajan.online

Fully automated Shopee affiliate content pipeline. Discovers products via Playwright, generates Bahasa Indonesia captions via Claude API, and publishes to Threads 6x/day across 4 niches.

## How it works

1. **Fetch** — Playwright scrapes Shopee using 4 strategies (flash sale → category → keyword → popular)
2. **Filter** — Deduplicate against seen products, apply quality thresholds (rating ≥ 4.5, sold ≥ 100)
3. **Score** — Rank by weighted formula: sold_count (40%), rating (30%), discount (20%), recency (10%)
4. **Caption** — Claude Haiku generates Bahasa Indonesia caption with adlib selection and self-validation
5. **Post** — Publish to Threads via Meta Graph API with affiliate tracking link
6. **Retry** — Failed posts are retried 15 min later; daily verification fills any remaining gaps

## Tech stack

- **Python 3.11** — Playwright, Claude API, Threads API, Supabase, APScheduler
- **Supabase** — PostgreSQL database + ephemeral image storage
- **Railway** — Docker deployment (single service, no separate worker)
- **Telegram** — Logging and alerts

## Project structure

```
├── src/
│   ├── fetcher.py      # Playwright product discovery (4-strategy fallback)
│   ├── filter.py       # 2-query dedup + quality filter
│   ├── affiliate.py    # Affiliate link constructor (an_redir format)
│   ├── caption.py      # Claude caption generator with adlib validation
│   ├── images.py       # Ephemeral image handler (download → bucket → delete)
│   ├── poster.py       # Threads API poster (single + carousel)
│   ├── pipeline.py     # Main orchestration (fetch → filter → queue → post)
│   ├── scheduler.py    # APScheduler — 4 jobs wired together
│   ├── db.py           # Supabase client + all query functions
│   └── notify.py       # Telegram notifications
├── scripts/
│   └── seed.py         # One-time seed for niches + adlibs
├── sql/
│   └── schema.sql      # Full Supabase schema (7 tables)
├── main.py             # Entry point
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Setup

### 1. Clone and install

```bash
git clone https://github.com/mahdihrs/affiliate-runner.git
cd affiliate-runner
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in all values in .env
```

Required variables:

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service role key |
| `SUPABASE_BUCKET_NAME` | Storage bucket name (default: `temp-images`) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for logging |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `POST_TIMES` | Comma-separated WIB times (default: `07:00,10:00,12:00,15:00,18:00,21:00`) |

`threads_token` and `affiliate_id` are stored per-account in the `accounts` table — not env vars.

### 3. Initialize database

Run `sql/schema.sql` against your Supabase project, then seed:

```bash
python scripts/seed.py
```

### 4. Add your account

Insert a row into the `accounts` table in Supabase:

```sql
INSERT INTO accounts (name, threads_token, affiliate_id, post_per_day)
VALUES ('racunjajan_main', '<your_threads_token>', '<your_affiliate_id>', 6);
```

Then link niches in `account_niches`:

```sql
INSERT INTO account_niches (account_id, niche_id, priority)
SELECT '<account_id>', id, ROW_NUMBER() OVER (ORDER BY name)
FROM niches;
```

### 5. Run

```bash
python main.py
```

## Deploy to Railway

1. Connect the GitHub repo to a new Railway service
2. Set all environment variables in Railway UI
3. Railway will build the Dockerfile and start `python main.py`

The Dockerfile uses `mcr.microsoft.com/playwright/python` which includes Chromium — no additional browser install needed in production.

## Scheduler jobs

| Job | Schedule | Description |
|---|---|---|
| `main_pipeline` | 6x/day (configurable) | Fetch → filter → queue → caption → post |
| `retry_job` | 15 min after each slot | Re-attempt failed posts |
| `daily_verification` | 23:00 WIB | Fill posting gap, send Telegram summary |
| `cleanup_job` | 03:00 WIB | Delete expired seen_products + old queue entries |

## Niches (Phase 1)

| Slug | Display name |
|---|---|
| `rumah_tangga` | Barang Rumah Tangga |
| `beauty` | Beauty & Skincare |
| `bayi_anak` | Perlengkapan Bayi & Anak |
| `makanan_minuman` | Makanan & Minuman |

## Spec docs

- [`PRD.md`](PRD.md) — Full product requirements
- [`ERD.md`](ERD.md) — Database schema and relationships
- [`AGENTS.md`](AGENTS.md) — Developer implementation guide
