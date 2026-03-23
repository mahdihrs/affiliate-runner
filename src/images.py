"""Ephemeral image handler — download, upload to Supabase bucket, delete after post."""

import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "temp-images")


def _get_storage():
    """Get Supabase storage client."""
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return client.storage


async def download_image(image_url: str) -> bytes:
    """Download an image from URL. Returns raw bytes."""
    async with httpx.AsyncClient() as client:
        response = await client.get(image_url, timeout=15, follow_redirects=True)
        response.raise_for_status()
        return response.content


async def upload_to_bucket(image_url: str, bucket_path: str) -> None:
    """Download image from URL and upload to Supabase Storage bucket."""
    image_bytes = await download_image(image_url)
    storage = _get_storage()
    storage.from_(BUCKET_NAME).upload(
        path=bucket_path,
        file=image_bytes,
        file_options={"content-type": "image/jpeg"},
    )
    logger.info(f"Uploaded image to bucket: {bucket_path}")


def get_public_url(bucket_path: str) -> str:
    """Get the public URL for a file in the bucket."""
    storage = _get_storage()
    result = storage.from_(BUCKET_NAME).get_public_url(bucket_path)
    return result


async def delete_from_bucket(bucket_path: str) -> None:
    """Delete a file from the bucket."""
    try:
        storage = _get_storage()
        storage.from_(BUCKET_NAME).remove([bucket_path])
        logger.info(f"Deleted image from bucket: {bucket_path}")
    except Exception as e:
        logger.warning(f"Failed to delete {bucket_path} from bucket: {e}")


async def post_with_image(
    product: dict[str, Any], caption: str, token: str
) -> str:
    """Orchestrate image handling + posting. Guarantees bucket cleanup via try/finally.

    Returns Threads post ID.
    """
    from src.poster import post_to_threads

    bucket_path: str | None = None
    bucket_url: str | None = None

    try:
        image_url = product.get("image_url", "")
        if image_url:
            bucket_path = f"temp-images/{product.get('item_id', 'unknown')}.jpg"
            await upload_to_bucket(image_url, bucket_path)
            bucket_url = get_public_url(bucket_path)

        post_id = await post_to_threads(
            caption=caption,
            image_url=bucket_url,
            token=token,
        )
        return post_id

    finally:
        if bucket_path:
            await delete_from_bucket(bucket_path)
