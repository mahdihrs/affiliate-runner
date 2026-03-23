"""
Manual product queue seeder.

Usage:
    python scripts/seed_queue.py              # interactive single product
    python scripts/seed_queue.py --file products.json  # batch from JSON file

JSON file format:
    [
      {
        "affiliate_url": "https://s.shopee.co.id/40c1Qit8WW",
        "name": "Product Name",
        "price": 85000,
        "original_price": 120000,
        "image_url": "https://cf.shopee.co.id/file/...",
        "rating": 4.8,
        "sold_count": 532,
        "description": "Optional seller description",
        "niche": "rumah_tangga"
      }
    ]
"""

import argparse
import json
import re
import sys

from dotenv import load_dotenv

load_dotenv()

from src.db import get_active_accounts, get_account_niches, get_client, get_niche_by_name
from src.filter import score_product


NICHE_NAMES = ["rumah_tangga", "beauty", "makanan_minuman", "bayi_anak"]


def parse_shopee_url(url: str) -> tuple[str, str] | None:
    """Extract (shop_id, item_id) from a Shopee product URL."""
    m = re.search(r"/product/(\d+)/(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"-i\.(\d+)\.(\d+)", url)
    if m:
        return m.group(1), m.group(2)
    return None


def prompt(label: str, default: str = "", required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        val = input(f"{label}{suffix}: ").strip()
        result = val if val else default
        if required and not result:
            print("  ⚠  This field is required, please enter a value.")
            continue
        return result


def prompt_float(label: str, default: float | None = None) -> float:
    default_str = str(int(default)) if default is not None else ""
    while True:
        val = prompt(label, default=default_str, required=(default is None))
        cleaned = val.replace(".", "").replace(",", "").lstrip("Rp").strip()
        try:
            return float(cleaned)
        except ValueError:
            print(f"  ⚠  Invalid number '{val}', enter digits only (e.g. 85000).")


def prompt_int(label: str, default: int | None = None) -> int:
    default_str = str(default) if default is not None else ""
    while True:
        val = prompt(label, default=default_str, required=(default is None))
        cleaned = val.replace(",", "").strip()
        try:
            return int(cleaned)
        except ValueError:
            print(f"  ⚠  Invalid number '{val}', enter digits only (e.g. 532).")


def prompt_niche() -> str:
    while True:
        print()
        print("Which niche?")
        for i, n in enumerate(NICHE_NAMES, 1):
            print(f"  {i}. {n}")
        val = input("Niche name or number: ").strip().lower()
        # Accept number shortcut
        if val.isdigit() and 1 <= int(val) <= len(NICHE_NAMES):
            return NICHE_NAMES[int(val) - 1]
        if val in NICHE_NAMES:
            return val
        print(f"  ⚠  Invalid niche '{val}'. Choose from the list above.")


def collect_product_interactively() -> dict:
    """Prompt user for product details, retrying on bad input."""
    print()
    print("=== Add product to queue ===")
    print("Open the product in Shopee Affiliate dashboard to get the short link.")
    print()

    affiliate_url = prompt("Affiliate link (e.g. https://s.shopee.co.id/40c1Qit8WW)", required=True)

    url = prompt("Shopee product URL (for item/shop ID)", required=True)
    parsed = parse_shopee_url(url)
    if parsed:
        shop_id, item_id = parsed
        print(f"  Parsed → shop_id={shop_id}  item_id={item_id}")
    else:
        print("  ⚠  Could not parse IDs from URL.")
        shop_id = prompt("  Shop ID", required=True)
        item_id = prompt("  Item ID", required=True)

    name        = prompt("Product name", required=True)
    price       = prompt_float("Price (Rp, discounted)")
    orig_price  = prompt_float("Original price (Rp, before discount)", default=price)
    image_url   = prompt("Image URL (right-click image → Copy image address)", required=True)
    rating      = prompt_float("Rating (e.g. 4.8)", default=4.8)
    sold_count  = prompt_int("Sold count (e.g. 532)", default=100)
    description = prompt("Seller description (optional, Enter to skip)", default="")
    niche       = prompt_niche()

    return {
        "affiliate_url": affiliate_url,
        "shop_id": shop_id,
        "item_id": item_id,
        "name": name,
        "price": price,
        "original_price": orig_price,
        "image_url": image_url,
        "rating": rating,
        "sold_count": sold_count,
        "description": description,
        "niche": niche,
    }


def insert_product(product: dict) -> int:
    """Insert a product into post_queue for active accounts + specified niche."""
    accounts = get_active_accounts()
    if not accounts:
        print("No active accounts found.")
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
        "shop_id": product.get("shop_id", ""),
        "item_id": product.get("item_id", ""),
    }

    affiliate_url = product["affiliate_url"]
    niche_slug = product.get("niche", "")
    score = score_product(product_data)
    inserted = 0

    for account in accounts:
        niche = get_niche_by_name(niche_slug)
        if not niche:
            print(f"  ⚠  Niche '{niche_slug}' not found")
            break

        entry = {
            "account_id": account["id"],
            "niche_id": niche["id"],
            "shopee_item_id": product_data["item_id"],
            "product_data": product_data,
            "affiliate_url": affiliate_url,
            "score": score,
            "status": "pending",
            "fetch_strategy": "keyword",
        }

        get_client().table("post_queue").insert(entry).execute()
        inserted += 1
        print(f"  ✓ Queued for {account['name']} / {niche['name']}  (score={score:.1f})")

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

        if "url" in product and ("shop_id" not in product or "item_id" not in product):
            parsed = parse_shopee_url(product["url"])
            if parsed:
                product["shop_id"], product["item_id"] = parsed

        count = insert_product(product)
        total += count

    print(f"\nDone. {total} queue entries inserted.")


if __name__ == "__main__":
    main()
