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
FETCH_TIMEOUT = 45          # hard kill (seconds)
RETRY_ATTEMPTS = 3
RETRY_DELAY = 15            # seconds

# chat_id -> {"account": str}
USER_DATA: Dict[int, Dict[str, str]] = {}

# global semaphore to prevent scheduler pile-up
FETCH_SEMAPHORE = asyncio.Semaphore(3)

# =================================================
# ENSURE PLAYWRIGHT BROWSER
# =================================================
import subprocess
import shutil

def ensure_playwright_browser():
    chrome_path = shutil.which("chromium") or shutil.which("google-chrome")
    if chrome_path:
        return
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception as e:
        print("❌ Failed to install Playwright browser:", e)
        raise

ensure_playwright_browser()

# =================================================
# NESCO SCRAPER
# =================================================
async def fetch_nesco_balance(account: str) -> float:
    last_error = None
    async with FETCH_SEMAPHORE:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    page = await browser.new_page()
                    await page.goto(NESCO_URL, timeout=30_000)
                    await page.wait_for_selector("input", timeout=20_000)
                    await page.fill("input[name='account']", account)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(8000)  # wait for JS to load balance

                    # Get the parent element of "অবশিষ্ট ব্যালেন্স (টাকা)"
                    elem = await page.locator("text=অবশিষ্ট ব্যালেন্স").locator("..").inner_text()
                    await browser.close()

                    digits = "".join(c for c in elem if c.isdigit() or c == ".")
                    return float(digits)
            except (PWTimeout, asyncio.TimeoutError):
                last_error = "Timeout while fetching NESCO"
            except Exception as e:
                last_error = str(e)

            await asyncio.sleep(RETRY_DELAY)

    raise RuntimeError(f"NESCO failed repeatedly: {last_error}")

# =================================================
# HELP TEXT
# =================================================
HELP_TEXT = (
    "📌 *NESCO Balance Bot*\n\n"
    "/start — Start the bot\n"
    "/balance — Check balance now\n"
    "/help — Show help menu\n\n"
    "*Automatic alerts:*\n"
    "• 🌅 10:00 AM\n"
    "• 🌙 10:00 PM\n"
    "• 🚨 Low balance alert (<100 BDT)\n"
)

# =================================================
# COMMANDS
# =================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_DATA.setdefault(chat_id, {})
    await update.message.reply_text(
        "👋 Welcome!\n\nSend your *NESCO account number*.",
        parse_mode="Markdown",
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def receive_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message.text.strip()

    if msg.lower() in {"hi", "hello", "hey"}:
        await update.message.reply_text("👋 Hello! Use /help to see options.")
        return

    if not msg.isdigit():
        await update.message.reply_text("❌ Digits only. Send your account number.")
        return

    USER_DATA.setdefault(chat_id, {})["account"] = msg

    await update.message.reply_text(
        f"✅ Account saved: *{msg}*\nAutomatic alerts enabled.\nUse /balance anytime.",
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
        bal = await fetch_nesco_balance(data["account"])
        await update.message.reply_text(
            f"💡 Remaining Balance: *{bal:.2f} BDT*",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")

# =================================================
# SCHEDULED JOBS
# =================================================
async def broadcast(context: ContextTypes.DEFAULT_TYPE, title: str):
    for chat_id, data in list(USER_DATA.items()):
        account = data.get("account")
        if not account:
            continue
        try:
            bal = await fetch_nesco_balance(account)
            await context.bot.send_message(
                chat_id,
                f"{title}\n💡 Remaining Balance: *{bal:.2f} BDT*",
                parse_mode="Markdown",
            )
        except Exception:
            continue

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await broadcast(context, "🌅 Good Morning!")
    except Exception:
        pass

async def evening_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        await broadcast(context, "🌙 Good Evening!")
    except Exception:
        pass

async def low_balance_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        for chat_id, data in list(USER_DATA.items()):
            account = data.get("account")
            if not account:
                continue
            bal = await fetch_nesco_balance(account)
            if bal < LOW_BALANCE_THRESHOLD:
                await context.bot.send_message(
                    chat_id,
                    f"🚨 *LOW BALANCE ALERT!*\nRemaining: {bal:.2f} BDT",
                    parse_mode="Markdown",
                )
    except Exception:
        pass

# =================================================
# TELEGRAM SESSION CLEANUP
# =================================================
async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)
    print("✅ Telegram session cleaned")

# =================================================
# MAIN
# =================================================
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
    jq.run_repeating(low_balance_job, interval=300, first=300)

    print("💓 Bot alive — hardened scheduler running")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()


