"""Telegram notifications for all pipeline events."""

import os
import logging
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


async def send_telegram(message: str) -> None:
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification")
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                TELEGRAM_API_URL,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


async def notify_success(product: dict[str, Any], post_id: str) -> None:
    """Notify on successful post."""
    name = product.get("name", "Unknown")
    price = product.get("price", 0)
    await send_telegram(
        f"<b>Posted</b>\n"
        f"{name}\n"
        f"Rp{price:,.0f}\n"
        f"Threads ID: {post_id}"
    )


async def notify_failure(product: dict[str, Any], error: str) -> None:
    """Notify on failed post."""
    name = product.get("name", "Unknown")
    await send_telegram(
        f"<b>Failed to post</b>\n"
        f"{name}\n"
        f"Error: {error}"
    )


async def notify_retry(product: dict[str, Any], attempt: int) -> None:
    """Notify on retry attempt."""
    name = product.get("name", "Unknown")
    await send_telegram(
        f"<b>Retrying</b> (attempt {attempt})\n"
        f"{name}"
    )


async def notify_daily_summary(
    account_name: str, actual: int, target: int
) -> None:
    """Send daily posting summary."""
    status = "on target" if actual >= target else "BELOW TARGET"
    await send_telegram(
        f"<b>Daily Summary</b> — {account_name}\n"
        f"Posts: {actual}/{target} ({status})"
    )


async def notify_alert(message: str) -> None:
    """Send a generic alert."""
    await send_telegram(f"<b>Alert</b>\n{message}")


async def notify_cleanup(seen_deleted: int, queue_deleted: int) -> None:
    """Notify cleanup results."""
    await send_telegram(
        f"<b>Cleanup</b>\n"
        f"Expired seen_products removed: {seen_deleted}\n"
        f"Old queue entries removed: {queue_deleted}"
    )


async def notify_fetch_fallback(strategy: str, count: int) -> None:
    """Notify when a fetch strategy returns fewer items than expected."""
    await send_telegram(
        f"<b>Fetch Warning</b>\n"
        f"Strategy <code>{strategy}</code> only returned {count} unseen items"
    )
