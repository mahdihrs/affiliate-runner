"""
Manual product queue seeder.

Usage:
    python scripts/seed_queue.py              # interactive single product
    python scripts/seed_queue.py --file products.json  # batch from JSON file

JSON file format:
    [
      {
        "url": "https://shopee.co.id/...",
        "name": "Product Name",
        "price": 85000,
        "original_price": 120000,
        "image_url": "https://cf.shopee.co.id/file/...",
        "rating": 4.8,
        "sold_count": 532,
        "description": "Optional seller description",
        "niche": "rumah_tangga"   # optional, omit to queue for all niches
      }
    ]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from src.affiliate import make_affiliate_link
from src.db import get_active_accounts, get_account_niches, get_client, get_niche_by_name
from src.filter import score_product


NICHE_NAMES = ["rumah_tangga", "beauty", "makanan_minuman", "bayi_anak"]


def parse_shopee_url(url: str) -> tuple[str, str] | None:
    """Extract (shop_id, item_id) from a Shopee product URL.

    Supports formats:
      https://shopee.co.id/Product-Name-i.{shop_id}.{item_id}
      https://shopee.co.id/product/{shop_id}/{item_id}
    """
    # Format 1: /product/{shop_id}/{item_id}
    m = re.search(r"/product/(\d+)/(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    # Format 2: -i.{shop_id}.{item_id}
    m = re.search(r"-i\.(\d+)\.(\d+)", url)
    if m:
        return m.group(1), m.group(2)

    return None


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val else default


def prompt_float(label: str, default: float = 0.0) -> float:
    val = prompt(label, str(default))
    try:
        return float(val.replace(",", "").replace(".", "").lstrip("Rp "))
    except ValueError:
        return default


def collect_product_interactively() -> dict:
    """Prompt user for product details. Returns product dict."""
    print()
    print("=== Add product to queue ===")
    print("Tip: open the product page in your browser, copy details from there.")
    print()

    url = prompt("Shopee product URL")
    parsed = parse_shopee_url(url)
    if not parsed:
        print("⚠  Could not parse shop_id/item_id from URL. Use format: shopee.co.id/...-i.SHOPID.ITEMID")
        shop_id = prompt("  Shop ID (from URL or page source)")
        item_id = prompt("  Item ID (from URL or page source)")
    else:
        shop_id, item_id = parsed
        print(f"  Parsed → shop_id={shop_id}  item_id={item_id}")

    name = prompt("Product name")
    price = prompt_float("Price (Rp, current/discounted)")
    original_price = prompt_float("Original price (Rp, before discount)", default=price)
    image_url = prompt("Image URL (right-click product image → Copy image address)")
    rating = float(prompt("Rating (e.g. 4.8)", default="4.8"))
    sold_count = int(prompt("Sold count (e.g. 532)", default="100").replace(",", ""))
    description = prompt("Seller description (optional, press Enter to skip)", default="")

    print()
    print("Which niche?")
    for i, n in enumerate(NICHE_NAMES, 1):
        print(f"  {i}. {n}")
    niche_input = prompt("Niche name (or 'all' for every niche)").strip().lower()

    return {
        "shop_id": shop_id,
        "item_id": item_id,
        "name": name,
        "price": price,
        "original_price": original_price,
        "image_url": image_url,
        "rating": rating,
        "sold_count": sold_count,
        "description": description,
        "niche": None if niche_input == "all" else niche_input,
    }


def insert_product(product: dict) -> int:
    """Insert a product into post_queue for all active accounts / specified niche.

    Returns the number of queue entries inserted.
    """
    accounts = get_active_accounts()
    if not accounts:
        print("No active accounts found. Add an account to the database first.")
        return 0

    discount_pct = 0
    if product["original_price"] > 0 and product["original_price"] > product["price"]:
        discount_pct = round((1 - product["price"] / product["original_price"]) * 100)

    product_data = {
        "name": product["name"],
        "price": product["price"],
        "original_price": product["original_price"],
        "discount_pct": discount_pct,
        "image_url": product["image_url"],
        "rating": product["rating"],
        "sold_count": product["sold_count"],
        "description": product.get("description", ""),
        "shop_id": product["shop_id"],
        "item_id": product["item_id"],
    }

    target_niche_slug = product.get("niche")
    score = score_product(product_data)
    inserted = 0

    for account in accounts:
        niches_data = get_account_niches(account["id"])
        niches = [n.get("niches", n) for n in niches_data]

        if target_niche_slug:
            niches = [n for n in niches if n["name"] == target_niche_slug]
            if not niches:
                niche_obj = get_niche_by_name(target_niche_slug)
                niches = [niche_obj] if niche_obj else []

        if not niches:
            print(f"  ⚠  No matching niches for account {account['name']}")
            continue

        for niche in niches:
            affiliate_url = make_affiliate_link(
                item_id=product["item_id"],
                shop_id=product["shop_id"],
                affiliate_id=account["affiliate_id"],
                niche_slug=niche["name"],
            )

            entry = {
                "account_id": account["id"],
                "niche_id": niche["id"],
                "shopee_item_id": product["item_id"],
                "product_data": product_data,
                "affiliate_url": affiliate_url,
                "score": score,
                "status": "pending",
                "fetch_strategy": "keyword",
            }

            get_client().table("post_queue").insert(entry).execute()
            inserted += 1
            print(
                f"  ✓ Queued for {account['name']} / {niche['name']}  "
                f"(score={score:.1f}, url={affiliate_url[:60]}...)"
            )

    return inserted


def main():
    parser = argparse.ArgumentParser(description="Manually seed products into post_queue")
    parser.add_argument("--file", "-f", help="JSON file with list of products")
    args = parser.parse_args()

    if args.file:
        with open(args.file) as f:
            products = json.load(f)
        if not isinstance(products, list):
            products = [products]
        print(f"Loaded {len(products)} product(s) from {args.file}")
    else:
        products = [collect_product_interactively()]

    total = 0
    for i, product in enumerate(products, 1):
        if args.file:
            print(f"\n[{i}/{len(products)}] {product.get('name', 'unknown')[:60]}")

        # Parse URL if given instead of raw ids
        if "url" in product and ("shop_id" not in product or "item_id" not in product):
            parsed = parse_shopee_url(product["url"])
            if parsed:
                product["shop_id"], product["item_id"] = parsed
            else:
                print(f"  ⚠  Could not parse URL: {product['url']}")
                continue

        count = insert_product(product)
        total += count

    print(f"\nDone. {total} queue entries inserted.")


if __name__ == "__main__":
    main()
