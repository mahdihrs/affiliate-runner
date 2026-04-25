"""Caption generator with provider switching (Claude or DeepSeek).

Uses Claude Haiku by default. Set USE_DEEPSEEK_CAPTION=true to use DeepSeek Chat instead.
"""

import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Provider selection
USE_DEEPSEEK = os.getenv("USE_DEEPSEEK_CAPTION", "false").lower() in ("true", "1", "yes")

# Import required modules
import anthropic
from src.deepseek_caption import generate_caption as _generate_caption_impl

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5-20251001"

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


def _generate_caption_claude(
    product: dict[str, Any],
    niche: dict[str, Any],
    adlibs: list[dict[str, Any]],
    affiliate_url: str,
) -> str:
    """Generate caption using Claude Haiku (sync version)."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    user_prompt = _build_user_prompt(product, niche, adlibs, affiliate_url)

    logger.info(f"Generating caption for: {product.get('name', 'unknown')} (Claude)")

    message = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    caption = message.content[0].text.strip()

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


async def generate_caption(
    product: dict[str, Any],
    niche: dict[str, Any],
    adlibs: list[dict[str, Any]],
    affiliate_url: str,
) -> str:
    """Generate a caption using the configured provider (Claude or DeepSeek).

    If DeepSeek is enabled but fails, falls back to Claude.
    Returns the final caption string.
    """
    if USE_DEEPSEEK:
        try:
            # Try DeepSeek first
            return await _generate_caption_impl(product, niche, adlibs, affiliate_url)
        except Exception as e:
            logger.warning(f"DeepSeek caption generation failed: {e}, falling back to Claude")
            # Fall back to Claude
            import asyncio
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                _generate_caption_claude,
                product,
                niche,
                adlibs,
                affiliate_url
            )
    else:
        # Claude is sync, but we're in an async context
        # Run it in a thread pool to avoid blocking
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            _generate_caption_claude,
            product,
            niche,
            adlibs,
            affiliate_url
        )
