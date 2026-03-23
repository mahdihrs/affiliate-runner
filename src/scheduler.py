"""APScheduler setup — 4 jobs wired with configurable times."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from src import db
from src.pipeline import process_and_post
from src.poster import ThreadsAPIError
from src.notify import (
    notify_alert,
    notify_daily_summary,
    notify_cleanup,
    notify_failure,
    notify_retry,
)

load_dotenv()

logger = logging.getLogger(__name__)

POST_TIMES = os.getenv("POST_TIMES", "07:00,10:00,12:00,15:00,18:00,21:00")
RETRY_DELAY_MINUTES = int(os.getenv("RETRY_DELAY_MINUTES", "15"))
VERIFICATION_TIME = os.getenv("VERIFICATION_TIME", "23:00")
CLEANUP_TIME = os.getenv("CLEANUP_TIME", "03:00")
QUEUE_RETENTION_DAYS = int(os.getenv("QUEUE_RETENTION_DAYS", "7"))

# WIB = UTC+7
WIB_OFFSET = 7

# Niche rotation per slot (PRD Section 6.1)
NICHE_ROTATION = [
    "rumah_tangga",   # Slot 1
    "beauty",         # Slot 2
    "makanan_minuman",# Slot 3
    "bayi_anak",      # Slot 4
    "rumah_tangga",   # Slot 5
    None,             # Slot 6 — highest scored across all niches
]


def _parse_post_times() -> list[tuple[int, int]]:
    """Parse POST_TIMES env var into list of (hour, minute) tuples in UTC."""
    times = []
    for t in POST_TIMES.split(","):
        t = t.strip()
        parts = t.split(":")
        hour_wib = int(parts[0])
        minute = int(parts[1])
        hour_utc = (hour_wib - WIB_OFFSET) % 24
        times.append((hour_utc, minute))
    return times


async def _main_pipeline_slot(slot_index: int) -> None:
    """Run the main pipeline for a specific slot."""
    logger.info(f"Running main_pipeline slot {slot_index + 1}")

    accounts = db.get_active_accounts()
    if not accounts:
        logger.warning("No active accounts found")
        return

    for account in accounts:
        niche_slug = NICHE_ROTATION[slot_index] if slot_index < len(NICHE_ROTATION) else None

        if niche_slug is None:
            # Slot 6: highest scored across all niches — pick from queue
            niches = db.get_account_niches(account["id"])
            if niches:
                # Pick niche with highest scored pending item
                best_niche = niches[0].get("niches", niches[0])
                niche = best_niche
            else:
                logger.warning(f"No niches for account {account['name']}")
                continue
        else:
            niche = db.get_niche_by_name(niche_slug)
            if not niche:
                logger.warning(f"Niche {niche_slug} not found")
                continue

        try:
            await process_and_post(account, niche)
        except Exception as e:
            logger.error(f"Slot {slot_index + 1} failed for {account['name']}: {e}")
            await notify_alert(f"Slot {slot_index + 1} failed: {e}")


async def _retry_job() -> None:
    """Retry failed post_queue entries from the last slot."""
    logger.info("Running retry_job")

    accounts = db.get_active_accounts()
    for account in accounts:
        failed = db.get_failed_from_queue(account["id"])
        if not failed:
            continue

        logger.info(f"Retrying {len(failed)} failed entries for {account['name']}")

        for entry in failed:
            product_data = entry["product_data"]
            await notify_retry(product_data, entry.get("retry_count", 0) + 1)

            try:
                from src.caption import generate_caption
                from src.images import post_with_image

                niche = db.get_niche_by_name(entry.get("niche_slug", ""))
                adlibs = db.get_adlibs(entry["niche_id"]) if niche else []

                caption = await generate_caption(
                    product=product_data,
                    niche=niche or {},
                    adlibs=adlibs,
                    affiliate_url=entry["affiliate_url"],
                )

                post_id = await post_with_image(
                    product=product_data,
                    caption=caption,
                    token=account["threads_token"],
                )

                now = datetime.now(timezone.utc).isoformat()
                db.update_queue_status(entry["id"], "posted", posted_at=now)
                db.insert_post_log({
                    "account_id": account["id"],
                    "niche_id": entry["niche_id"],
                    "threads_post_id": post_id,
                    "affiliate_url": entry["affiliate_url"],
                    "status": "retried",
                    "retry_count": entry.get("retry_count", 0) + 1,
                })

            except Exception as e:
                logger.error(f"Retry failed for {product_data.get('name', '')}: {e}")
                # Keep status as failed — will show up in daily verification
                db.insert_post_log({
                    "account_id": account["id"],
                    "niche_id": entry["niche_id"],
                    "threads_post_id": None,
                    "affiliate_url": entry["affiliate_url"],
                    "status": "failed",
                    "retry_count": entry.get("retry_count", 0) + 1,
                    "error_message": str(e),
                })
                await notify_failure(product_data, str(e))


async def _daily_verification() -> None:
    """Count today's posts per account. Fill gap if below target. Send summary."""
    logger.info("Running daily_verification")

    accounts = db.get_active_accounts()
    for account in accounts:
        target = account.get("post_per_day", 6)
        actual = db.count_today_posts(account["id"])

        gap = target - actual
        if gap > 0:
            logger.info(
                f"Account {account['name']}: {actual}/{target} posts. "
                f"Filling {gap} gap(s)."
            )
            # Fill gaps by running pipeline for missing slots
            niches_data = db.get_account_niches(account["id"])
            niches = [n.get("niches", n) for n in niches_data]

            for i in range(gap):
                if not niches:
                    break
                niche = niches[i % len(niches)]
                try:
                    await process_and_post(account, niche)
                except Exception as e:
                    logger.error(f"Gap fill failed: {e}")

            actual = db.count_today_posts(account["id"])

        await notify_daily_summary(account["name"], actual, target)


