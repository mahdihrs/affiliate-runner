"""Telegram admin bot — submit products to post_queue via screenshot + chat.

Run as a second Railway service:  `python admin_bot.py`

Flow:
  1. User runs /submit.
  2. Bot asks for a screenshot.
  3. Gemini extracts fields (name, price, description, etc.) + bounding box.
  4. Cropped screenshot is uploaded to Supabase Storage (`bot-uploads` bucket).
  5. Bot asks for any missing mandatory fields (name, price, description).
  6. User pastes the affiliate link.
  7. User picks a niche (inline keyboard).
  8. User confirms → row is inserted into `post_queue` for every active account.
"""

import asyncio
import logging
import os
from typing import Any

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from src import db, bot_storage
from src.claude_vision import extract_product, crop_to_bbox

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()
}

AWAIT_PHOTO, ASK_MISSING, ASK_AFFILIATE, ASK_NICHE, CONFIRM = range(5)

MANDATORY_FIELDS = ("name", "price", "description")
OPTIONAL_FIELDS = ("original_price", "discount_pct", "rating", "sold_count")
NUMERIC_FIELDS = {"price", "original_price", "discount_pct", "rating", "sold_count"}


# --- Guards ---------------------------------------------------------------

def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if not ALLOWED_USER_IDS:
        logger.warning("TELEGRAM_ALLOWED_USER_IDS not set — rejecting all users")
        return False
    return user.id in ALLOWED_USER_IDS


async def _reject_unauthorized(update: Update) -> None:
    user = update.effective_user
    logger.warning("Unauthorized access attempt from user_id=%s", user.id if user else "?")
    if update.message:
        await update.message.reply_text("Sorry, you are not authorized to use this bot.")


# --- Helpers --------------------------------------------------------------

