"""Telegram admin bot — submit products to post_queue via chat.

Run as a second Railway service:  `python admin_bot.py`

Flow:
  1. User runs /submit.
  2. User types product name and price.
  3. User types keywords/description.
  4. User uploads product image (no editing/cropping).
  5. Bot generates caption (60s timeout, full error shown).
  6. User reviews: use / re-generate (with optional tone) / manual write.
  7. User pastes affiliate link.
  8. User confirms → row inserted into `post_queue` for every active account.
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
from src.caption import generate_caption

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USER_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if x.strip().isdigit()
}

# Conversation states
(
    ASK_NAME_PRICE,
    ASK_KEYWORDS,
    AWAIT_IMAGE,
    GENERATING_CAPTION,
    REVIEW_CAPTION,
    ASK_REGEN_TONE,
    ASK_MANUAL_CAPTION,
    ASK_AFFILIATE,
    ASK_NICHE,
    CONFIRM,
) = range(10)

CAPTION_TIMEOUT_SECONDS = 60


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
    ]
    if data.get("original_price"):
        lines.append(f"<b>Original price:</b> {_fmt_price(data.get('original_price'))}")
    if data.get("discount_pct"):
        lines.append(f"<b>Discount:</b> {data['discount_pct']}%")
    if data.get("seller_keywords"):
        lines.append(f"<b>Keywords:</b> {', '.join(data['seller_keywords'])}")
    if data.get("image_url"):
        lines.append("<b>Image:</b> ✅ uploaded")
    if data.get("affiliate_url"):
        lines.append(f"<b>Affiliate:</b> {data['affiliate_url']}")
    if data.get("niche_name"):
        lines.append(f"<b>Niche:</b> {data['niche_name']}")
    if data.get("final_caption"):
        lines.append(f"\n<b>Caption:</b>\n{data['final_caption']}")
    return "\n".join(lines)


def _parse_keywords(text: str) -> list[str]:
    chunks = text.replace(";", ",").split(",")
    if len(chunks) == 1:
        chunks = text.splitlines()
    keywords: list[str] = []
    seen: set[str] = set()
    for raw in chunks:
        word = raw.strip().lower()
        if not word:
            continue
        if word in seen:
            continue
        seen.add(word)
        keywords.append(word)
    return keywords


# --- Handlers -------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.message.reply_text(
        "Hi! Use /submit to queue a new product.\n\n"
        "Commands:\n"
        "  /submit — submit a product\n"
        "  /cancel — abort the current submission\n"
        "  /whoami — show your Telegram user_id"
    )


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(f"Your Telegram user_id is: {user.id}")


async def cmd_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — ask for product name and price."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "Kirim <b>nama produk</b> dan <b>harga</b> (pisahkan baris baru).\n\n"
        "Contoh:\n<code>Detergen Cair 1L\n35000</code>\n\n"
        "Opsional baris ke-3: harga asli (sebelum diskon)\n"
        "Use /cancel to abort.",
        parse_mode="HTML",
    )
    return ASK_NAME_PRICE


async def handle_name_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse product name and price from user message."""
    if not _is_authorized(update):
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if len(lines) < 2:
        await update.message.reply_text(
            "Kirim minimal 2 baris:\n1. Nama produk\n2. Harga\n\n"
            "Contoh:\n<code>Detergen Cair 1L\n35000</code>",
            parse_mode="HTML",
        )
        return ASK_NAME_PRICE

    name = lines[0]
    price_raw = lines[1].replace(",", "").replace(".", "").replace("Rp", "").replace("rp", "").strip()
    try:
        price = float(price_raw)
    except ValueError:
        await update.message.reply_text(
            f"Harga tidak valid: <code>{lines[1]}</code>\nKirim ulang nama + harga.",
            parse_mode="HTML",
        )
        return ASK_NAME_PRICE

    context.user_data["name"] = name
    context.user_data["price"] = price

    # Optional: original price on line 3
    if len(lines) >= 3:
        orig_raw = lines[2].replace(",", "").replace(".", "").replace("Rp", "").replace("rp", "").strip()
        try:
            original_price = float(orig_raw)
            context.user_data["original_price"] = original_price
            if original_price > price:
                context.user_data["discount_pct"] = round(
                    (1 - price / original_price) * 100
                )
        except ValueError:
            pass

    await update.message.reply_text(
        f"✅ <b>{name}</b> — {_fmt_price(price)}\n\n"
        "Sekarang kirim <b>keywords/deskripsi</b> produk (pisahkan koma atau baris baru).",
        parse_mode="HTML",
    )
    return ASK_KEYWORDS


