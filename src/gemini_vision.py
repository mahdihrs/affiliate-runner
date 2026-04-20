"""Gemini vision — screenshot → product JSON + crop bbox."""

import io
import json
import logging
import os
import re
from typing import Any

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_configured = False


def _configure() -> None:
    global _configured
    if not _configured:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set")
        genai.configure(api_key=GEMINI_API_KEY)
        _configured = True


EXTRACTION_PROMPT = """You are extracting product data from a Shopee screenshot.

Return ONLY valid JSON with this exact shape:
{
  "name": "string or null",
  "price": number or null,
  "original_price": number or null,
  "discount_pct": number or null,
  "description": "string or null",
  "rating": number or null,
  "sold_count": number or null,
  "product_image_bbox": [ymin, xmin, ymax, xmax] or null
}

Rules:
- Values are NUMBERS for price, original_price, discount_pct, rating, sold_count (no "Rp", no commas, no "%").
- `price` is the CURRENT discounted price. `original_price` is the crossed-out price (or equal to price if no discount).
- `discount_pct` is an integer 0-100.
- `sold_count` is the total count (convert "1,2rb terjual" -> 1200, "5rb+" -> 5000).
- `description` is the seller's product description text if visible. Write a SHORT (1-2 sentence) factual description based only on what you can read in the screenshot. Do NOT invent features.
- `product_image_bbox` is the bounding box of the main product photo in the screenshot, as [ymin, xmin, ymax, xmax] normalized to 0-1000. If no clean product photo is visible, return null.
- If ANY field is not clearly readable in the screenshot, return null for that field. DO NOT GUESS.
- Return ONLY the JSON object, no markdown fences, no prose."""


def _parse_json_from_text(text: str) -> dict[str, Any]:
    """Strip fences and parse JSON object from model output."""
    stripped = text.strip()
    # Remove ```json ... ``` fences if present
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    # Find first { ... last } to be robust
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in response: {text[:200]}")
    return json.loads(stripped[start : end + 1])


def extract_product(image_bytes: bytes) -> dict[str, Any]:
    """Call Gemini vision to extract product fields + bbox from a screenshot.

    Returns a dict with keys:
      name, price, original_price, discount_pct, description, rating, sold_count,
      product_image_bbox (list[int] | None)
    Missing values are None.
    """
    _configure()
    model = genai.GenerativeModel(GEMINI_MODEL)
    image = Image.open(io.BytesIO(image_bytes))
    response = model.generate_content(
        [EXTRACTION_PROMPT, image],
        generation_config={
            "temperature": 0.1,
            "response_mime_type": "application/json",
        },
    )
    text = response.text or ""
    data = _parse_json_from_text(text)
    # Normalize types
    for num_field in ("price", "original_price", "discount_pct", "rating", "sold_count"):
        if data.get(num_field) is not None:
            try:
                if num_field in ("discount_pct", "sold_count"):
                    data[num_field] = int(data[num_field])
                else:
                    data[num_field] = float(data[num_field])
            except (TypeError, ValueError):
                data[num_field] = None
    logger.info(
        "Gemini extracted: name=%s price=%s has_bbox=%s",
        data.get("name"),
        data.get("price"),
        data.get("product_image_bbox") is not None,
    )
    return data


def crop_to_bbox(image_bytes: bytes, bbox: list[int] | None) -> bytes:
    """Crop the image to `[ymin, xmin, ymax, xmax]` normalized 0-1000.

    If `bbox` is None or invalid, returns the original bytes unchanged.
    Output is JPEG.
    """
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if bbox and len(bbox) == 4:
        try:
            ymin, xmin, ymax, xmax = bbox
            w, h = image.size
            left = max(0, int(xmin / 1000 * w))
            upper = max(0, int(ymin / 1000 * h))
            right = min(w, int(xmax / 1000 * w))
            lower = min(h, int(ymax / 1000 * h))
            if right > left and lower > upper:
                image = image.crop((left, upper, right, lower))
        except (TypeError, ValueError):
            logger.warning("Invalid bbox %s, returning uncropped", bbox)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=88)
    return out.getvalue()
