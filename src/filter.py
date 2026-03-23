"""2-query dedup filter, quality filtering, and product scoring."""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from src import db
from src.notify import notify_fetch_fallback, notify_alert

logger = logging.getLogger(__name__)

MIN_ITEMS_THRESHOLD = int(os.getenv("MIN_ITEMS_THRESHOLD", "3"))
SEEN_EXPIRY_DAYS = int(os.getenv("SEEN_EXPIRY_DAYS", "3"))


async def filter_unseen(
    items: list[dict[str, Any]], account_id: str
) -> list[dict[str, Any]]:
    """2-query batch dedup: single IN query to seen_products, filter in Python."""
    if not items:
        return []

    item_ids = [item["item_id"] for item in items]
    seen_ids = db.get_seen_ids(account_id, item_ids)

    unseen = [i for i in items if i["item_id"] not in seen_ids]
    logger.info(f"Dedup: {len(items)} total → {len(unseen)} unseen")
    return unseen


def score_product(product: dict[str, Any]) -> float:
    """Calculate product score using weighted formula.

    score = (sold_count × 0.4) + (rating × 20 × 0.3) + (discount_pct × 0.2) + (recency × 0.1)
    recency = 1.0 for newly discovered products.
    """
    sold_count = min(product.get("sold_count", 0), 10000)  # cap for normalization
    rating = product.get("rating", 0)
    discount_pct = product.get("discount_pct", 0)
    recency = 1.0  # always 1.0 at discovery time

    score = (
        (sold_count * 0.4)
        + (rating * 20 * 0.3)
        + (discount_pct * 0.2)
        + (recency * 0.1)
    )
    return round(score, 2)


async def filter_and_score(
    items: list[dict[str, Any]],
    account_id: str,
    strategy_name: str,
) -> list[dict[str, Any]]:
    """Full filter pipeline: dedup → score. Returns scored items sorted by score DESC."""
    unseen = await filter_unseen(items, account_id)

    if len(unseen) < MIN_ITEMS_THRESHOLD:
        await notify_fetch_fallback(strategy_name, len(unseen))

    # Score each product
    for item in unseen:
        item["score"] = score_product(item)

    # Sort by score descending
    unseen.sort(key=lambda x: x["score"], reverse=True)
    return unseen


async def handle_low_inventory(
    items: list[dict[str, Any]], account_id: str
) -> list[dict[str, Any]]:
    """If items below threshold after all strategies, use 1-day expiry override."""
    if len(items) >= MIN_ITEMS_THRESHOLD:
        return items

    await notify_alert(
        f"Low inventory ({len(items)} items). "
        f"Triggering 1-day expiry override for account {account_id}"
    )

    # Re-query with shorter expiry window (items seen 1-3 days ago become eligible)
    # This is handled by the caller fetching with relaxed dedup
    return items
