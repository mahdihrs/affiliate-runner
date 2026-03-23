"""Threads API poster (single post + carousel) with exponential backoff."""

import asyncio
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

THREADS_API_BASE = "https://graph.threads.net/v1.0"
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))


class ThreadsAPIError(Exception):
    """Raised when Threads API returns an error."""
    pass


async def _threads_request(
    method: str, url: str, token: str, **kwargs: Any
) -> dict[str, Any]:
    """Make an authenticated request to Threads API with exponential backoff."""
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method, url, headers=headers, timeout=30, **kwargs
                )

                if response.status_code == 200:
                    return response.json()

                error_data = response.json() if response.content else {}
                error_msg = error_data.get("error", {}).get("message", response.text)

                if response.status_code in (429, 500, 502, 503):
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        f"Threads API error {response.status_code} "
                        f"(attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}), "
                        f"retrying in {wait_time}s: {error_msg}"
                    )
                    await asyncio.sleep(wait_time)
                    continue

                raise ThreadsAPIError(
                    f"Threads API {response.status_code}: {error_msg}"
                )

        except httpx.RequestError as e:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                wait_time = 2 ** attempt
                logger.warning(
                    f"Request error (attempt {attempt + 1}), "
                    f"retrying in {wait_time}s: {e}"
                )
                await asyncio.sleep(wait_time)
            else:
                raise ThreadsAPIError(f"Request failed after {MAX_RETRY_ATTEMPTS} attempts: {e}")

    raise ThreadsAPIError(f"Max retries ({MAX_RETRY_ATTEMPTS}) exceeded")


async def _create_media_container(
    user_id: str, token: str, caption: str, image_url: str | None = None
) -> str:
    """Create a media container for a single post. Returns container ID."""
    params: dict[str, Any] = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": caption,
    }
    if image_url:
        params["image_url"] = image_url

    result = await _threads_request(
        "POST",
        f"{THREADS_API_BASE}/{user_id}/threads",
        token=token,
        params=params,
    )
    return result["id"]


async def _publish_container(user_id: str, token: str, container_id: str) -> str:
    """Publish a media container. Returns the post ID."""
    result = await _threads_request(
        "POST",
        f"{THREADS_API_BASE}/{user_id}/threads_publish",
        token=token,
        params={"creation_id": container_id},
    )
    return result["id"]


async def _get_user_id(token: str) -> str:
    """Get the authenticated user's Threads user ID."""
    result = await _threads_request(
        "GET",
        f"{THREADS_API_BASE}/me",
        token=token,
        params={"fields": "id"},
    )
    return result["id"]


async def post_to_threads(
    caption: str, image_url: str | None = None, token: str = ""
) -> str:
    """Post a single item to Threads.

    Args:
        caption: Post text/caption.
        image_url: Optional public image URL.
        token: Threads API access token.

    Returns:
        Threads post ID.
    """
    user_id = await _get_user_id(token)

    # Step 1: Create media container
    container_id = await _create_media_container(
        user_id=user_id,
        token=token,
        caption=caption,
        image_url=image_url,
    )

    # Wait for media processing
    await asyncio.sleep(2)

    # Step 2: Publish
    post_id = await _publish_container(user_id, token, container_id)

    logger.info(f"Published to Threads: {post_id}")
    return post_id


async def post_carousel_to_threads(
    caption: str, image_urls: list[str], token: str = ""
) -> str:
    """Post a carousel (multiple images) to Threads.

    Args:
        caption: Post text/caption.
        image_urls: List of public image URLs (up to 10).
        token: Threads API access token.

    Returns:
        Threads post ID.
    """
    user_id = await _get_user_id(token)

    # Step 1: Create individual image containers
    children_ids = []
    for img_url in image_urls[:10]:
        result = await _threads_request(
            "POST",
            f"{THREADS_API_BASE}/{user_id}/threads",
            token=token,
            params={
                "media_type": "IMAGE",
                "image_url": img_url,
                "is_carousel_item": "true",
            },
        )
        children_ids.append(result["id"])
        await asyncio.sleep(1)

    # Step 2: Create carousel container
    result = await _threads_request(
        "POST",
        f"{THREADS_API_BASE}/{user_id}/threads",
        token=token,
        params={
            "media_type": "CAROUSEL",
            "text": caption,
            "children": ",".join(children_ids),
        },
    )
    carousel_id = result["id"]

    # Wait for processing
    await asyncio.sleep(3)

    # Step 3: Publish
    post_id = await _publish_container(user_id, token, carousel_id)

    logger.info(f"Published carousel to Threads: {post_id} ({len(children_ids)} images)")
    return post_id
