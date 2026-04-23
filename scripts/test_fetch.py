"""Test fetcher — DEPRECATED. Products now come from Telegram admin bot."""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.fetcher import fetch_products
from src.db import get_niche_by_name


async def main():
    print("⚠️  Fetcher is deprecated — products now come from Telegram admin bot")
    niche = get_niche_by_name("rumah_tangga")
    if not niche:
        print("Niche 'rumah_tangga' not found — run make seed first")
        return
    print(f"Attempting to fetch from: {niche['name']}")
    products, strategy = await fetch_products(niche)
    print(f"Found {len(products)} products (method: {strategy})")


asyncio.run(main())
