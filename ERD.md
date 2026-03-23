# ERD — racunjajan.online

7 tables total. All primary keys are UUID. All timestamps are `TIMESTAMPTZ`.

## Relationships summary
- `accounts` → `account_niches` (one-to-many)
- `niches` → `account_niches` (one-to-many) — junction table, many-to-many between accounts and niches
- `niches` → `niche_adlibs` (one-to-many)
- `accounts` → `seen_products` (one-to-many) — dedup is per-account
- `accounts` → `post_queue` (one-to-many)
- `niches` → `post_queue` (one-to-many)
- `accounts` → `post_logs` (one-to-many)
- `niches` → `post_logs` (one-to-many)

---

## Diagram

```mermaid
erDiagram
  accounts {
    uuid id PK
    text name
    text threads_token
    text affiliate_id
    int post_per_day
    bool is_active
    timestamptz created_at
  }
  niches {
    uuid id PK
    text name
    text display_name
    text shopee_category_id
    text[] keywords
    timestamptz created_at
  }
  niche_adlibs {
    uuid id PK
    uuid niche_id FK
    text phrase
    text angle
    bool is_active
    timestamptz created_at
  }
  account_niches {
    uuid account_id FK
    uuid niche_id FK
    int priority
  }
  seen_products {
    uuid id PK
    uuid account_id FK
    text shopee_item_id
    timestamptz expires_at
    timestamptz created_at
  }
  post_queue {
    uuid id PK
    uuid account_id FK
    uuid niche_id FK
    text shopee_item_id
    jsonb product_data
    text affiliate_url
    float score
    text status
    text fetch_strategy
    timestamptz queued_at
    timestamptz posted_at
  }
  post_logs {
    uuid id PK
    uuid account_id FK
    uuid niche_id FK
    text threads_post_id
    text affiliate_url
    text status
    int retry_count
    text error_message
    timestamptz posted_at
  }

  accounts ||--o{ account_niches : "has"
  niches ||--o{ account_niches : "used by"
  niches ||--o{ niche_adlibs : "has"
  accounts ||--o{ seen_products : "tracks"
  accounts ||--o{ post_queue : "queues"
  niches ||--o{ post_queue : "categorizes"
  accounts ||--o{ post_logs : "logs"
  niches ||--o{ post_logs : "logs"
```

---

## Table notes

### accounts
Stores one row per Threads account. Phase 1 = 1 row.
`threads_token` and `affiliate_id` stored here (not env vars) to support multi-tenant.

### niches
Phase 1 seed data (4 niches):
| name | display_name | shopee_category_id |
|---|---|---|
| `rumah_tangga` | Barang Rumah Tangga | TBD — lookup from Shopee URL |
| `beauty` | Beauty & Skincare | TBD |
| `bayi_anak` | Perlengkapan Bayi & Anak | TBD |
| `makanan_minuman` | Makanan & Minuman | TBD |

### niche_adlibs
40 rows seeded on init (10 per niche). See PRD.md Section 3.3 for full seed data.
`angle` values: `benefit` | `pain_point` | `urgency` | `social_proof`
`is_active` = true by default. Toggle to false to disable a phrase without deleting.

### account_niches
Composite PK: `(account_id, niche_id)`.
`priority` = integer, ORDER BY ASC determines niche rotation order per account.
Phase 1 priority order: rumah_tangga=1, beauty=2, makanan_minuman=3, bayi_anak=4.

### seen_products
Dedup is **per-account** — same product can be posted by different accounts.
`expires_at` = `created_at + SEEN_EXPIRY_DAYS` (default 3 days).
After expiry, product is eligible to re-enter post_queue.

### post_queue
`status` values: `pending` | `posted` | `skipped` | `failed`
`fetch_strategy` values: `flash_sale` | `category` | `keyword` | `popular` | `expiry_override`
`product_data` JSONB shape:
```json
{
  "name": "string",
  "price": 150000,
  "original_price": 200000,
  "discount_pct": 25,
  "image_url": "https://...",
  "rating": 4.8,
  "sold_count": 1200,
  "description": "string",
  "shop_id": "string",
  "item_id": "string"
}
```

### post_logs
`status` values: `success` | `failed` | `retried`
`threads_post_id` = null if status is `failed`.
`retry_count` = 0 if first attempt succeeded.
Used by `daily_verification` job: `COUNT(*) WHERE DATE(posted_at) = TODAY AND status = 'success'`.
