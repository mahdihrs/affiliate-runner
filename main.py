"""Entry point for the racunjajan.online affiliate pipeline."""

import asyncio
import logging
import signal
import sys
import os
import traceback

from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Required env vars — fail fast if any are missing.
REQUIRED_ENV_VARS = [
    "SUPABASE_URL",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def _validate_env() -> None:
    """Check that critical environment variables are set."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    # At least one Supabase key must be present
    if not os.getenv("SUPABASE_SERVICE_KEY") and not os.getenv("SUPABASE_KEY"):
        raise EnvironmentError(
            "Missing required environment variable: SUPABASE_SERVICE_KEY or SUPABASE_KEY"
        )


async def main() -> None:
    """Initialize and start the scheduler."""
    from src.notify import notify_alert

    logger.info("Starting racunjajan.online pipeline")

    # Step 1: Validate env vars before doing anything else.
    try:
        _validate_env()
    except EnvironmentError as e:
        logger.critical(f"Startup aborted: {e}")
        # Try to notify even though Telegram creds might be the problem.
        await notify_alert(f"Startup FAILED: {e}")
        sys.exit(1)

    # Step 2: Start scheduler with error handling.
    try:
        from src.scheduler import start_scheduler
        scheduler = start_scheduler()
    except Exception as e:
        tb = traceback.format_exc()
        logger.critical(f"Scheduler failed to start: {e}\n{tb}")
        await notify_alert(f"Scheduler FAILED to start:\n<code>{e}</code>")
        sys.exit(1)

    await notify_alert("Pipeline started successfully")

    # Keep the event loop running
    stop_event = asyncio.Event()

    def handle_shutdown(sig: int, frame: object) -> None:
        logger.info(f"Received signal {sig}, shutting down...")
        scheduler.shutdown(wait=False)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)

    logger.info("Pipeline stopped")


if __name__ == "__main__":
    asyncio.run(main())
