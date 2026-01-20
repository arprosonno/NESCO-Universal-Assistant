import asyncio
import os
import re
from datetime import time
from typing import Dict

import pytz
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =================================================
# CONFIG
# =================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

NESCO_URL = "https://customer.nesco.gov.bd/pre/panel"
BD_TZ = pytz.timezone("Asia/Dhaka")

LOW_BALANCE_THRESHOLD = 100

FETCH_TIMEOUT = 50
RETRY_ATTEMPTS = 3
RETRY_DELAY = 15

USER_DATA: Dict[int, Dict[str, str]] = {}
FETCH_SEMAPHORE = asyncio.Semaphore(2)

# =================================================
# NESCO SCRAPER
# =================================================

async def fetch_nesco_balance(account: str) -> float:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = await browser.new_page()

        try:
            await page.goto(NESCO_URL, timeout=30_000)

            # Wait for input
            await page.wait_for_selector("input", timeout=20_000)

            # Fill account number
            await page.fill("input", account)
            await page.keyboard.press("Enter")

            # VERY IMPORTANT: wait for JS rendering
            await page.wait_for_timeout(8_000)

            # Try to capture balance number (digits only)
            text = await page.inner_text("body")

        finally:
            await browser.close()

    # Match numbers like: 1,234.56 or 123.45
    match = re.search(r"([\d,]+\.\d+)", text)
    if not match:
        raise RuntimeError("Balance not found")

    return float(match.group(1).replace(",", ""))


async def fetch_with_retry(account: str) -> float:
    last_error = None

    async with FETCH_SEMAPHORE:
        for _ in range(RETRY_ATTEMPTS):
            try:
                return await asyncio.wait_for(
                    fetch_nesco_balance(account),
                    timeout=FETCH_TIMEOUT,
                )
            except (PWTimeout, asyncio.TimeoutError):
                last_error = "Timeout while fetching NESCO"
            except Exception as e:
                last_error = str(e)

            await asyncio.sleep(RETRY_DELAY)

    raise RuntimeError("⚠️ NESCO unreachable repeatedly") from None

# =================================================
# HELP TEXT
# =================================================

HELP_TEXT = (
    "📌 *NESCO Balance Bot*\n\n"
    "/start — Start bot\n"
    "/balance — Check balance\n"
    "/help — Help menu\n\n"
    "*Automatic alerts:*\n"
    "• 🌅 10:00 AM\n"
    "• 🌙 10:00 PM\n"
    "• 🔔 Every 10 minutes\n"
    "• 🚨 Low balance alert\n"
)

# =================================================
# COMMANDS
# =================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_DATA.setdefault(chat_id, {})
    await update.message.reply_text(
        "👋 Welcome!\n\nSend your *NESCO prepaid account number*.",
        parse_mode="Markdown",
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def receive_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message.text.strip()

    if not msg.isdigit():
        await update.message.reply_text("❌ Digits only. Send valid account number.")
        return

    USER_DATA.setdefault(chat_id, {})["account"] = msg

    await update.message.reply_text(
        f"✅ Account saved: *{msg}*\n\nAutomatic alerts enabled.",
        parse_mode="Markdown",
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = USER_DATA.get(chat_id)

    if not data or "account" not in data:
        await update.message.reply_text("❗ Send your account number first.")
        return

    await update.message.reply_text("🔄 Checking balance...")
    try:
        bal = await fetch_with_retry(data["account"])
        await update.message.reply_text(
            f"💡 Remaining Balance: *{bal:.2f} BDT*",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(str(e))

# =================================================
# SCHEDULED JOBS
# =================================================

async def broadcast(context: ContextTypes.DEFAULT_TYPE, title: str):
    for chat_id, data in list(USER_DATA.items()):
        account = data.get("account")
        if not account:
            continue

        try:
            bal = await fetch_with_retry(account)
            await context.bot.send_message(
                chat_id,
                f"{title}\n💡 Remaining Balance: *{bal:.2f} BDT*",
                parse_mode="Markdown",
            )
        except Exception:
            continue

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    await broadcast(context, "🌅 Good Morning!")

async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    await broadcast(context, "🌙 Good Evening!")

async def ten_min_job(context: ContextTypes.DEFAULT_TYPE):
    await broadcast(context, "🔔 10-Minute Update")

async def low_balance_job(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, data in list(USER_DATA.items()):
        account = data.get("account")
        if not account:
            continue

        try:
            bal = await fetch_with_retry(account)
            if bal < LOW_BALANCE_THRESHOLD:
                await context.bot.send_message(
                    chat_id,
                    f"🚨 *LOW BALANCE ALERT!*\nRemaining: {bal:.2f} BDT",
                    parse_mode="Markdown",
                )
        except Exception:
            continue

# =================================================
# MAIN
# =================================================

async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    print("✅ Telegram session cleaned")

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_account))

    jq = app.job_queue
    jq.run_daily(morning_job, time=time(10, 0, tzinfo=BD_TZ))
    jq.run_daily(evening_job, time=time(22, 0, tzinfo=BD_TZ))
    jq.run_repeating(ten_min_job, interval=600, first=600)
    jq.run_repeating(low_balance_job, interval=300, first=300)

    print("💓 NESCO Bot alive")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