def _fmt_price(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"Rp{float(value):,.0f}"
    except (TypeError, ValueError):
        return str(value)


def _summary(data: dict[str, Any]) -> str:
    lines = [
        f"<b>Name:</b> {data.get('name') or '—'}",
        f"<b>Price:</b> {_fmt_price(data.get('price'))}",
        f"<b>Original price:</b> {_fmt_price(data.get('original_price'))}",
        f"<b>Discount:</b> {data.get('discount_pct') if data.get('discount_pct') is not None else '—'}%",
        f"<b>Rating:</b> {data.get('rating') if data.get('rating') is not None else '—'}",
        f"<b>Sold count:</b> {data.get('sold_count') if data.get('sold_count') is not None else '—'}",
        f"<b>Description:</b> {data.get('description') or '—'}",
    ]
    if data.get("affiliate_url"):
        lines.append(f"<b>Affiliate:</b> {data['affiliate_url']}")
    if data.get("niche_name"):
        lines.append(f"<b>Niche:</b> {data['niche_name']}")
    return "\n".join(lines)


def _missing_mandatory(data: dict[str, Any]) -> list[str]:
    return [f for f in MANDATORY_FIELDS if data.get(f) in (None, "")]


def _parse_field_lines(text: str) -> tuple[dict[str, Any], list[str]]:
    """Parse `field: value` lines. Returns (parsed, unknown_keys)."""
    parsed: dict[str, Any] = {}
    unknown: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key not in MANDATORY_FIELDS and key not in OPTIONAL_FIELDS:
            unknown.append(key)
            continue
        if key in NUMERIC_FIELDS:
            cleaned = value.replace(",", "").replace(".", "").replace("Rp", "").strip()
            try:
                parsed[key] = int(cleaned) if key in ("discount_pct", "sold_count") else float(cleaned)
            except ValueError:
                unknown.append(f"{key} (invalid number: {value!r})")
        else:
            parsed[key] = value
    return parsed, unknown


# --- Handlers -------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.message.reply_text(
        "Hi! Use /submit to queue a new product.\n\n"
        "Commands:\n"
        "  /submit — submit a product (bot will ask for a screenshot)\n"
        "  /cancel — abort the current submission\n"
        "  /whoami — show your Telegram user_id"
    )


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Useful for discovering your Telegram user_id to populate the allowlist."""
    user = update.effective_user
    await update.message.reply_text(f"Your Telegram user_id is: {user.id}")


async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for a new product submission — ask for a screenshot."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "Send me a screenshot of the Shopee product.\n\nUse /cancel to abort."
    )
    return AWAIT_PHOTO


async def stray_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top-level handler for photos sent outside a /submit flow."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.message.reply_text(
        "To submit a product, start with /submit first, then send the screenshot."
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return ConversationHandler.END

    msg = await update.message.reply_text("Got it, analyzing the screenshot...")

    # Download the largest photo
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    # Gemini extraction — run in thread since the SDK is sync
    # Add 35-second timeout (5s buffer above Gemini's 30s timeout)
    try:
        extracted = await asyncio.wait_for(
            asyncio.to_thread(extract_product, image_bytes),
            timeout=35
        )
    except asyncio.TimeoutError:
        logger.error("Gemini extraction timed out")
        await msg.edit_text("Analysis took too long. Try with a clearer screenshot or /cancel.")
        return ConversationHandler.END
    except Exception as e:
        logger.exception("Gemini extraction failed")
        await msg.edit_text(f"Failed to analyze image: {e}\nSend another screenshot or /cancel.")
        return ConversationHandler.END

    bbox = extracted.pop("product_image_bbox", None)

    # Crop and upload the image regardless — we'll keep it unless user cancels.
    cropped = await asyncio.to_thread(crop_to_bbox, image_bytes, bbox)
    try:
        public_url, storage_path = await asyncio.to_thread(bot_storage.upload_bot_image, cropped)
    except Exception as e:
        logger.exception("Upload to bot-uploads bucket failed")
        await msg.edit_text(f"Failed to save the image: {e}\nTry again or /cancel.")
        return ConversationHandler.END

    # Compute discount if possible
    if (
        extracted.get("discount_pct") is None
        and extracted.get("price")
        and extracted.get("original_price")
        and extracted["original_price"] > extracted["price"]
    ):
        extracted["discount_pct"] = round(
            (1 - extracted["price"] / extracted["original_price"]) * 100
        )

    context.user_data.clear()
    context.user_data.update(extracted)
    context.user_data["image_url"] = public_url
    context.user_data["image_storage_path"] = storage_path

    missing = _missing_mandatory(context.user_data)
    if missing:
        await msg.edit_text(
            _summary(context.user_data)
            + f"\n\n<b>Missing:</b> {', '.join(missing)}\n"
            "Reply with `field: value` — one per line. Example:\n"
            "<code>name: Detergen Cair 1L\nprice: 35000</code>",
            parse_mode="HTML",
        )
        return ASK_MISSING

    await msg.edit_text(
        _summary(context.user_data)
        + "\n\nNow paste the <b>affiliate link</b>.",
        parse_mode="HTML",
    )
    return ASK_AFFILIATE


async def handle_missing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update):
        return ConversationHandler.END
    text = update.message.text or ""
    parsed, unknown = _parse_field_lines(text)
    if not parsed and not unknown:
        await update.message.reply_text(
            "Couldn't parse any fields. Format: <code>field: value</code> (one per line).",
            parse_mode="HTML",
        )
        return ASK_MISSING

    context.user_data.update(parsed)
    notes = []
    if unknown:
        notes.append(f"Ignored: {', '.join(unknown)}")

    missing = _missing_mandatory(context.user_data)
    if missing:
        reply = _summary(context.user_data) + f"\n\n<b>Still missing:</b> {', '.join(missing)}"
        if notes:
            reply += "\n" + "\n".join(notes)
        await update.message.reply_text(reply, parse_mode="HTML")
        return ASK_MISSING

    # Recompute discount if both prices are present and pct is not
    if (
        context.user_data.get("discount_pct") is None
        and context.user_data.get("price")
        and context.user_data.get("original_price")
        and context.user_data["original_price"] > context.user_data["price"]
    ):
        context.user_data["discount_pct"] = round(
            (1 - context.user_data["price"] / context.user_data["original_price"]) * 100
        )

    reply = _summary(context.user_data) + "\n\nNow paste the <b>affiliate link</b>."
    if notes:
        reply = "\n".join(notes) + "\n\n" + reply
    await update.message.reply_text(reply, parse_mode="HTML")
    return ASK_AFFILIATE


async def handle_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update):
        return ConversationHandler.END
    url = (update.message.text or "").strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("That doesn't look like a URL. Paste the affiliate link again.")
        return ASK_AFFILIATE
    context.user_data["affiliate_url"] = url

    # Load niches from DB for the keyboard
    try:
        niches = await asyncio.to_thread(
            lambda: db.get_client().table("niches").select("id,name,display_name").execute().data
        )
    except Exception as e:
        logger.exception("Failed to load niches")
        await update.message.reply_text(f"Couldn't load niches: {e}. Try /cancel and retry.")
        return ASK_AFFILIATE

    if not niches:
        await update.message.reply_text("No niches found in DB. Aborting.")
        return await cmd_cancel(update, context)

    context.user_data["_niches_by_id"] = {n["id"]: n for n in niches}
    keyboard = [
        [InlineKeyboardButton(n.get("display_name") or n["name"], callback_data=f"niche:{n['id']}")]
        for n in niches
    ]
    await update.message.reply_text(
        "Pick a niche:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_NICHE


async def handle_niche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    niche_id = query.data.split(":", 1)[1]
    niche = context.user_data.get("_niches_by_id", {}).get(niche_id)
    if not niche:
        await query.edit_message_text("Niche not found — try /cancel and restart.")
        return ConversationHandler.END

    context.user_data["niche_id"] = niche_id
    context.user_data["niche_name"] = niche.get("display_name") or niche["name"]

    keyboard = [[
        InlineKeyboardButton("✅ Queue it", callback_data="confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]]
    await query.edit_message_text(
        _summary(context.user_data) + "\n\nReady to queue?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CONFIRM


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await _cleanup_image(context)
        await query.edit_message_text("Cancelled. Nothing was queued.")
        context.user_data.clear()
        return ConversationHandler.END

    ud = context.user_data
    product_data = {
        "name": ud.get("name") or "",
        "price": ud.get("price") or 0,
        "original_price": ud.get("original_price") or ud.get("price") or 0,
        "discount_pct": ud.get("discount_pct") or 0,
        "image_url": ud.get("image_url") or "",
        "rating": ud.get("rating") or 0,
        "sold_count": ud.get("sold_count") or 0,
        "description": ud.get("description") or "",
        "shop_id": "",
        "item_id": "",
        "image_storage_path": ud.get("image_storage_path") or "",
        "source": "telegram_bot",
    }

    try:
        inserted = await asyncio.to_thread(
            db.insert_bot_queue_entries,
            product_data,
            ud["affiliate_url"],
            ud["niche_id"],
        )
    except Exception as e:
        logger.exception("Insert to queue failed")
        await query.edit_message_text(f"Insert failed: {e}\nThe image is still in storage. Try /cancel.")
        return ConversationHandler.END

    await query.edit_message_text(
        _summary(ud)
        + f"\n\n✅ Queued for {inserted} account(s). The scheduler will post it on the next slot."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _cleanup_image(context)
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Cancelled.")
    elif update.callback_query:
        await update.callback_query.edit_message_text("Cancelled.")
    return ConversationHandler.END


async def _cleanup_image(context: ContextTypes.DEFAULT_TYPE) -> None:
    path = context.user_data.get("image_storage_path")
    if path:
        try:
            await asyncio.to_thread(bot_storage.delete_bot_image, path)
        except Exception:
            logger.exception("Failed to delete image on cancel")


# --- Entry point ----------------------------------------------------------

async def await_photo_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent text while we were waiting for a photo — remind them."""
    await update.message.reply_text("I'm waiting for a screenshot. Send an image, or /cancel.")
    return AWAIT_PHOTO


