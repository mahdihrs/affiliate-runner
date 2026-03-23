# PRD — racunjajan.online
## Shopee Affiliate Auto-Post Pipeline
**Version**: 1.0 — MVP  
**Author**: Haris  
**Target build**: 23–24 March 2026 (2 days)  
**Deploy target**: Railway → VPS (future migration)  
**Replaces**: AutoPost.id (OLX used car pipeline)  
**Status**: Pre-development

---

## 1. Overview

racunjajan.online is a fully automated Shopee affiliate content pipeline that discovers
products via Playwright-based scraping, generates engaging Bahasa Indonesia captions via
Claude API, constructs affiliate tracking links, and publishes content to Threads 6 times
per day across 4 niches.

The system replaces AutoPost.id with a multi-tenant, multi-niche architecture designed
to scale from a single Threads account to multiple niche-specific accounts without code changes.

### 1.1 Goals
- Fully automated Shopee affiliate posting pipeline with zero manual intervention
- Multi-tenant architecture supporting multiple Threads accounts and niches from day one
- Score-based product queue to prioritize high-performing, high-discount products
- Deduplication with 3-day expiration to prevent repeat posts while allowing re-promotion
- Retry mechanism + daily verification to ensure posting target is met every day
- Daily cleanup job to maintain database health
- Railway deployment with documented migration path to VPS

### 1.2 Non-goals (v1)
- Shopee Affiliate Open API integration (pending access — use an_redir workaround)
- Analytics dashboard or reporting UI
- Comment/engagement automation on Threads
- Multi-platform posting (Instagram, Twitter, etc.)
- Paid traffic or ad integration

---

## 2. System Architecture

### 2.1 Pipeline layers
| Layer | Component | Tech | Description |
|---|---|---|---|
| 1 | Product discovery | Playwright + playwright-stealth | Headless Chromium opens Shopee, intercepts api/v4 JSON responses |
| 2 | Filter & rank | Python | Apply quality thresholds; score products; insert to post_queue |
| 3 | Affiliate link gen | Python | Construct an_redir affiliate URL per product |
| 4 | Caption generation | Claude API (Haiku) | Generate Bahasa Indonesia caption, hook, hashtags |
| 5 | Post to Threads | Threads API (Meta Graph API) | Publish single post or carousel |
| 6 | State & scheduler | APScheduler + Supabase | Cron orchestration, dedup, queue management, cleanup |

### 2.2 Scheduler jobs
| Job | Schedule | Description |
|---|---|---|
| `main_pipeline` | 6x/day via POST_TIMES | Fetch → filter → queue → caption → post for all active accounts |
| `retry_job` | 15 min after each main_pipeline slot | Re-attempt post_queue entries with status = failed from last slot |
| `daily_verification` | 23:00 WIB | Count today's posts; fill gap if below post_per_day; send Telegram summary |
| `cleanup_job` | 03:00 WIB | Delete expired seen_products; purge post_queue entries older than QUEUE_RETENTION_DAYS |

### 2.3 Product scoring formula
```
score = (sold_count × 0.4) + (rating × 20 × 0.3) + (discount_pct × 0.2) + (recency × 0.1)
```
| Factor | Weight | Rationale |
|---|---|---|
| sold_count | 40% | Social proof — terbukti laku |
| rating × 20 | 30% | Product quality signal |
| discount_pct | 20% | Deal attractiveness |
| recency | 10% | Prefer newly discovered products |

### 2.4 Retry mechanism
3-layer retry strategy before permanently marking as failed:

| Layer | Trigger | Action | Max attempts |
|---|---|---|---|
| Slot retry | Threads API error during main_pipeline | Retry once after 15 min via retry_job | 1x per slot |
| Daily verification fill | Post count < post_per_day at 23:00 | Re-run pipeline for missing slots | post_per_day - actual_count |
| Manual | status = failed after daily verification | Visible in post_logs — operator decision | Operator |

Exponential backoff on individual HTTP calls: 1s → 2s → 4s (max 3 attempts).

```python
# Retry pseudocode
try:
    post_to_threads(payload)           # attempt 1
except ThreadsAPIError:
    await asyncio.sleep(15 * 60)       # wait 15 min
    post_to_threads(payload)           # attempt 2 (retry_job)
    if still fails → status = 'failed'

# daily_verification at 23:00
gap = post_per_day - count_successful_today(account_id)
if gap > 0:
    run_pipeline(account, slots=gap)
notify_telegram(f'{actual}/{target} posts today')
```

