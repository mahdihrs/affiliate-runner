"""Test Claude caption generator for the first niche."""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.caption import generate_caption
from src.db import get_niche_by_name, get_adlibs


async def main():
    niche = get_niche_by_name("rumah_tangga")
    if not niche:
        print("Niche 'rumah_tangga' not found — run make seed first")
        return
    adlibs = get_adlibs(niche["id"])
    product = {
        "name": "Rak Dapur Minimalis",
        "price": 85000,
        "original_price": 120000,
        "discount_pct": 29,
        "rating": 4.8,
        "sold_count": 532,
        "description": "Rak serbaguna anti karat, bahan stainless steel, mudah dipasang tanpa bor.",
    }
    caption = await generate_caption(product, niche, adlibs, "https://s.shopee.co.id/test")
    print(caption)


asyncio.run(main())
