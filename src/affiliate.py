"""Affiliate link constructor using an_redir format."""

import logging
from urllib.parse import quote

logger = logging.getLogger(__name__)

AFFILIATE_BASE = "https://s.shopee.co.id/an_redir"


def make_affiliate_link(
    item_id: str, shop_id: str, affiliate_id: str, niche_slug: str
) -> str:
    """Construct an_redir affiliate URL for a product.

    Args:
        item_id: Shopee item ID.
        shop_id: Shopee shop ID.
        affiliate_id: Affiliate ID from account (e.g. an_XXXX).
        niche_slug: Niche slug for sub_id attribution.

    Returns:
        Full affiliate tracking URL.
    """
    product_url = f"https://shopee.co.id/product/{shop_id}/{item_id}"
    encoded_url = quote(product_url, safe="")

    affiliate_url = (
        f"{AFFILIATE_BASE}"
        f"?origin_link={encoded_url}"
        f"&affiliate_id={affiliate_id}"
        f"&sub_id={niche_slug}"
    )

    logger.debug(f"Affiliate link: {affiliate_url}")
    return affiliate_url