### 2.5 Affiliate link format
```
https://s.shopee.co.id/an_redir?origin_link={ENCODED_URL}&affiliate_id={AFFILIATE_ID}&sub_id={NICHE_SLUG}
```
`sub_id` = niche slug for per-niche click attribution in Shopee affiliate dashboard.
Once Shopee Open API access is granted, swap this layer for the official deeplink endpoint.

---

## 3. Database Schema

See **ERD.md** for full relationship diagram.

### 3.1 accounts
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| name | TEXT | e.g. racunjajan_main |
| threads_token | TEXT | Meta Graph API access token |
| affiliate_id | TEXT | Shopee affiliate ID (from utm_source=an_XXXX) |
| post_per_day | INTEGER | Target posts per day (default: 6) |
| is_active | BOOLEAN | Toggle account on/off |
| created_at | TIMESTAMPTZ | |

### 3.2 niches
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| name | TEXT | Slug: rumah_tangga, beauty, bayi_anak, makanan_minuman |
| display_name | TEXT | Human label: Barang Rumah Tangga, etc. |
| shopee_category_id | TEXT | From Shopee URL — manual seed for MVP |
| keywords | TEXT[] | Search keywords array for this niche |
| created_at | TIMESTAMPTZ | |

**Phase 1 niches**: barang rumah tangga (start here), beauty & skincare, perlengkapan bayi & anak, makanan & minuman.

### 3.3 niche_adlibs
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| niche_id | UUID FK → niches.id | |
| phrase | TEXT | e.g. "Cocok buat kamu yang..." |
| angle | TEXT | benefit / pain_point / urgency / social_proof |
| is_active | BOOLEAN | Toggle without deleting — default true |
| created_at | TIMESTAMPTZ | |

Stored in DB (not hardcoded) so phrases can be added/edited without redeployment.

#### Seed data — Barang Rumah Tangga
| Phrase | Angle |
|---|---|
| Cocok buat dapur kecil yang butuh alat multifungsi tanpa makan banyak tempat | benefit |
| Buat yang sering males beberes karena alatnya nggak praktis — ini lebih simpel | pain_point |
| Kalau kamu sering lupa matiin kompor, timer ini lumayan bantu | pain_point |
| Cocok digunakan untuk rumah yang sering lembab — bahan anti jamurnya lumayan tahan | benefit |
| Buat yang capek beli produk murahan yang cepat rusak, ini build quality-nya lebih solid | pain_point |
| Kalau kamu tinggal sendiri dan masak porsi kecil, ukurannya pas banget | benefit |
| Cocok buat yang mau dapur tetap rapi tanpa beli banyak organizer berbeda | benefit |
| Buat yang nggak mau ribet setup — langsung bisa dipakai out of the box | benefit |
| Kalau bau kulkas jadi masalah rutin di rumah kamu, ini worth dicoba dulu | pain_point |
| Cocok dipakai juga buat kos atau kontrakan, nggak makan tempat | benefit |

#### Seed data — Beauty & Skincare
| Phrase | Angle |
|---|---|
| Cocok buat kamu yang lagi nyari skincare harian tanpa banyak langkah | benefit |
| Buat kulit yang gampang breakout, formula ringan ini worth dicoba | pain_point |
| Kalau kamu tipe yang males ribet, satu produk ini bisa gantiin beberapa step | benefit |
| Pas buat yang baru mau mulai skincare tapi bingung mulai dari mana | pain_point |
| Buat yang sering skip sunscreen karena lengket — ini teksturnya beda | pain_point |
| Kalau kulit kamu cenderung kering di AC seharian, ini worth dicoba | pain_point |
| Cocok dipakai pagi sebelum makeup, nggak bikin pilling | benefit |
| Buat yang nggak mau keluar banyak tapi tetap mau rawat kulit | benefit |
| Formulanya cukup mild, cocok buat yang kulitnya sensitif | benefit |
| Kalau kamu sering lupa pakai skincare karena packagingnya ribet, yang ini simpel | pain_point |

