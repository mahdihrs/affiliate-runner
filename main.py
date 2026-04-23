"""Entry point for the racunjajan.online affiliate pipeline.

Runs the APScheduler (post pipeline) and Telegram admin bot alongside
a minimal Flask HTTP server for health checks (Render requirement).
"""

import asyncio
import logging
import signal
import sys
import os
import traceback
import threading

from dotenv import load_dotenv
from telegram import Update
from flask import Flask, jsonify

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


def _start_health_server() -> None:
    """Start a minimal Flask HTTP server for Render health checks."""
    app = Flask(__name__)

    @app.route("/", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "racunjajan-pipeline"}), 200

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "healthy"}), 200

    port = int(os.getenv("PORT", "10000"))
    logger.info(f"Starting Flask health server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


async def main() -> None:
    """Initialize and start the scheduler + admin bot."""
    from src.notify import notify_alert

    logger.info("Starting racunjajan.online pipeline")

    # Step 0: Start Flask health server in background thread.
    health_thread = threading.Thread(target=_start_health_server, daemon=True)
    health_thread.start()
    logger.info("Health check server thread started")

    # Step 1: Validate env vars before doing anything else.
    try:
        _validate_env()
    except EnvironmentError as e:
        logger.critical(f"Startup aborted: {e}")
        await notify_alert(f"Startup FAILED: {e}")
        sys.exit(1)

    # Step 2: Start scheduler.
    try:
        from src.scheduler import start_scheduler
        scheduler = start_scheduler()
    except Exception as e:
        tb = traceback.format_exc()
        logger.critical(f"Scheduler failed to start: {e}\n{tb}")
        await notify_alert(f"Scheduler FAILED to start:\n<code>{e}</code>")
        sys.exit(1)

    # Step 3: Start admin bot (non-blocking).
    bot_app = None
    try:
        from admin_bot import build_application
        bot_app = build_application()
        await bot_app.initialize()
        await bot_app.start()
        await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Admin bot started (polling)")
    except Exception as e:
        logger.error(f"Admin bot failed to start: {e}")
        await notify_alert(f"Admin bot FAILED to start:\n<code>{e}</code>")
        # Continue — scheduler can still run without the bot.

    await notify_alert("Pipeline started successfully (scheduler + admin bot)")

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

    # Graceful bot shutdown
    if bot_app:
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception:
            pass

    logger.info("Pipeline stopped")


if __name__ == "__main__":
    asyncio.run(main())
