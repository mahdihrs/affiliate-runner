"""Fetcher stub — products now come from Telegram admin bot."""

import logging

logger = logging.getLogger(__name__)


async def fetch_products(niche, target=10):
    """Deprecated — products are now submitted via Telegram admin bot."""
    logger.warning("fetch_products() is deprecated — use Telegram admin bot instead")
    return [], "telegram"