#### Seed data — Perlengkapan Bayi & Anak
| Phrase | Angle |
|---|---|
| Cocok buat bayi yang aktif gerak, bahannya nggak bikin gerah | benefit |
| Buat mama yang nggak mau ribet tiap mau nyusuin di luar rumah | pain_point |
| Kalau si kecil susah tidur, produk ini lumayan bantu bikin tidurnya lebih nyenyak | pain_point |
| Cocok digunakan untuk anak yang lagi fase oral — materialnya food grade | benefit |
| Buat yang sering panik kalau barang bayi ketinggalan waktu pergi — ini compact | pain_point |
| Kalau kamu capek cuci botol berkali-kali, desain wide neck ini lebih gampang dibersihin | pain_point |
| Cocok buat anak yang lagi belajar jalan, solnya cukup grip di lantai rumah | benefit |
| Buat yang mau stimulasi motorik anak tanpa harus keluar rumah | benefit |
| Kalau kamu khawatir soal bahan kimia, produk ini sudah certified bebas BPA | pain_point |
| Cocok dipakai dari newborn sampai usia toddler, jadi nggak cepat ganti | benefit |

#### Seed data — Makanan & Minuman
| Phrase | Angle |
|---|---|
| Cocok buat yang sering skip sarapan karena nggak sempat masak | pain_point |
| Buat yang lagi cari camilan yang nggak bikin terlalu guilty | pain_point |
| Kalau kamu sering ngidam sesuatu manis tapi mau tetap kontrol porsi | pain_point |
| Cocok digunakan untuk bekal kerja atau sekolah, nggak ribet dibawa | benefit |
| Buat yang bosan minum air putih polos seharian di kantor | pain_point |
| Kalau kamu susah makan sayur, ini salah satu cara yang lebih gampang | pain_point |
| Cocok buat yang lagi coba pola makan lebih teratur tanpa harus masak dari nol | benefit |
| Buat yang sering kehabisan stok di rumah — lebih hemat beli bundling | benefit |
| Kalau kamu butuh camilan yang bisa disimpan lama di laci kantor | benefit |
| Cocok buat anak-anak yang susah makan, rasanya nggak aneh-aneh | benefit |

### 3.4 account_niches (junction table)
| Column | Type | Description |
|---|---|---|
| account_id | UUID FK → accounts.id | |
| niche_id | UUID FK → niches.id | |
| priority | INTEGER | Post order — lower = first (ORDER BY ASC) |

Composite PK: (account_id, niche_id). Many-to-many: one account → many niches, one niche → many accounts.

Phase 1 priority: rumah_tangga=1, beauty=2, makanan_minuman=3, bayi_anak=4.

### 3.5 seen_products
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| account_id | UUID FK → accounts.id | Dedup is per-account |
| shopee_item_id | TEXT | |
| expires_at | TIMESTAMPTZ | NOW() + 3 days — after this, product eligible to re-queue |
| created_at | TIMESTAMPTZ | |

Filter query: `WHERE account_id = $1 AND shopee_item_id IN ($2...) AND expires_at > NOW()`

### 3.6 post_queue
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| account_id | UUID FK → accounts.id | |
| niche_id | UUID FK → niches.id | |
| shopee_item_id | TEXT | |
| product_data | JSONB | name, price, image_url, rating, sold_count, discount_pct, description |
| affiliate_url | TEXT | Constructed an_redir link |
| score | FLOAT | Calculated at insert time |
| status | TEXT | pending / posted / skipped / failed |
| fetch_strategy | TEXT | flash_sale / category / keyword / popular / expiry_override |
| queued_at | TIMESTAMPTZ | |
| posted_at | TIMESTAMPTZ | Set when Threads post succeeds |

### 3.7 post_logs
| Column | Type | Description |
|---|---|---|
| id | UUID PK | |
| account_id | UUID FK → accounts.id | |
| niche_id | UUID FK → niches.id | |
| threads_post_id | TEXT | Returned by Threads API — null if failed |
| affiliate_url | TEXT | |
| status | TEXT | success / failed / retried |
| retry_count | INTEGER | 0 = first attempt succeeded |
| error_message | TEXT | Threads API error if status = failed |
| posted_at | TIMESTAMPTZ | |

---

## 4. Product Discovery (Playwright)

### 4.1 Strategy
Playwright launches headless Chromium with playwright-stealth. Instead of parsing HTML,
the scraper intercepts JSON responses from Shopee's own api/v4 calls — same technique
as AutoPost.id with Cloudflare Browser Rendering.

### 4.2 Endpoints intercepted
| Niche type | Endpoint | Key params |
|---|---|---|
| Flash sale | /api/v4/flash_sale/flash_sale_batch_get_items | limit=20, sort_soldout=true |
| Category browse | /api/v4/search/search_items | category={id}, by=pop |
| Keyword search | /api/v4/search/search_items | keyword={kw}, limit=20, by=pop, order=desc |
| Global popular (fallback) | /api/v4/search/search_items | by=pop, order=desc, no filter |

