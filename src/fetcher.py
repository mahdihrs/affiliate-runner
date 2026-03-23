"""Playwright product fetcher with 4-strategy fallback."""

import asyncio
import json
import logging
import random
from typing import Any

from playwright.async_api import async_playwright, Page, Response
from playwright_stealth import stealth_async

logger = logging.getLogger(__name__)

SHOPEE_BASE = "https://shopee.co.id"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Quality thresholds
MIN_RATING = 4.5
MIN_SOLD = 100


def _parse_items_from_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize product items from Shopee API response."""
    items = []
    raw_items = data.get("items") or data.get("data", {}).get("items") or []

    for raw in raw_items:
        item_info = raw.get("item_basic") or raw
        item_id = str(item_info.get("itemid", ""))
        if not item_id:
            continue

        price_raw = item_info.get("price", 0)
        price_before_discount = item_info.get("price_before_discount", 0)
        # Shopee prices are in units of 100000
        price = price_raw / 100000 if price_raw > 100000 else price_raw
        original_price = (
            price_before_discount / 100000
            if price_before_discount > 100000
            else price_before_discount
        )
        discount_pct = 0
        if original_price > 0:
            discount_pct = round((1 - price / original_price) * 100)

        rating = round(item_info.get("item_rating", {}).get("rating_star", 0), 1)
        sold_count = item_info.get("sold", 0) or item_info.get("historical_sold", 0)
        stock = item_info.get("stock", 1)

        image_id = item_info.get("image", "")
        image_url = f"https://cf.shopee.co.id/file/{image_id}" if image_id else ""

        shop_id = str(item_info.get("shopid", ""))

        items.append({
            "item_id": item_id,
            "shop_id": shop_id,
            "name": item_info.get("name", ""),
            "price": price,
            "original_price": original_price,
            "discount_pct": discount_pct,
            "image_url": image_url,
            "rating": rating,
            "sold_count": sold_count,
            "stock": stock,
            "description": "",
        })

    return items


def apply_quality_filters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter products by quality thresholds."""
    return [
        item
        for item in items
        if item["rating"] >= MIN_RATING
        and item["sold_count"] >= MIN_SOLD
        and item["stock"] > 0
    ]


async def _intercept_shopee_api(
    page: Page, url_path: str, navigate_url: str
) -> list[dict[str, Any]]:
    """Navigate to a Shopee URL and intercept the API response."""
    captured: list[dict[str, Any]] = []

    async def handle_response(response: Response) -> None:
        if url_path in response.url and response.status == 200:
            try:
                data = await response.json()
                parsed = _parse_items_from_response(data)
                captured.extend(parsed)
            except Exception as e:
                logger.warning(f"Failed to parse response from {response.url}: {e}")

    page.on("response", handle_response)

    try:
        await page.goto(navigate_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(random.uniform(2, 5))
    except Exception as e:
        logger.warning(f"Navigation timeout or error for {navigate_url}: {e}")

    return captured


async def fetch_flash_sale(niche: dict[str, Any]) -> list[dict[str, Any]]:
    """Strategy 1: Fetch flash sale items."""
    logger.info("Fetching flash sale items")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await stealth_async(page)

        items = await _intercept_shopee_api(
            page,
            "flash_sale",
            f"{SHOPEE_BASE}/flash_sale",
        )

        await browser.close()
    return apply_quality_filters(items)


async def fetch_by_category(niche: dict[str, Any]) -> list[dict[str, Any]]:
    """Strategy 2: Fetch by category ID."""
    category_id = niche.get("shopee_category_id", "")
    if not category_id:
        logger.warning(f"No category ID for niche {niche['name']}, skipping")
        return []

    logger.info(f"Fetching by category {category_id}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await stealth_async(page)

        items = await _intercept_shopee_api(
            page,
            "search_items",
            f"{SHOPEE_BASE}/search?category={category_id}&sortBy=pop",
        )

        await browser.close()
    return apply_quality_filters(items)


async def fetch_by_keyword(niche: dict[str, Any]) -> list[dict[str, Any]]:
    """Strategy 3: Fetch by keyword search."""
    keywords = niche.get("keywords", [])
    if not keywords:
        logger.warning(f"No keywords for niche {niche['name']}, skipping")
        return []

    keyword = random.choice(keywords)
    logger.info(f"Fetching by keyword: {keyword}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await stealth_async(page)

        items = await _intercept_shopee_api(
            page,
            "search_items",
            f"{SHOPEE_BASE}/search?keyword={keyword}&sortBy=pop",
        )

        await browser.close()
    return apply_quality_filters(items)


async def fetch_popular_all() -> list[dict[str, Any]]:
    """Strategy 4: Fetch globally popular (last resort, no filter)."""
    logger.info("Fetching popular items (global fallback)")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await stealth_async(page)

        items = await _intercept_shopee_api(
            page,
            "search_items",
            f"{SHOPEE_BASE}/search?sortBy=pop",
        )

        await browser.close()
    return apply_quality_filters(items)


async def fetch_products(
    niche: dict[str, Any], target: int = 10
) -> tuple[list[dict[str, Any]], str]:
    """Run all 4 strategies in sequence until target unseen products collected.

    Returns (items, last_strategy_used).
    """
    strategies = [
        ("flash_sale", lambda: fetch_flash_sale(niche)),
        ("category", lambda: fetch_by_category(niche)),
        ("keyword", lambda: fetch_by_keyword(niche)),
        ("popular", lambda: fetch_popular_all()),
    ]

    collected: list[dict[str, Any]] = []
    last_strategy = "flash_sale"

    for strategy_name, strategy_fn in strategies:
        if len(collected) >= target:
            break

        last_strategy = strategy_name
        try:
            items = await strategy_fn()
            # Deduplicate within this fetch run
            existing_ids = {item["item_id"] for item in collected}
            new_items = [i for i in items if i["item_id"] not in existing_ids]
            collected.extend(new_items)
            logger.info(
                f"Strategy {strategy_name}: got {len(new_items)} new items "
                f"(total: {len(collected)})"
            )
        except Exception as e:
            logger.error(f"Strategy {strategy_name} failed: {e}")

    return collected[:target], last_strategy