async def handle_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Parse keywords/description."""
    if not _is_authorized(update):
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Kirim keywords/deskripsi produk.")
        return ASK_KEYWORDS

    keywords = _parse_keywords(text)
    if not keywords:
        await update.message.reply_text(
            "Belum kebaca keyword-nya. Kirim lagi (pisahkan koma atau baris baru)."
        )
        return ASK_KEYWORDS

    context.user_data["seller_keywords"] = keywords
    context.user_data["description"] = text

    await update.message.reply_text(
        f"Keywords: {', '.join(keywords)}\n\n"
        "Sekarang <b>upload image produk</b> (kirim sebagai foto).",
        parse_mode="HTML",
    )
    return AWAIT_IMAGE


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Upload product image — no editing, no cropping."""
    if not _is_authorized(update):
        return ConversationHandler.END

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        await update.message.reply_text("Kirim image sebagai foto (bukan file). Coba lagi.")
        return AWAIT_IMAGE

    msg = await update.message.reply_text("Uploading image...")

    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    try:
        public_url, storage_path = await asyncio.to_thread(
            bot_storage.upload_bot_image, image_bytes
        )
    except Exception as e:
        logger.exception("Image upload failed")
        await msg.edit_text(f"Gagal upload image: {e}\nKirim ulang foto atau /cancel.")
        return AWAIT_IMAGE

    context.user_data["image_url"] = public_url
    context.user_data["image_storage_path"] = storage_path

    await msg.edit_text("Image uploaded ✅\n\nGenerating caption...")

    # Generate caption immediately after image upload
    return await _do_generate_caption(msg, context)


async def _do_generate_caption(
    msg, context: ContextTypes.DEFAULT_TYPE, tone: str | None = None
) -> int:
    """Generate caption with 60s timeout. Returns next state."""
    ud = context.user_data

    # Build product dict for caption generator
    product = {
        "name": ud.get("name") or "",
        "price": ud.get("price") or 0,
        "original_price": ud.get("original_price") or ud.get("price") or 0,
        "discount_pct": ud.get("discount_pct") or 0,
        "rating": ud.get("rating") or 0,
        "sold_count": ud.get("sold_count") or 0,
        "description": ud.get("description") or "",
    }
    keywords = ud.get("seller_keywords") or []
    if keywords:
        product["description"] = (
            f"{product['description']}\n\nKeyword seller: " + ", ".join(keywords)
        ).strip()

    if tone:
        product["description"] += f"\n\nTone/instruksi khusus: {tone}"

    # We need niche for caption — use a default or ask later
    # For now generate without niche-specific adlibs; niche is asked after caption
    niche = {}
    adlibs = []

    # If niche already selected (from regen after niche step), use it
    if ud.get("niche_id"):
        niche = await asyncio.to_thread(db.get_niche_by_id, ud["niche_id"]) or {}
        adlibs = await asyncio.to_thread(db.get_adlibs, ud["niche_id"])

    try:
        caption = await asyncio.wait_for(
            generate_caption(
                product=product,
                niche=niche,
                adlibs=adlibs,
                affiliate_url=ud.get("affiliate_url") or "{{LINK}}",
            ),
            timeout=CAPTION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await msg.edit_text(
            f"❌ Caption generation timed out (>{CAPTION_TIMEOUT_SECONDS}s).\n\n"
            "Pilih opsi:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Coba lagi", callback_data="cap:regen"),
                InlineKeyboardButton("✍️ Tulis manual", callback_data="cap:manual"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]]),
        )
        return REVIEW_CAPTION
    except Exception as e:
        logger.exception("Caption generation failed")
        error_str = str(e)
        # Show full error to user
        await msg.edit_text(
            f"❌ Gagal generate caption:\n\n<code>{error_str}</code>\n\nPilih opsi:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Coba lagi", callback_data="cap:regen"),
                InlineKeyboardButton("✍️ Tulis manual", callback_data="cap:manual"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]]),
        )
        return REVIEW_CAPTION

    context.user_data["generated_caption"] = caption
    context.user_data["final_caption"] = caption

    await msg.edit_text(
        f"<b>Draft caption:</b>\n\n{caption}",
        parse_mode="HTML",
        reply_markup=_caption_review_keyboard(),
    )
    return REVIEW_CAPTION


def _caption_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Pakai caption ini", callback_data="cap:use"),
            InlineKeyboardButton("✍️ Tulis manual", callback_data="cap:manual"),
        ],
        [
            InlineKeyboardButton("🔁 Re-generate", callback_data="cap:regen"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ],
    ])


async def handle_caption_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle caption review buttons."""
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await _cleanup_image(context)
        await query.edit_message_text("Cancelled. Nothing was queued.")
        context.user_data.clear()
        return ConversationHandler.END

    action = query.data.split(":", 1)[1]

    if action == "use":
        # Proceed to affiliate link
        await query.edit_message_text(
            "Caption tersimpan ✅\n\nSekarang paste <b>affiliate link</b>.",
            parse_mode="HTML",
        )
        return ASK_AFFILIATE

    if action == "manual":
        await query.edit_message_text(
            "Kirim caption manual kamu. Nanti masih bisa diedit lagi."
        )
        return ASK_MANUAL_CAPTION

    if action == "regen":
        await query.edit_message_text(
            "Mau ada tone/instruksi khusus untuk caption?\n\n"
            "Contoh: <i>lebih playful</i>, <i>fokus ke diskon</i>, <i>formal</i>\n\n"
            "Atau kirim <b>skip</b> untuk regenerate tanpa instruksi khusus.",
            parse_mode="HTML",
        )
        return ASK_REGEN_TONE

    return REVIEW_CAPTION


async def handle_regen_tone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User provides tone/instruction for regeneration, or 'skip'."""
    if not _is_authorized(update):
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    tone = None if text.lower() in ("skip", "-", "") else text

    msg = await update.message.reply_text("Re-generating caption...")
    return await _do_generate_caption(msg, context, tone=tone)