### 4.3 Multi-strategy fetching with fallback
Run strategies in sequence until TARGET unseen products collected:
```python
strategies = [fetch_flash_sale, fetch_by_category, fetch_by_keyword, fetch_popular_all]
collected = []
for strategy in strategies:
    if len(collected) >= TARGET: break
    items = await strategy(niche)
    unseen = await filter_unseen(items, account_id)
    collected.extend(unseen)
    if len(collected) < TARGET:
        notify_telegram(f'⚠️ {strategy.__name__} only returned {len(unseen)} items')
```
If all strategies exhausted and result < MIN_ITEMS_THRESHOLD (default: 3):
trigger expiry override (1-day window instead of 3-day) and send Telegram alert.

### 4.4 Efficient deduplication (2-query pattern)
Never loop per-item. Always batch:
```python
# Step 1 — fetch from Shopee (1 Playwright call)
raw_items = await fetch_from_shopee(niche)

# Step 2 — single IN query
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
# Total DB queries: 1
```

### 4.5 Anti-bot mitigations
- playwright-stealth patches navigator.webdriver, canvas fingerprint, browser API signatures
- Random wait 2–5 seconds after page load
- Fresh Chromium context per run (new cookies each time)
- Fetch interval minimum 4 hours
- User-Agent: latest stable Chrome on macOS

### 4.6 Quality filters (applied after dedup)
| Filter | Threshold |
|---|---|
| Minimum rating | ≥ 4.5 |
| Minimum sold count | ≥ 100 |
| Stock status | in_stock = true |

---

## 5. Content Generation

### 5.1 Claude API (Haiku)
Model: `claude-haiku-4-5-20251001` — cost efficient at 6x/day volume.
Single API call per product: adlib selection + caption writing + self-validation.

### 5.2 Adlib validation rules
- Only use adlib phrases supported by the seller's product description
- Self-review: remove any claim not present in seller's data
- If description is empty → skip all adlibs, use name + price + rating + sold only
- If no adlibs match → proceed without adlibs, plain caption
- Seller description in English → output caption in Bahasa Indonesia regardless
- Seller makes exaggerated claims → only use verifiable facts (specs, rating, sold count)

### 5.3 Prompt structure
```
SYSTEM:
Kamu copywriter konten Shopee untuk akun affiliate racunjajan.online.
Tugasmu menulis caption yang jujur, engaging, dan tidak melebih-lebihkan.

ATURAN KETAT:
- Hanya gunakan klaim yang didukung oleh deskripsi produk seller
- Jika adlib tidak relevan dengan produk, jangan gunakan
- Jangan tambahkan manfaat yang tidak disebutkan seller
- Kalau deskripsi produk minim, fokus ke harga dan spesifikasi saja
- Tulis dalam Bahasa Indonesia yang casual dan relatable
- Gunakan emoji secukupnya
- Format: hook (1 baris) + body (2-3 baris) + CTA + hashtags

USER:
Nama: {name}
Harga: Rp{price} (diskon {discount_pct}%)
Rating: {rating}/5 ({sold_count} terjual)
Deskripsi seller: {description}
Kategori: {niche_display_name}

Adlibs tersedia untuk niche ini (pilih maksimal 2 yang relevan):
{adlibs_list}

Tugas:
1. Pilih adlibs yang benar-benar didukung deskripsi seller di atas
2. Tulis caption dengan format yang ditentukan
3. Review — hapus klaim yang tidak ada di deskripsi seller
4. Return final caption saja, tanpa penjelasan tambahan

Link affiliate: {affiliate_url}
```

### 5.4 Post formats
| Format | When used | Structure |
|---|---|---|
| Single post | 1 product | Caption + 1 image |
| Carousel | Flash sale bundle or 3–5 related products | 1 caption + up to 10 image cards |

### 5.5 Image handling (ephemeral bucket pattern)
Images are downloaded temporarily, used for posting, then deleted immediately after success.
This keeps storage lean — no persistent image accumulation.

**Flow:**
```
1. Cron triggers main_pipeline
2. For each product to post:
   a. Check product_data.image_url
   b. IF image_url exists:
      - Download image via httpx
      - Upload to Supabase Storage bucket (temp-images/)
      - Get public URL from bucket
   c. ELSE: skip image, post text-only
3. Run post_to_threads(caption, bucket_image_url)
4. On post success → delete image from bucket immediately
5. On post failure → delete image from bucket, mark status = failed
```

