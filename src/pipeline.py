"""Main pipeline orchestration: fetch → filter → score → queue → caption → post."""

import logging
import os
from typing import Any

from src import db
from src.fetcher import fetch_products
from src.filter import filter_and_score, handle_low_inventory
from src.affiliate import make_affiliate_link
from src.caption import generate_caption
from src.images import post_with_image
from src.notify import notify_success, notify_failure, notify_alert

logger = logging.getLogger(__name__)

SEEN_EXPIRY_DAYS = int(os.getenv("SEEN_EXPIRY_DAYS", "3"))


async def discover_and_queue(
    account: dict[str, Any], niche: dict[str, Any]
) -> list[dict[str, Any]]:
    """Fetch → filter → score → construct affiliate URL → insert to post_queue.

    Returns the queued entries.
    """
    account_id = account["id"]
    niche_id = niche["id"]
    niche_name = niche["name"]
    affiliate_id = account["affiliate_id"]

    # Step 1: Fetch products from Shopee
    raw_items, last_strategy = await fetch_products(niche)
    logger.info(f"Fetched {len(raw_items)} items for niche {niche_name}")

    if not raw_items:
        await notify_alert(f"No products fetched for {niche_name}")
        return []

    # Step 2: Filter unseen + score
    scored_items = await filter_and_score(raw_items, account_id, last_strategy)

    # Step 3: Handle low inventory
    scored_items = await handle_low_inventory(scored_items, account_id)

    if not scored_items:
        await notify_alert(f"No unseen products after filtering for {niche_name}")
        return []

    # Step 4: Build queue entries with affiliate links
    queue_entries = []
    for item in scored_items[:1]:  # Take top-scored item for this slot
        affiliate_url = make_affiliate_link(
            item_id=item["item_id"],
            shop_id=item["shop_id"],
            affiliate_id=affiliate_id,
            niche_slug=niche_name,
        )

        entry = {
            "account_id": account_id,
            "niche_id": niche_id,
            "shopee_item_id": item["item_id"],
            "product_data": {
                "name": item["name"],
                "price": item["price"],
                "original_price": item.get("original_price", 0),
                "discount_pct": item["discount_pct"],
                "image_url": item["image_url"],
                "rating": item["rating"],
                "sold_count": item["sold_count"],
                "description": item.get("description", ""),
                "shop_id": item["shop_id"],
                "item_id": item["item_id"],
            },
            "affiliate_url": affiliate_url,
            "score": item["score"],
            "status": "pending",
            "fetch_strategy": last_strategy,
        }
        queue_entries.append(entry)

    # Step 5: Insert to post_queue
    db.insert_to_queue(queue_entries)

    # Step 6: Mark items as seen
    db.insert_seen(account_id, scored_items[:1], SEEN_EXPIRY_DAYS)

    logger.info(f"Queued {len(queue_entries)} items for {niche_name}")
    return queue_entries


async def process_and_post(
    account: dict[str, Any], niche: dict[str, Any]
) -> bool:
    """Full pipeline for a single slot: discover → queue → caption → post.

    Returns True if post succeeded.
    """
    account_id = account["id"]
    niche_id = niche["id"]
    token = account["threads_token"]

    # Check for existing pending items (e.g. from manual seed)
    # First try any niche, then niche-specific
    pending = db.get_pending_from_queue(account_id)

    if not pending:
        # No pending items at all — discover and queue new ones
        queued = await discover_and_queue(account, niche)
        if not queued:
            return False
        pending = db.get_pending_from_queue(account_id, niche_id)
        if not pending:
            return False

    entry = pending[0]
    product_data = entry["product_data"]
    affiliate_url = entry["affiliate_url"]
    # Use the entry's actual niche_id for adlibs (may differ from slot niche)
    entry_niche_id = entry["niche_id"]

    # Get adlibs for caption generation
    niche = db.get_niche_by_id(entry_niche_id) or niche
    adlibs = db.get_adlibs(entry_niche_id)

    try:
        # Generate caption
        caption = await generate_caption(
            product=product_data,
            niche=niche,
            adlibs=adlibs,
            affiliate_url=affiliate_url,
        )

        # Post to Threads with image
        post_id = await post_with_image(
            product=product_data,
            caption=caption,
            token=token,
        )

        # Update queue status
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        db.update_queue_status(entry["id"], "posted", posted_at=now)

        # Log success
        db.insert_post_log({
            "account_id": account_id,
            "niche_id": entry_niche_id,
            "threads_post_id": post_id,
            "affiliate_url": affiliate_url,
            "status": "success",
            "retry_count": 0,
        })

        await notify_success(product_data, post_id)
        return True

    except Exception as e:
        logger.error(f"Pipeline failed for {product_data.get('name', 'unknown')}: {e}")
        db.update_queue_status(entry["id"], "failed")
        db.insert_post_log({
            "account_id": account_id,
            "niche_id": entry_niche_id,
            "threads_post_id": None,
            "affiliate_url": affiliate_url,
            "status": "failed",
            "retry_count": 0,
            "error_message": str(e),
        })
        await notify_failure(product_data, str(e))
        return False