async def handle_manual_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User writes manual caption."""
    if not _is_authorized(update):
        return ConversationHandler.END
    caption = (update.message.text or "").strip()
    if not caption:
        await update.message.reply_text("Caption kosong. Kirim caption manual yang valid.")
        return ASK_MANUAL_CAPTION

    context.user_data["edited_caption"] = caption
    context.user_data["final_caption"] = caption

    await update.message.reply_text(
        f"<b>Caption:</b>\n\n{caption}\n\n",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Pakai ini", callback_data="cap:use"),
                InlineKeyboardButton("🔁 Re-generate AI", callback_data="cap:regen"),
            ],
            [
                InlineKeyboardButton("✍️ Tulis ulang", callback_data="cap:manual"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ],
        ]),
    )
    return REVIEW_CAPTION


async def handle_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User pastes affiliate link."""
    if not _is_authorized(update):
        return ConversationHandler.END
    url = (update.message.text or "").strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("Bukan URL valid. Paste affiliate link lagi.")
        return ASK_AFFILIATE

    context.user_data["affiliate_url"] = url

    # Load niches for keyboard
    try:
        niches = await asyncio.to_thread(
            lambda: db.get_client().table("niches").select("id,name,display_name").execute().data
        )
    except Exception as e:
        logger.exception("Failed to load niches")
        await update.message.reply_text(f"Gagal load niches: {e}. Coba /cancel dan ulangi.")
        return ASK_AFFILIATE

    if not niches:
        await update.message.reply_text("Tidak ada niche di DB. Aborting.")
        return await cmd_cancel(update, context)

    context.user_data["_niches_by_id"] = {n["id"]: n for n in niches}
    keyboard = [
        [InlineKeyboardButton(n.get("display_name") or n["name"], callback_data=f"niche:{n['id']}")]
        for n in niches
    ]
    await update.message.reply_text(
        "Pilih niche:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ASK_NICHE


async def handle_niche(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User picks a niche → show confirm."""
    query = update.callback_query
    await query.answer()
    niche_id = query.data.split(":", 1)[1]
    niche = context.user_data.get("_niches_by_id", {}).get(niche_id)
    if not niche:
        await query.edit_message_text("Niche not found — coba /cancel dan ulangi.")
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
    """Final confirmation — insert into post_queue."""
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await _cleanup_image(context)
        await query.edit_message_text("Cancelled. Nothing was queued.")
        context.user_data.clear()
        return ConversationHandler.END

    ud = context.user_data

    # Replace {{LINK}} placeholder in caption with actual affiliate URL
    final_caption = (ud.get("final_caption") or "").replace("{{LINK}}", ud.get("affiliate_url", ""))
    ud["final_caption"] = final_caption

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
        "seller_keywords": ud.get("seller_keywords") or [],
        "approved_caption": final_caption,
        "caption_source": "manual" if ud.get("edited_caption") else "ai",
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
        await query.edit_message_text(f"Insert failed: {e}\nImage masih di storage. Coba /cancel.")
        return ConversationHandler.END

    await query.edit_message_text(
        _summary(ud)
        + f"\n\n✅ Queued for {inserted} account(s). Scheduler akan posting di slot berikutnya."
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


# --- Fallback text handlers for wrong-state messages ----------------------

async def _text_in_image_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Saya nunggu image. Kirim foto produk atau /cancel.")
    return AWAIT_IMAGE


async def stray_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Top-level handler for photos sent outside a /submit flow."""
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return
    await update.message.reply_text(
        "Untuk submit produk, mulai dengan /submit dulu."
    )


# --- Entry point ----------------------------------------------------------

async def _set_commands(app: Application) -> None:
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
            ASK_NAME_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_price),
            ],
            ASK_KEYWORDS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_keywords),
            ],
            AWAIT_IMAGE: [
                MessageHandler(filters.PHOTO, handle_image),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _text_in_image_state),
            ],
            REVIEW_CAPTION: [
                CallbackQueryHandler(handle_caption_review, pattern=r"^(cap:.*|cancel)$"),
            ],
            ASK_REGEN_TONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_regen_tone),
            ],
            ASK_MANUAL_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_manual_caption),
            ],
            ASK_AFFILIATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_affiliate),
            ],
            ASK_NICHE: [
                CallbackQueryHandler(handle_niche, pattern=r"^niche:"),
            ],
            CONFIRM: [
                CallbackQueryHandler(handle_confirm, pattern=r"^(confirm|cancel)$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=600,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(conversation)
    app.add_handler(MessageHandler(filters.PHOTO, stray_photo))
    return app


def main() -> None:
    logger.info("Starting admin_bot")
    app = build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