**Pseudocode:**
```python
async def post_with_image(product, caption, token):
    bucket_url = None
    bucket_path = None

    if product.get('image_url'):
        bucket_path = f"temp-images/{product['item_id']}.jpg"
        await download_and_upload(product['image_url'], bucket_path)
        bucket_url = supabase.storage.from_('temp-images').get_public_url(bucket_path)

    try:
        post_id = await post_to_threads(caption, image_url=bucket_url, token=token)
        return post_id
    finally:
        if bucket_path:
            await supabase.storage.from_('temp-images').remove([bucket_path])
```

`finally` block guarantees image deletion regardless of success or failure — no orphaned files.

---

## 6. Posting Schedule

### 6.1 Niche rotation (6x/day)
| Slot | Time (WIB) | Niche |
|---|---|---|
| 1 | 07:00 | Barang rumah tangga |
| 2 | 10:00 | Beauty & skincare |
| 3 | 12:00 | Makanan & minuman |
| 4 | 15:00 | Perlengkapan bayi & anak |
| 5 | 18:00 | Barang rumah tangga |
| 6 | 21:00 | Highest scored product across all niches |

Slot timing configurable via POST_TIMES env var.

### 6.2 Threads API rate limits
- Hard limit: 250 posts per 24h per account — 6x/day is well within limits
- Carousel counts as 1 post
- Minimum gap between posts: 30 minutes (enforced by schedule)

---

## 7. Tech Stack

### 7.1 Core
| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.11 | Required for Supabase/Playwright compatibility |
| Browser automation | Playwright + playwright-stealth | Latest stable |
| AI captions | Claude API | claude-haiku-4-5-20251001 |
| Posting | Threads API | Meta Graph API — reuse AutoPost.id token |
| Database | Supabase (PostgreSQL) | Existing account from AutoPost.id |
| Image storage | Supabase Storage | Ephemeral bucket `temp-images/` — deleted after each post |
| Scheduler | APScheduler | In-process, no Celery needed |
| Deploy | Railway | Single service, Docker container |
| Logging | Telegram Bot | Same as AutoPost.id |

### 7.2 Python dependencies
| Library | Purpose |
|---|---|
| playwright | Headless browser + network response interception |
| playwright-stealth | Anti-bot fingerprint bypass |
| anthropic | Claude API client |
| supabase-py | Supabase client |
| apscheduler | Cron scheduler |
| pillow | Image processing for carousel |
| httpx | Async HTTP client for Threads API |
| python-dotenv | Env var management |

---

## 8. Environment Variables

| Variable | Description |
|---|---|
| ANTHROPIC_API_KEY | Claude API key |
| SUPABASE_URL | Supabase project URL |
| SUPABASE_KEY | Supabase service role key |
| SUPABASE_BUCKET_NAME | Supabase Storage bucket name for ephemeral images — default: `temp-images` |
| TELEGRAM_BOT_TOKEN | Telegram logging bot token |
| TELEGRAM_CHAT_ID | Telegram chat ID for logs |
| POST_TIMES | Comma-separated WIB times — default: `07:00,10:00,12:00,15:00,18:00,21:00` |
| RETRY_DELAY_MINUTES | Minutes before retry_job runs after failed slot — default: `15` |
| VERIFICATION_TIME | Time daily_verification runs. Fills gap same day, not next day — default: `23:00` |
| CLEANUP_TIME | Time cleanup_job runs — default: `03:00` |
| SEEN_EXPIRY_DAYS | Days before seen_product expires and can be re-queued — default: `3` |
| QUEUE_RETENTION_DAYS | Days to keep posted/failed post_queue entries before cleanup deletes them — default: `7` |
| MAX_RETRY_ATTEMPTS | Max HTTP retry attempts per Threads API call (exponential backoff 1s→2s→4s) — default: `3` |
| MIN_ITEMS_THRESHOLD | Minimum unseen products after all 4 strategies. If below, trigger expiry override — default: `3` |

`threads_token` and `affiliate_id` are stored in the `accounts` table in Supabase, not as env vars — enables multi-tenant without redeployment.

---

## 9. Build Plan (2 Days)

Commit after every task using the specified commit message. No batching.

