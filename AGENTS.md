# AGENTS.md — racunjajan.online

## Project overview
Shopee affiliate auto-post pipeline. Discovers products via Playwright, generates
Bahasa Indonesia captions via Claude API (Haiku), constructs affiliate tracking links,
and publishes to Threads 6x/day across 4 niches.

Full spec: **PRD.md** | Full schema: **ERD.md**

---

## Stack
- **Language**: Python 3.11 (strictly — no 3.12+ syntax)
- **Browser automation**: Playwright + playwright-stealth
- **AI captions**: Claude API — model `claude-haiku-4-5-20251001`
- **Posting**: Threads API (Meta Graph API)
- **Database**: Supabase (PostgreSQL via supabase-py)
- **Scheduler**: APScheduler (in-process, no Celery)
- **Deploy**: Railway (Docker)
- **Logging**: Telegram Bot

---

## Folder structure
```
racunjajan/
├── src/
│   ├── fetcher.py      # Playwright product discovery (4-strategy fallback)
│   ├── filter.py       # 2-query dedup + quality filter against seen_products
│   ├── affiliate.py    # Affiliate link constructor (an_redir format)
│   ├── caption.py      # Claude caption generator with adlib selection + validation
│   ├── images.py       # Ephemeral image handler — download, upload bucket, delete
│   ├── poster.py       # Threads API poster (single + carousel)
│   ├── scheduler.py    # APScheduler — 4 jobs wired together
│   ├── db.py           # Supabase client + all query functions
│   └── notify.py       # Telegram notifications
├── scripts/
│   └── seed.py         # One-time script to seed niches + adlibs to Supabase
├── sql/
│   └── schema.sql      # Full Supabase schema (all 7 tables)
├── AGENTS.md
├── PRD.md
├── ERD.md
├── .env.example
├── requirements.txt
├── Dockerfile
└── README.md
```

---

## Coding conventions
- **Async-first**: all I/O uses `asyncio` — no blocking calls
- **Env vars**: loaded via `python-dotenv`, never hardcode credentials
- **DB queries**: all via `supabase-py` client in `db.py` — no raw SQL except Supabase RPC calls
- **Error handling**: mandatory try/except on all Playwright, Threads API, and Claude API calls
- **Logging**: every significant event logs to Telegram via `notify.py`
- **Type hints**: use on all function signatures
- **No print statements**: use Python `logging` module instead

---

## Commit rules
- **Commit after every task** before moving to the next one — no exceptions
- **Format**: conventional commits
  - `feat:` — new feature or module
  - `fix:` — bug fix
  - `chore:` — config, setup, seed data, tooling
- **Never batch multiple tasks into one commit**

---

## Constraints
- Do not install libraries outside the PRD-defined stack without asking first
- Do not refactor code that has already been committed unless explicitly asked
- Do not create files outside the defined folder structure above
- Do not assume — if there is ambiguity in a task, ask before writing code
- Python 3.11 only — do not use syntax or features from 3.12+
- All Supabase queries go through `db.py` — no direct Supabase calls in other modules
- Telegram notifications are mandatory for all post success, failure, retry, and daily summary events

---

## Scheduler jobs (4 total)
| Job | Schedule | Description |
|---|---|---|
| `main_pipeline` | 6x/day (configurable via POST_TIMES) | Fetch → filter → queue → caption → post |
| `retry_job` | 15 min after each main_pipeline slot | Re-attempt failed post_queue entries |
| `daily_verification` | 23:00 WIB | Check post count vs target, fill gap, send summary |
| `cleanup_job` | 03:00 WIB | Delete expired seen_products + old post_queue entries |

---

## 4 fetch strategies (in order)
1. `fetch_flash_sale` — `/api/v4/flash_sale/flash_sale_batch_get_items`
2. `fetch_by_category` — `/api/v4/search/search_items?category={id}`
3. `fetch_by_keyword` — `/api/v4/search/search_items?keyword={keyword}`
4. `fetch_popular_all` — `/api/v4/search/search_items?by=pop` (last resort, no filter)

Run strategies in sequence until `TARGET` unseen products collected. If all strategies
exhausted and result < `MIN_ITEMS_THRESHOLD`, trigger expiry override (1-day window)
and send Telegram alert.

---

## Dedup pattern (2-query — never per-item loop)
```python
# Step 1 — fetch from Shopee (1 Playwright call)
raw_items = await fetch_from_shopee(niche)

# Step 2 — single IN query for all item IDs
item_ids = [item['item_id'] for item in raw_items]
seen = await supabase.table('seen_products')\
    .select('shopee_item_id')\
    .eq('account_id', account_id)\
    .in_('shopee_item_id', item_ids)\
    .gt('expires_at', now)\
    .execute()
seen_ids = {row['shopee_item_id'] for row in seen.data}

# Step 3 — filter in Python
unseen = [i for i in raw_items if i['item_id'] not in seen_ids]
```

---

## Image handling (ephemeral bucket pattern)
Images live in Supabase Storage bucket `temp-images/` for the duration of one post only.

```
1. Cron triggers post slot
2. Check product_data.image_url
   - IF exists → download via httpx → upload to temp-images/{item_id}.jpg → get public URL
   - ELSE → skip, post text-only
3. post_to_threads(caption, image_url=bucket_url)
4. finally block → delete image from bucket (success OR failure)
```

**Critical rule**: always use `try/finally` in `images.py` — bucket deletion must run
regardless of whether the post succeeded or failed. No orphaned files.

```python
async def post_with_image(product, caption, token):
    bucket_path = None
    try:
        if product.get('image_url'):
            bucket_path = f"temp-images/{product['item_id']}.jpg"
            await upload_to_bucket(product['image_url'], bucket_path)
            bucket_url = get_public_url(bucket_path)
        post_id = await post_to_threads(caption, image_url=bucket_url, token=token)
        return post_id
    finally:
        if bucket_path:
            await delete_from_bucket(bucket_path)
```

All image logic lives in `src/images.py`. `poster.py` calls it — never handles storage directly.

---


```
https://s.shopee.co.id/an_redir?origin_link={ENCODED_URL}&affiliate_id={ID}&sub_id={NICHE_SLUG}
```
`sub_id` = niche slug (e.g. `rumah_tangga`, `beauty`) for per-niche click attribution.

---

## Caption generation rules
- Model: `claude-haiku-4-5-20251001`
- Claude selects max 2 adlibs from `niche_adlibs` that are supported by seller description
- Claude self-validates: removes any claim not present in seller's description
- If seller description is empty → skip adlibs, use product name + price + rating + sold only
- Output: Bahasa Indonesia, casual tone, format: hook + body (2-3 lines) + CTA + hashtags
- Return final caption only — no explanation

---

## How to start (Day 1)
1. Read **PRD.md Section 3** for full schema details
2. Read **ERD.md** for table relationships
3. Begin with task #1: `chore: init supabase schema` — create `sql/schema.sql` and run against Supabase
4. Then task #2: `chore: seed niches and adlibs` — create and run `scripts/seed.py`
5. Follow the full build order in **PRD.md Section 9**
6. Commit after each task using the commit message specified in the build plan
