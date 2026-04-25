# racunjajan.online

Fully automated Shopee affiliate content pipeline. Discovers products via Playwright, generates Bahasa Indonesia captions via DeepSeek API (with Claude fallback), processes images via Claude Haiku, and publishes to Threads 6x/day across 4 niches.

## How it works

1. **Fetch** — Playwright scrapes Shopee using 4 strategies (flash sale → category → keyword → popular)
2. **Filter** — Deduplicate against seen products, apply quality thresholds (rating ≥ 4.5, sold ≥ 100)
3. **Score** — Rank by weighted formula: sold_count (40%), rating (30%), discount (20%), recency (10%)
4. **Caption** — DeepSeek Chat generates Bahasa Indonesia caption with adlib selection and self-validation (Claude fallback available)
5. **Post** — Publish to Threads via Meta Graph API with affiliate tracking link
6. **Retry** — Failed posts are retried 15 min later; daily verification fills any remaining gaps

## Tech stack

- **Python 3.11** — Playwright, DeepSeek API, Claude API, Threads API, Supabase, APScheduler
- **Supabase** — PostgreSQL database + ephemeral image storage
- **Railway** — Docker deployment (single service, no separate worker)
- **Telegram** — Logging and alerts

## Project structure

```
├── src/
│   ├── fetcher.py        # Playwright product discovery (4-strategy fallback)
│   ├── filter.py         # 2-query dedup + quality filter
│   ├── affiliate.py      # Affiliate link constructor (an_redir format)
│   ├── caption.py        # DeepSeek caption generator with Claude fallback
│   ├── images.py         # Ephemeral post-time image handler (temp-images bucket)
│   ├── bot_storage.py    # Persistent bot-uploads bucket helpers
│   ├── claude_vision.py  # Claude screenshot → product JSON + crop bbox
│   ├── poster.py         # Threads API poster (single + carousel)
│   ├── pipeline.py       # Main orchestration (fetch → filter → queue → post)
│   ├── scheduler.py      # APScheduler — 4 jobs wired together
│   ├── db.py             # Supabase client + all query functions
│   └── notify.py         # Telegram notifications
├── scripts/
│   ├── seed.py           # One-time seed for niches + adlibs
│   └── seed_queue.py     # Manual CLI product seeder
├── sql/
│   └── schema.sql        # Full Supabase schema (7 tables)
├── main.py               # Scheduler entry point
├── admin_bot.py          # Telegram admin bot entry point
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
| `ANTHROPIC_API_KEY` | Claude API key (image processing and optional captions) |
| `DEEPSEEK_API_KEY` | DeepSeek API key (captions and optional vision) |
| `USE_DEEPSEEK_CAPTION` | Set to `true` to use DeepSeek for captions (default: `false`) |
| `CLAUDE_VISION_MODEL` | Claude model for vision (default: `claude-haiku-4-5-20251001`) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service role key |
| `SUPABASE_BUCKET_NAME` | Ephemeral post-time bucket (default: `temp-images`) |
| `SUPABASE_BOT_UPLOADS_BUCKET` | Persistent bucket for bot-submitted images (default: `bot-uploads`) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (shared by notifier + admin bot) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID for outbound notifications |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated user IDs allowed to submit products via the admin bot |
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

Deploy as **two services from the same repo** — they share the Dockerfile and env vars but run different entry points.

**Service 1: scheduler** (existing)
- Start command: `python main.py`
- Needs: everything in `.env.example`

**Service 2: admin bot**
- Start command: `python admin_bot.py`
- Needs (minimum): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`, `ANTHROPIC_API_KEY`, all `SUPABASE_*` vars
- Before setting `TELEGRAM_ALLOWED_USER_IDS`: deploy once with it empty, send `/whoami` to the bot, copy your user_id, then set the env var.

The Dockerfile uses `mcr.microsoft.com/playwright/python` which includes Chromium — no additional browser install needed in production.

### Supabase Storage setup

Create two public buckets:
- `temp-images` — ephemeral, used by the scheduler per post
- `bot-uploads` — persistent, used by the admin bot until the product is posted

Make both **public** (so Threads can fetch images for the post container).

## Admin bot (manual product submission)

Run via `python admin_bot.py` (or the second Railway service).

1. Open your Telegram bot and send `/start` to see the command menu.
2. Run `/submit` — the bot asks for a screenshot.
3. Send a screenshot of a Shopee product. Claude extracts fields and the bot shows you what it parsed. If anything mandatory is missing (`name`, `price`, `description`), reply with `field: value` — one per line.
4. Paste the affiliate link when asked.
5. Pick a niche via the inline keyboard.
6. Confirm → the product is inserted into `post_queue` for every active account and the scheduler picks it up on the next slot.

Use `/cancel` at any time to abort the current submission.

The cropped screenshot is uploaded to the `bot-uploads` bucket. It's deleted automatically after a successful post, or by the nightly cleanup job if the entry is abandoned.

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
