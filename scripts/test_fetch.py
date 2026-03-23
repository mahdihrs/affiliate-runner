"""Test Playwright fetcher for the first niche."""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.fetcher import fetch_products
from src.db import get_niche_by_name


async def main():
    niche = get_niche_by_name("rumah_tangga")
    if not niche:
        print("Niche 'rumah_tangga' not found — run make seed first")
        return
    print(f"Fetching products for: {niche['name']}")
    products = await fetch_products(niche)
    print(f"Found {len(products)} products")
    if products:
        print("Sample:", products[0].get("name", "n/a"))


asyncio.run(main())
