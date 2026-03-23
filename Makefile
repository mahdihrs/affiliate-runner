VENV := .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: install seed run test-pipeline test-caption test-fetch help

help:
	@echo "Available commands:"
	@echo "  make install        Install all dependencies + Playwright browsers"
	@echo "  make seed           Seed niches and adlibs to Supabase"
	@echo "  make run            Start the scheduler (all 4 jobs)"
	@echo "  make test-pipeline  Run a single pipeline slot manually"
	@echo "  make test-fetch     Test Playwright fetcher for a niche"
	@echo "  make test-caption   Test Claude caption generator"

install:
	python3.11 -m venv $(VENV)
	$(PIP) install -r requirements.txt
	$(VENV)/bin/playwright install chromium

seed:
	$(PYTHON) scripts/seed.py

run:
	$(PYTHON) main.py

test-pipeline:
	$(PYTHON) -c "\
import asyncio, os; \
from dotenv import load_dotenv; load_dotenv(); \
from src.db import get_active_accounts, get_account_niches; \
from src.pipeline import process_and_post; \
async def main(): \
    accounts = get_active_accounts(); \
    if not accounts: print('No active accounts found'); return; \
    account = accounts[0]; \
    niches = get_account_niches(account['id']); \
    if not niches: print('No niches linked to account'); return; \
    niche = niches[0]; \
    print(f'Running pipeline: {account[\"name\"]} / {niche[\"name\"]}'); \
    await process_and_post(account, niche); \
asyncio.run(main())"

test-fetch:
	$(PYTHON) -c "\
import asyncio; \
from dotenv import load_dotenv; load_dotenv(); \
from src.fetcher import fetch_products; \
from src.db import get_niches; \
async def main(): \
    niches = get_niches(); \
    niche = niches[0]; \
    print(f'Fetching products for: {niche[\"name\"]}'); \
    products = await fetch_products(niche); \
    print(f'Found {len(products)} products'); \
    if products: print('Sample:', products[0].get('name', 'n/a')); \
asyncio.run(main())"

test-caption:
	$(PYTHON) -c "\
import asyncio; \
from dotenv import load_dotenv; load_dotenv(); \
from src.caption import generate_caption; \
from src.db import get_niches, get_niche_adlibs; \
async def main(): \
    niches = get_niches(); \
    niche = niches[0]; \
    adlibs = get_niche_adlibs(niche['id']); \
    product = {'name': 'Rak Dapur Minimalis', 'price': 85000, 'original_price': 120000, \
               'discount_pct': 29, 'rating': 4.8, 'sold_count': 532, \
               'description': 'Rak serbaguna anti karat, bahan stainless steel, mudah dipasang tanpa bor.'}; \
    caption = await generate_caption(product, niche, adlibs, 'https://s.shopee.co.id/test'); \
    print(caption); \
asyncio.run(main())"
