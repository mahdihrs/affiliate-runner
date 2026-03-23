"""Manually run one pipeline slot for the first active account + niche."""
import asyncio
from dotenv import load_dotenv

load_dotenv()

from src.db import get_active_accounts, get_account_niches
from src.pipeline import process_and_post


async def main():
    accounts = get_active_accounts()
    if not accounts:
        print("No active accounts found")
        return
    account = accounts[0]
    niches_data = get_account_niches(account["id"])
    if not niches_data:
        print("No niches linked to account")
        return
    niche = niches_data[0].get("niches", niches_data[0])
    print(f"Running pipeline: {account['name']} / {niche['name']}")
    await process_and_post(account, niche)


asyncio.run(main())
