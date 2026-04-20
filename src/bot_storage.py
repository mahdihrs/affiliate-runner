"""Persistent Supabase Storage for bot-submitted product images.

Separate from `src/images.py` — that module uploads/deletes ephemerally per post.
This bucket must PERSIST across failed post attempts until a retry succeeds.
"""

import logging
import os
import uuid

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", SUPABASE_KEY)
BUCKET_NAME = os.getenv("SUPABASE_BOT_UPLOADS_BUCKET", "bot-uploads")


def _get_storage():
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return client.storage


def upload_bot_image(image_bytes: bytes) -> tuple[str, str]:
    """Upload bytes to the bot-uploads bucket.

    Returns (public_url, storage_path).
    """
    storage_path = f"{uuid.uuid4().hex}.jpg"
    storage = _get_storage()
    storage.from_(BUCKET_NAME).upload(
        path=storage_path,
        file=image_bytes,
        file_options={"content-type": "image/jpeg"},
    )
    public_url = storage.from_(BUCKET_NAME).get_public_url(storage_path)
    logger.info("Uploaded bot image: %s", storage_path)
    return public_url, storage_path


def delete_bot_image(storage_path: str) -> None:
    """Delete an image from the bot-uploads bucket. Safe on missing objects."""
    if not storage_path:
        return
    try:
        storage = _get_storage()
        storage.from_(BUCKET_NAME).remove([storage_path])
        logger.info("Deleted bot image: %s", storage_path)
    except Exception as e:
        logger.warning("Failed to delete bot image %s: %s", storage_path, e)


def list_bot_images() -> list[dict]:
    """List all objects in the bot-uploads bucket.

    Each item has at least `name` and `created_at` (ISO string) fields.
    """
    storage = _get_storage()
    try:
        items = storage.from_(BUCKET_NAME).list()
    except Exception as e:
        logger.warning("Failed to list bot-uploads bucket: %s", e)
        return []
    return [it for it in items if "name" in it and not it["name"].endswith("/")]
