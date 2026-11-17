"""Telegram handlers + conversation flow: extract -> read back -> confirm/edit/cancel -> save."""
import asyncio
import logging
from datetime import datetime
from functools import wraps

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
import db
import sheets
import summary
from extract import ExtractionError, extract
from schema import SiteUpdate

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines print the bot token in the URL
log = logging.getLogger("bot")

START_TEXT = """🏗️ Site Daily Log Bot

Bhejo apna aaj ka update, Hindi / English / Hinglish me. Jaise:
"Aaj 40 sq ft false ceiling ka kaam hua, 6 mazdoor, 2 bag cement nikala"

Send your daily update in plain Hindi / English / Hinglish. I'll read it back; tap Yes to save.

Commands:
/today - aaj ka log / today's log
/stock cement - stock balance
/undo - apni last entry hatao / delete your last entry
/summary - abhi ka summary / summary now
/cancel - current message chhodo / discard pending"""


def today_str() -> str:
    return datetime.now(config.TZ).date().isoformat()


def authorised(fn):
    @wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id not in config.ALLOWED_USER_IDS:
            msg = f"Not authorised. (Your Telegram ID is {user.id if user else '?'})"
            log.warning("REJECTED user id=%s name=%s", user.id if user else "?", user.full_name if user else "?")
            if update.callback_query:
                await update.callback_query.answer(msg, show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(msg)
            return
        return await fn(update, context)
    return wrapper


KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Yes", callback_data="confirm"),
    InlineKeyboardButton("✏️ Edit", callback_data="edit"),
    InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
]])


# ---------------------------------------------------------------- commands

@authorised
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(START_TEXT)


@authorised
async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(summary.build_summary(today_str(), with_flags=False))


@authorised
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(summary.build_summary(today_str()))


@authorised
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    material = " ".join(context.args).strip()
    if not material:
        rows = db.stock_balance()
        text = "\n".join(summary.format_stock(r["material"]) for r in {r["material"]: r for r in rows}.values()) \
            if rows else "No stock records yet."
        await update.message.reply_text(text + "\n\nTip: /stock cement")
        return
    await update.message.reply_text(summary.format_stock(material))


@authorised
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    removed = db.delete_last_entry(str(update.effective_user.id))
    if removed is None:
        await update.message.reply_text("Nothing to undo.")
        return
    if removed["was_synced"]:
        try:
            await asyncio.to_thread(sheets.remove_entry, removed["entry_id"])
        except Exception:
            log.exception("sheet removal failed for %s", removed["entry_id"])
    lines = [f"{r['task']}" + (f" {summary.fmt_qty(r['quantity'])} {r['unit'] or ''}".rstrip() if r['quantity'] is not None else "")
             for r in removed["progress"]]
    lines += [f"{r['material']} {summary.fmt_qty(r['quantity'])} {r['unit']} {r['direction']}" for r in removed["stock"]]
    await update.message.reply_text("↩️ Removed your last entry:\n" + "\n".join(lines))


@authorised
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending", None)
    context.user_data.pop("clarify_base", None)
    await update.message.reply_text("Cancelled. Send a new update anytime.")


# ---------------------------------------------------------------- message flow

@authorised
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    base = context.user_data.pop("clarify_base", None)
    # an answer to a clarifying question is re-extracted together with the original message
    message = f"{base}\n{text}" if base else text
    context.user_data.pop("pending", None)

    await update.message.chat.send_action("typing")
    try:
        result = await asyncio.to_thread(extract, message, today_str())
    except ExtractionError:
        log.exception("extraction failed")
        await update.message.reply_text("Couldn't understand, please resend.\nSamajh nahi aaya, dobara bhejo.")
        return

    if result.needs_clarification or result.is_empty():
        context.user_data["clarify_base"] = message
        await update.message.reply_text(
            result.needs_clarification or "Kya kaam hua, kitna, aur mazdoor kitne the? (What was done, how much, how many workers?)")
        return

    report_date = (result.report_date or datetime.now(config.TZ).date()).isoformat()
    context.user_data["pending"] = {"update": result.model_dump(mode="json"), "raw": message, "date": report_date}
    await update.message.reply_text(summary.format_update(result, report_date) + "\nCorrect?", reply_markup=KEYBOARD)


@authorised
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pending = context.user_data.get("pending")
    if not pending:
        await q.edit_message_text("This entry has expired. Please resend your message.")
        return

    if q.data == "cancel":
        context.user_data.pop("pending", None)
        await q.edit_message_text("❌ Cancelled. Nothing saved.")
    elif q.data == "edit":
        context.user_data.pop("pending", None)
        await q.edit_message_text("✏️ Send the corrected message.\nSahi message dobara bhejo.")
    elif q.data == "confirm":
        user = update.effective_user
        su = SiteUpdate.model_validate(pending["update"])
        db.insert_update(su, pending["date"], user.full_name, str(user.id), pending["raw"])
        context.user_data.pop("pending", None)
        await q.edit_message_text("✅ Saved\n" + summary.format_update(su, pending["date"]))
        try:  # SQLite already has the data; a failed sync only delays the Sheet
            log.info("sync: %s", await asyncio.to_thread(sheets.sync_pending))
        except Exception:
            log.exception("sheet sync failed; rows stay unsynced and will retry")


# ---------------------------------------------------------------- daily summary job

async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    text = summary.build_summary(today_str())
    for chat_id in config.SUMMARY_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id, text)
        except Exception:
            log.exception("could not send summary to %s", chat_id)
    try:  # retry any rows that failed to sync earlier
        await asyncio.to_thread(sheets.sync_pending)
    except Exception:
        log.exception("end-of-day sheet sync failed")


def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.GEMINI_API_KEY:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in .env (see .env.example)")
    if not config.ALLOWED_USER_IDS:
        log.warning("ALLOWED_USER_IDS is empty: every user will be rejected")

    db.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_button, pattern="^(confirm|edit|cancel)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    if app.job_queue is None:
        log.warning('JobQueue unavailable: pip install "python-telegram-bot[job-queue]" for the daily summary')
    else:
        app.job_queue.run_daily(send_daily_summary, time=config.SUMMARY_TIME, name="daily_summary")
        log.info("Daily summary scheduled at %s", config.SUMMARY_TIME.strftime("%H:%M %Z"))

    app.run_polling()


if __name__ == "__main__":
    main()
