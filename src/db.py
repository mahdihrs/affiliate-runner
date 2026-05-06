"""Supabase client and all query functions."""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    """Get or create the Supabase client singleton."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", os.getenv("SUPABASE_KEY", ""))
        _client = create_client(url, key)
    return _client


# --- Accounts ---

def get_active_accounts() -> list[dict[str, Any]]:
    """Fetch all active accounts."""
    result = get_client().table("accounts").select("*").eq("is_active", True).execute()
    return result.data


# --- Niches ---

def get_account_niches(account_id: str) -> list[dict[str, Any]]:
    """Fetch niches for an account, ordered by priority ASC."""
    result = (
        get_client()
        .table("account_niches")
        .select("*, niches(*)")
        .eq("account_id", account_id)
        .order("priority", desc=False)
        .execute()
    )
    return result.data


def get_niche_by_name(name: str) -> dict[str, Any] | None:
    """Fetch a single niche by slug name."""
    result = get_client().table("niches").select("*").eq("name", name).limit(1).execute()
    return result.data[0] if result.data else None


def get_niche_by_id(niche_id: str) -> dict[str, Any] | None:
    """Fetch a single niche by ID."""
    result = get_client().table("niches").select("*").eq("id", niche_id).limit(1).execute()
    return result.data[0] if result.data else None


# --- Adlibs ---

def get_adlibs(niche_id: str) -> list[dict[str, Any]]:
    """Fetch active adlibs for a niche."""
    result = (
        get_client()
        .table("niche_adlibs")
        .select("*")
        .eq("niche_id", niche_id)
        .eq("is_active", True)
        .execute()
    )
    return result.data


# --- Seen Products ---

def get_seen_ids(account_id: str, item_ids: list[str]) -> set[str]:
    """Batch check which item IDs have been seen (not expired) for an account."""
    if not item_ids:
        return set()
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_client()
        .table("seen_products")
        .select("shopee_item_id")
        .eq("account_id", account_id)
        .in_("shopee_item_id", item_ids)
        .gt("expires_at", now)
        .execute()
    )
    return {row["shopee_item_id"] for row in result.data}


def insert_seen(account_id: str, items: list[dict[str, Any]], expiry_days: int = 3) -> None:
    """Insert items into seen_products with expiry."""
    expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat()
    rows = [
        {
            "account_id": account_id,
            "shopee_item_id": item["item_id"],
            "expires_at": expires_at,
        }
        for item in items
    ]
    if rows:
        get_client().table("seen_products").insert(rows).execute()


# --- Post Queue ---

def insert_to_queue(entries: list[dict[str, Any]]) -> None:
    """Batch insert products into post_queue."""
    if entries:
        get_client().table("post_queue").insert(entries).execute()


def get_pending_from_queue(account_id: str, niche_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch pending queue entries for an account, optionally filtered by niche.

    When niche_id is None, returns pending items across all niches.
    """
    query = (
        get_client()
        .table("post_queue")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "pending")
        .order("score", desc=True)
    )
    if niche_id is not None:
        query = query.eq("niche_id", niche_id)
    return query.execute().data


def get_failed_from_queue(account_id: str) -> list[dict[str, Any]]:
    """Fetch failed queue entries for retry."""
    result = (
        get_client()
        .table("post_queue")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "failed")
        .execute()
    )
    return result.data


def update_queue_status(
    queue_id: str, status: str, posted_at: str | None = None
) -> None:
    """Update the status of a post_queue entry."""
    data: dict[str, Any] = {"status": status}
    if posted_at:
        data["posted_at"] = posted_at
    get_client().table("post_queue").update(data).eq("id", queue_id).execute()


# --- Post Logs ---

def insert_post_log(log: dict[str, Any]) -> None:
    """Insert a post log entry."""
    get_client().table("post_logs").insert(log).execute()


def count_today_posts(account_id: str) -> int:
    """Count successful posts (including reposts) for an account today."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    result = (
        get_client()
        .table("post_logs")
        .select("id", count="exact")
        .eq("account_id", account_id)
        .in_("status", ["success", "reposted"])
        .gte("posted_at", today_start)
        .execute()
    )
    return result.count or 0


# --- Cleanup ---

def cleanup_expired_seen() -> int:
    """Delete seen_products where expires_at < now. Returns count deleted."""
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_client()
        .table("seen_products")
        .delete()
        .lt("expires_at", now)
        .execute()
    )
    return len(result.data)


def cleanup_old_queue(retention_days: int = 7) -> int:
    """Delete post_queue entries older than retention_days with terminal status."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    result = (
        get_client()
        .table("post_queue")
        .delete()
        .in_("status", ["posted", "failed", "skipped"])
        .lt("queued_at", cutoff)
        .execute()
    )
    return len(result.data)


# --- Bot-submitted queue entries ---

def insert_bot_queue_entries(
    product_data: dict[str, Any],
    affiliate_url: str,
    niche_id: str,
    score: float = 50.0,
) -> int:
    """Insert a bot-submitted product into post_queue for every active account.

    `product_data` is the JSONB shape — must already include `image_storage_path`
    so the success hook can delete the bot-uploaded image after posting.

    Returns the number of rows inserted.
    """
    accounts = get_active_accounts()
    if not accounts:
        return 0
    item_id = product_data.get("item_id") or f"manual_{datetime.now(timezone.utc).timestamp():.0f}"
    product_data.setdefault("item_id", item_id)
    rows = [
        {
            "account_id": account["id"],
            "niche_id": niche_id,
            "shopee_item_id": item_id,
            "product_data": product_data,
            "affiliate_url": affiliate_url,
            "score": score,
            "status": "pending",
            "fetch_strategy": "keyword",
        }
        for account in accounts
    ]
    get_client().table("post_queue").insert(rows).execute()
    return len(rows)


def get_repostable_entries(account_id: str, limit: int = 1) -> list[dict[str, Any]]:
    """Fetch previously posted queue entries that can be reposted (text-only).

    Returns oldest posted entries first (round-robin reposting).
    Excludes entries already reposted today.
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    # Get today's already-reposted affiliate URLs to avoid duplicates
    today_reposts = (
        get_client()
        .table("post_logs")
        .select("affiliate_url")
        .eq("account_id", account_id)
        .eq("status", "reposted")
        .gte("posted_at", today_start)
        .execute()
    )
    reposted_urls = {r["affiliate_url"] for r in today_reposts.data}

    # Get posted queue entries ordered by oldest first
    result = (
        get_client()
        .table("post_queue")
        .select("*")
        .eq("account_id", account_id)
        .eq("status", "posted")
        .order("posted_at", desc=False)
        .limit(50)
        .execute()
    )

    # Filter out already-reposted-today
    candidates = [
        r for r in result.data
        if r.get("affiliate_url") and r["affiliate_url"] not in reposted_urls
    ]
    return candidates[:limit]


def get_active_bot_image_paths() -> set[str]:
    """Return all `image_storage_path` values referenced by non-terminal queue rows.

    Used by orphan cleanup — any object in the bot-uploads bucket whose path is
    NOT in this set (and is older than the retention cutoff) can be deleted.
    """
    result = (
        get_client()
        .table("post_queue")
        .select("product_data")
        .in_("status", ["pending", "failed"])
        .execute()
    )
    paths = set()
    for row in result.data:
        pd = row.get("product_data") or {}
        p = pd.get("image_storage_path")
        if p:
            paths.add(p)
    return paths