async def _cleanup_job() -> None:
    """Delete expired seen_products and old post_queue entries."""
    logger.info("Running cleanup_job")

    seen_deleted = db.cleanup_expired_seen()
    queue_deleted = db.cleanup_old_queue(QUEUE_RETENTION_DAYS)

    logger.info(f"Cleanup: {seen_deleted} seen, {queue_deleted} queue entries deleted")
    await notify_cleanup(seen_deleted, queue_deleted)


def start_scheduler() -> AsyncIOScheduler:
    """Configure and start the APScheduler with all 4 jobs."""
    scheduler = AsyncIOScheduler()

    # Job 1: main_pipeline — 6x/day
    post_times = _parse_post_times()
    for slot_index, (hour, minute) in enumerate(post_times):
        scheduler.add_job(
            _main_pipeline_slot,
            CronTrigger(hour=hour, minute=minute),
            args=[slot_index],
            id=f"main_pipeline_slot_{slot_index}",
            name=f"Main pipeline slot {slot_index + 1}",
        )

        # Job 2: retry_job — RETRY_DELAY_MINUTES after each slot
        retry_minute = (minute + RETRY_DELAY_MINUTES) % 60
        retry_hour = hour + ((minute + RETRY_DELAY_MINUTES) // 60)
        retry_hour = retry_hour % 24

        scheduler.add_job(
            _retry_job,
            CronTrigger(hour=retry_hour, minute=retry_minute),
            id=f"retry_job_slot_{slot_index}",
            name=f"Retry job for slot {slot_index + 1}",
        )

    # Job 3: daily_verification — 23:00 WIB
    v_parts = VERIFICATION_TIME.split(":")
    v_hour_utc = (int(v_parts[0]) - WIB_OFFSET) % 24
    v_minute = int(v_parts[1])
    scheduler.add_job(
        _daily_verification,
        CronTrigger(hour=v_hour_utc, minute=v_minute),
        id="daily_verification",
        name="Daily verification",
    )

    # Job 4: cleanup_job — 03:00 WIB
    c_parts = CLEANUP_TIME.split(":")
    c_hour_utc = (int(c_parts[0]) - WIB_OFFSET) % 24
    c_minute = int(c_parts[1])
    scheduler.add_job(
        _cleanup_job,
        CronTrigger(hour=c_hour_utc, minute=c_minute),
        id="cleanup_job",
        name="Cleanup job",
    )

    scheduler.start()
    logger.info(
        f"Scheduler started with {len(scheduler.get_jobs())} jobs. "
        f"Post times (WIB): {POST_TIMES}"
    )

    return scheduler
