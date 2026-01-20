import asyncio
import os
import pytz
from datetime import time
from typing import Dict

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =============================
# CONFIG
# =============================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

NESCO_URL = "https://customer.nesco.gov.bd/pre/panel"
BD_TZ = pytz.timezone("Asia/Dhaka")

LOW_BALANCE_THRESHOLD = 100.0  # BDT
FETCH_TIMEOUT = 45
RETRY_ATTEMPTS = 3
RETRY_DELAY = 15

# in-memory storage: chat_id → {"account": str}
USER_DATA: Dict[int, Dict[str, str]] = {}
FETCH_SEMAPHORE = asyncio.Semaphore(2)


# =============================
# FETCH BALANCE (NETWORK INTERCEPT)
# =============================

async def fetch_nesco_balance(account: str) -> float:
    """
    Open NESCO prepaid panel, enter the account number,
    intercept any network response likely containing balance,
    and return remaining balance.
    """
    balance_value: float = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context()
        page = await context.new_page()

        # Catch network responses
        async def handle_response(response):
            nonlocal balance_value
            try:
                url = response.url
                # Heuristic: NESCO prepaid API likely includes "prepaid" or "balance" in request/URL
                if "prepaid" in url.lower() or "balance" in url.lower():
                    json_data = await response.json()
                    # try to extract a numeric balance field
                    for key in ("remainingBalance", "balance", "remaining_balance", "value"):
                        if key in json_data:
                            val = json_data[key]
                            # ensure numeric
                            try:
                                balance_value = float(val)
                            except Exception:
                                pass
            except Exception:
                # ignore parse errors
                pass

        # Register response listener
        page.on("response", handle_response)

        try:
            await page.goto(NESCO_URL, timeout=30_000)
            # Wait for input where user types account number
            await page.wait_for_selector("input", timeout=20_000)

            # Fill number and submit
            await page.fill("input", account)
            await page.keyboard.press("Enter")

            # Wait for network/API
            await page.wait_for_timeout(10_000)

        finally:
            await browser.close()

    if balance_value is None:
        raise RuntimeError("Balance not found from API")

    return balance_value


async def fetch_with_retry(account: str) -> float:
    """
    Retry logic around fetch_nesco_balance to make it robust.
    """
    async with FETCH_SEMAPHORE:
        last_error = None
        for _ in range(RETRY_ATTEMPTS):
            try:
                return await asyncio.wait_for(
                    fetch_nesco_balance(account),
                    timeout=FETCH_TIMEOUT,
                )
            except (PWTimeout, asyncio.TimeoutError):
                last_error = "Timeout while fetching balance"
            except Exception as e:
                last_error = str(e)
            await asyncio.sleep(RETRY_DELAY)
        raise RuntimeError(f"NESCO failed repeatedly ({last_error})")


# =============================
# HELP TEXT
# =============================

HELP_TEXT = (
    "📌 *NESCO Balance Bot*\n\n"
    "/start — Start the bot\n"
    "/balance — Check balance now\n"
    "/help — Show help\n\n"
    "*Automatic alerts:*\n"
    "• 🌅 Morning (10:00 AM)\n"
    "• 🌙 Evening (10:00 PM)\n"
    "• ⏱ Every 10 minutes\n"
    "• 🚨 Low balance alert\n"
)


# =============================
# TELEGRAM COMMANDS
# =============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_DATA.setdefault(chat_id, {})
    await update.message.reply_text(
        "👋 Welcome to NESCO Balance Bot!\n"
        "Send your *NESCO prepaid account number* (digits only).",
        parse_mode="Markdown",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def receive_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg = update.message.text.strip()

    if not msg.isdigit():
        await update.message.reply_text("❌ Only digits allowed. Send your account number.")
        return

    USER_DATA.setdefault(chat_id, {})["account"] = msg
    await update.message.reply_text(
        f"✅ Account saved: *{msg}*\n\nYou can now use /balance anytime.",
        parse_mode="Markdown",
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = USER_DATA.get(chat_id)
    if not data or "account" not in data:
        await update.message.reply_text("❗ Send your account number first.")
        return

    await update.message.reply_text("🔄 Fetching your balance…")
    try:
        bal = await fetch_with_retry(data["account"])
        await update.message.reply_text(
            f"💡 *Remaining Balance:* {bal:.2f} BDT",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")


# =============================
# SCHEDULED JOBS
# =============================

async def broadcast(context: ContextTypes.DEFAULT_TYPE, title: str):
    for chat_id, data in list(USER_DATA.items()):
        account = data.get("account")
        if not account:
            continue
        try:
            bal = await fetch_with_retry(account)
            await context.bot.send_message(
                chat_id,
                f"{title}\n💡 *Remaining Balance:* {bal:.2f} BDT",
                parse_mode="Markdown",
            )
        except Exception:
            pass


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
            pass


# =============================
# MAIN
# =============================

async def post_init(app):
    await app.bot.delete_webhook(drop_pending_updates=True)


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

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

