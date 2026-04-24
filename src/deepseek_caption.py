"""DeepSeek Chat caption generator with adlib selection and self-validation."""

import httpx
import json
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL_CHAT = os.getenv("DEEPSEEK_MODEL_CHAT", "deepseek-chat")

MAX_CAPTION_LENGTH = 500

SYSTEM_PROMPT = """Kamu copywriter konten Shopee untuk akun affiliate racunjajan.online.
Tugasmu menulis caption yang jujur, engaging, dan tidak melebih-lebihkan.

ATURAN KETAT:
- TOTAL caption WAJIB di bawah 500 karakter (termasuk link, hashtag, emoji)
- Hanya gunakan klaim yang didukung oleh deskripsi produk seller
- Jika adlib tidak relevan dengan produk, jangan gunakan
- Jangan tambahkan manfaat yang tidak disebutkan seller
- Kalau deskripsi produk minim, fokus ke harga dan spesifikasi saja
- Tulis dalam Bahasa Indonesia yang casual dan relatable
- Gunakan emoji secukupnya (maks 3)
- Hashtag maks 3-4 saja, pendek
- Format: hook (1 baris) + body (1-2 baris) + CTA + hashtags + link"""


def _build_user_prompt(
    product: dict[str, Any],
    niche: dict[str, Any],
    adlibs: list[dict[str, Any]],
    affiliate_url: str,
) -> str:
    """Build the user prompt for caption generation."""
    name = product.get("name", "")
    price = product.get("price", 0)
    discount_pct = product.get("discount_pct", 0)
    rating = product.get("rating", 0)
    sold_count = product.get("sold_count", 0)
    description = product.get("description", "")
    niche_display = niche.get("display_name", "")

    adlibs_text = ""
    if description and adlibs:
        adlib_lines = [
            f"- {a['phrase']} (angle: {a['angle']})" for a in adlibs
        ]
        adlibs_text = (
            f"\nAdlibs tersedia untuk niche ini (pilih maksimal 2 yang relevan):\n"
            + "\n".join(adlib_lines)
        )
    elif not description:
        adlibs_text = "\n(Deskripsi produk kosong — skip adlibs, fokus ke harga dan spesifikasi)"

    return f"""Nama: {name}
Harga: Rp{price:,.0f} (diskon {discount_pct}%)
Rating: {rating}/5 ({sold_count:,} terjual)
Deskripsi seller: {description}
Kategori: {niche_display}
{adlibs_text}

Tugas:
1. Pilih adlibs yang benar-benar didukung deskripsi seller di atas
2. Tulis caption dengan format yang ditentukan
3. Review — hapus klaim yang tidak ada di deskripsi seller
4. Return final caption saja, tanpa penjelasan tambahan

Link affiliate: {affiliate_url}"""


async def generate_caption(
    product: dict[str, Any],
    niche: dict[str, Any],
    adlibs: list[dict[str, Any]],
    affiliate_url: str,
) -> str:
    """Generate a caption using DeepSeek Chat.

    Returns the final caption string.
    """
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    user_prompt = _build_user_prompt(product, niche, adlibs, affiliate_url)

    logger.info(f"Generating caption for: {product.get('name', 'unknown')} (DeepSeek)")

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL_CHAT,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{DEEPSEEK_API_BASE}/chat/completions",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
    except httpx.TimeoutException:
        logger.error("DeepSeek caption API call timed out after 30 seconds")
        raise TimeoutError("DeepSeek caption generation took too long (>30s)")
    except httpx.HTTPError as e:
        logger.error(f"DeepSeek API error: {e}")
        raise

    response_data = response.json()

    # Extract text from DeepSeek response (OpenAI-compatible format)
    try:
        if "choices" in response_data and len(response_data["choices"]) > 0:
            caption = response_data["choices"][0].get("message", {}).get("content", "")
        else:
            caption = ""
    except (KeyError, IndexError, TypeError):
        logger.error(f"Unexpected DeepSeek response format: {response_data}")
        raise ValueError("Invalid DeepSeek response format")

    if not caption:
        logger.error(f"DeepSeek returned empty response: {response_data}")
        raise ValueError("DeepSeek returned no content")

    caption = caption.strip()

    if len(caption) > MAX_CAPTION_LENGTH:
        logger.warning(
            f"Caption too long ({len(caption)} chars), truncating to {MAX_CAPTION_LENGTH}"
        )
        # Truncate at last newline before limit to keep structure clean
        truncated = caption[:MAX_CAPTION_LENGTH]
        last_newline = truncated.rfind("\n")
        if last_newline > MAX_CAPTION_LENGTH * 0.6:
            caption = truncated[:last_newline]
        else:
            caption = truncated

    logger.info(f"Caption generated ({len(caption)} chars)")
    return caption
