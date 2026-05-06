"""Main pipeline orchestration: caption → post (fetch happens via Telegram admin bot)."""

import logging
import os
from typing import Any

from src import db, bot_storage
from src.caption import generate_caption
from src.images import post_with_image
from src.notify import notify_success, notify_failure, notify_alert
from src.poster import post_to_threads

logger = logging.getLogger(__name__)


async def _repost_from_history(account: dict[str, Any]) -> bool:
    """When no pending products, repost a previously posted item (text-only, no image).

    Returns True if repost succeeded.
    """
    account_id = account["id"]
    token = account["threads_token"]

    repostable = db.get_repostable_entries(account_id, limit=1)
    if not repostable:
        logger.info("No repostable entries found for %s", account.get("name", "unknown"))
        await notify_alert(
            f"Slot skipped — no pending items and no repostable history for "
            f"<b>{account.get('name', 'unknown')}</b>.\nSubmit products via the Telegram bot."
        )
        return False

    entry = repostable[0]
    product_data = entry["product_data"]
    affiliate_url = entry["affiliate_url"]

    # Use the approved caption, or regenerate one
    caption = (product_data.get("approved_caption") or "").strip()
    if not caption:
        niche = db.get_niche_by_id(entry["niche_id"]) or {}
        adlibs = db.get_adlibs(entry["niche_id"])
        try:
            caption = await generate_caption(
                product=product_data,
                niche=niche,
                adlibs=adlibs,
                affiliate_url=affiliate_url,
            )
        except Exception as e:
            logger.error(f"Repost caption generation failed: {e}")
            return False

    try:
        # Post text-only (no image since it was already deleted)
        post_id = await post_to_threads(
            caption=caption,
            image_url=None,
            token=token,
        )

        # Log as reposted
        from datetime import datetime, timezone
        db.insert_post_log({
            "account_id": account_id,
            "niche_id": entry["niche_id"],
            "threads_post_id": post_id,
            "affiliate_url": affiliate_url,
            "status": "reposted",
            "retry_count": 0,
        })

        logger.info(f"Reposted (text-only): {product_data.get('name', 'unknown')}")
        await notify_success(product_data, post_id)
        return True

    except Exception as e:
        logger.error(f"Repost failed for {product_data.get('name', 'unknown')}: {e}")
        await notify_failure(product_data, f"Repost failed: {e}")
        return False


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
    pending = db.get_pending_from_queue(account_id)

    if not pending:
        logger.info("No pending items — attempting repost from history")
        return await _repost_from_history(account)

    entry = pending[0]
    product_data = entry["product_data"]
    affiliate_url = entry["affiliate_url"]
    # Use the entry's actual niche_id for adlibs (may differ from slot niche)
    entry_niche_id = entry["niche_id"]

    # Get adlibs for caption generation
    niche = db.get_niche_by_id(entry_niche_id) or niche
    adlibs = db.get_adlibs(entry_niche_id)

    try:
        # Use pre-approved caption from Telegram flow when available.
        caption = (product_data.get("approved_caption") or "").strip()
        if not caption:
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

        # Bot-submitted products: drop the persisted image from storage.
        storage_path = product_data.get("image_storage_path")
        if storage_path:
            try:
                bot_storage.delete_bot_image(storage_path)
            except Exception as cleanup_err:
                logger.warning(f"Bot image cleanup failed: {cleanup_err}")

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