async def _set_commands(app: Application) -> None:
    """Register the command menu so /submit etc. appear above the Telegram keyboard."""
    await app.bot.set_my_commands([
        BotCommand("submit", "Submit a new product"),
        BotCommand("cancel", "Abort the current submission"),
        BotCommand("start", "Show help"),
        BotCommand("whoami", "Show your Telegram user_id"),
    ])


def build_application() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    if not ALLOWED_USER_IDS:
        logger.warning(
            "TELEGRAM_ALLOWED_USER_IDS is empty — the bot will reject every user. "
            "Send /whoami to the bot to discover your user_id, then set the env var."
        )

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_set_commands).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("submit", cmd_submit)],
        states={
            AWAIT_PHOTO: [
                MessageHandler(filters.PHOTO, handle_photo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, await_photo_text),
            ],
            ASK_MISSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_missing)],
            ASK_AFFILIATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate)],
            ASK_NICHE: [CallbackQueryHandler(handle_niche, pattern=r"^niche:")],
            CONFIRM: [CallbackQueryHandler(handle_confirm, pattern=r"^(confirm|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=600,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(conversation)
    # Stray photos outside a /submit flow get a gentle nudge.
    app.add_handler(MessageHandler(filters.PHOTO, stray_photo))
    return app


def main() -> None:
    logger.info("Starting admin_bot")
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
