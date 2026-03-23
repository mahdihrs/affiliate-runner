"""Entry point for the racunjajan.online affiliate pipeline."""

import asyncio
import logging
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Initialize and start the scheduler."""
    from src.scheduler import start_scheduler
    from src.notify import notify_alert

    logger.info("Starting racunjajan.online pipeline")

    # Start scheduler
    scheduler = start_scheduler()

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