### Day 1 — 23 March 2026 (Foundation)
| # | Task | Commit message | Output |
|---|---|---|---|
| 1 | Supabase schema setup | `chore: init supabase schema with all tables and FK constraints` | sql/schema.sql created and run — 7 tables live |
| 2 | Seed niches + adlibs | `chore: seed niches and adlibs for 4 Phase 1 niches` | scripts/seed.py run — 4 niches + 40 adlibs in Supabase |
| 3 | Playwright product fetcher | `feat: add playwright product fetcher with 4-strategy fallback` | src/fetcher.py — fetch_products(niche) returns product list |
| 4 | Filter & dedup logic | `feat: add 2-query dedup filter against seen_products` | src/filter.py — filter_unseen() working against Supabase |
| 5 | Affiliate link constructor | `feat: add affiliate link constructor with an_redir format` | src/affiliate.py — make_affiliate_link() tested |
| 6 | post_queue insertion with scoring | `feat: add product scoring and post_queue insertion` | Products scored and inserted to queue correctly |
| 7 | Claude caption generator | `feat: add caption generator with adlib selection and self-validation` | src/caption.py — generate_caption() returns validated caption |

### Day 2 — 24 March 2026 (Integration & Deploy)
| # | Task | Commit message | Output |
|---|---|---|---|
| 1 | Threads API poster + image handling | `feat: add threads poster with ephemeral image bucket pattern` | src/poster.py — download → upload bucket → post → delete working for single + carousel |
| 2 | Retry mechanism | `feat: add retry job with exponential backoff and failed status handling` | retry_job + 3-layer retry working |
| 3 | Daily verification job | `feat: add daily verification job with gap fill and telegram report` | Counts posts, fills gap, sends summary |
| 4 | APScheduler setup | `feat: wire all 4 scheduler jobs with configurable times` | src/scheduler.py — all jobs scheduled |
| 5 | Telegram logging | `feat: add telegram notifications for all pipeline events` | src/notify.py — all alerts firing |
| 6 | End-to-end test | `chore: run full e2e test including simulated failure and retry` | Pipeline verified end-to-end |
| 7 | Dockerfile + Railway deploy | `chore: add dockerfile and deploy to railway` | Service live on Railway |
| 8 | First live post verification | `chore: verify first live post on threads with affiliate link` | Post live on Threads, link tracked |

---

## 10. Deployment

### 10.1 Railway (Day 2 target)
- Single Railway service, Python 3.11 Docker container
- Base image: `mcr.microsoft.com/playwright/python` (includes Chromium)
- All secrets injected via Railway environment variables UI
- APScheduler runs in-process — no separate worker
- Restart policy: on-failure, 3 retries

### 10.2 VPS migration path (future)
Because the app is fully containerized, migration requires only:
1. Provision VPS (DigitalOcean / Hetzner recommended)
2. Install Docker
3. `docker pull your-registry/racunjajan && docker run` with env vars
4. Set up systemd service or docker-compose for auto-restart
5. Point racunjajan.online domain to VPS IP

Zero code changes required.

---

## 11. Future Improvements (Post-MVP)

| Feature | Priority | Notes |
|---|---|---|
| Shopee Affiliate Open API | High | Replace an_redir once access approved |
| threads_permalink in post_logs | High | Store Threads post URL for analytics |
| Performance-based niche rotation | Medium | Auto-adjust slot frequency based on engagement |
| Similar products by category + price | Medium | Post related products in same session |
| Auto category seed from Shopee API | Medium | Replace manual shopee_category_id |
| Multiple Threads accounts | Medium | Already supported by schema — add row to accounts |
| Seasonal awareness in captions | Medium | Inject Ramadan / Harbolnas context to prompt |
| Web dashboard | Low | View post_logs, queue, per-niche performance |
| Caption A/B testing | Low | Test hook styles per niche, track via Threads Insights |
| Embedding-based similar products | Low | pgvector in Supabase for semantic product matching |
| VPS migration | Low | After Railway is stable |

---

## 12. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Shopee updates anti-bot, blocks Playwright | Medium | playwright-stealth actively maintained; reduce to 6h interval if blocked |
| Threads API token expires | Low | Store in Supabase with refresh logic; Telegram alert on 401 |
| Railway cold start delays post timing | Low | APScheduler handles drift; retry_job + daily_verification as safety net |
| Affiliate link format changes | Low | Monitor one link weekly; format parameterized in config |
| Claude API cost spike | Low | Haiku ~$0.25/MTok; 6 posts/day ≈ cents per day |
| Shopee returns same product list repeatedly | Medium | 4-strategy fallback + expiry override handles thin inventory |
