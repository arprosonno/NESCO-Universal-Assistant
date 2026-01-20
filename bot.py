import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from playwright.async_api import async_playwright
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ==========================
# CONFIG
# ==========================
BOT_TOKEN = "TELEGRAM_BOT_TOKEN"
LOW_BALANCE = 100

logging.basicConfig(level=logging.INFO)

USER_METERS = {}        # chat_id -> meter_no
LOW_ALERT_ACTIVE = {}  # chat_id -> bool


# ==========================
# SCRAPER
# ==========================
async def fetch_balance(meter_no: str) -> float:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page()

        try:
            await page.goto(
                "https://customer.nesco.gov.bd/pre/panel",
                timeout=30000,
            )

            await page.wait_for_selector("input", timeout=20000)
            await page.fill("input", meter_no)
            await page.keyboard.press("Enter")

            await page.wait_for_selector("text=অবশিষ্ট ব্যালেন্স", timeout=30000)

            balance_input = await page.query_selector(
                "xpath=//label[contains(text(),'অবশিষ্ট ব্যালেন্স')]/following::input[1]"
            )

            value = await balance_input.input_value()

        finally:
            await browser.close()

    return float(value.strip().replace(",", ""))


async def safe_fetch(meter):
    for _ in range(3):
        try:
            return await fetch_balance(meter)
        except Exception as e:
            logging.warning(e)
            await asyncio.sleep(3)
    raise RuntimeError("NESCO failed repeatedly")


# ==========================
# COMMANDS
# ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to NESCO Balance Bot\n\n"
        "Commands:\n"
        "/balance – check balance\n"
        "/help – help menu\n\n"
        "Send your meter number first."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Commands\n\n"
        "/start – Start bot\n"
        "/balance – Check balance\n"
        "/help – Help\n\n"
        "Bot also responds to hi / hello"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in USER_METERS:
        await update.message.reply_text("❌ Please send your meter number first.")
        return

    try:
        bal = await safe_fetch(USER_METERS[chat_id])
        await update.message.reply_text(f"💡 Balance: {bal} Tk")
    except Exception as e:
        await update.message.reply_text(f"⚠️ {e}")


async def greeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if text.isdigit():
        USER_METERS[chat_id] = text
        LOW_ALERT_ACTIVE[chat_id] = False
        await update.message.reply_text("✅ Meter number saved.")
        return

    await balance(update, context)


# ==========================
# SCHEDULED JOBS
# ==========================
async def ten_min_check(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, meter in USER_METERS.items():
        try:
            bal = await safe_fetch(meter)
            await context.bot.send_message(
                chat_id, f"🔔 Balance update: {bal} Tk"
            )
        except Exception as e:
            await context.bot.send_message(chat_id, f"⚠️ {e}")


async def low_balance_check(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, meter in USER_METERS.items():
        try:
            bal = await safe_fetch(meter)
            if bal < LOW_BALANCE:
                await context.bot.send_message(
                    chat_id,
                    f"🚨 LOW BALANCE ALERT!\nRemaining: {bal} Tk",
                )
        except:
            pass


async def morning_evening(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, meter in USER_METERS.items():
        try:
            bal = await safe_fetch(meter)
            await context.bot.send_message(
                chat_id,
                f"⏰ Scheduled Update\nBalance: {bal} Tk",
            )
        except:
            pass


# ==========================
# MAIN
# ==========================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, greeting)
    )

    scheduler = AsyncIOScheduler(timezone="Asia/Dhaka")

    scheduler.add_job(ten_min_check, "interval", minutes=10, args=[app.bot])
    scheduler.add_job(low_balance_check, "interval", minutes=5, args=[app.bot])
    scheduler.add_job(morning_evening, "cron", hour=10, minute=0, args=[app.bot])
    scheduler.add_job(morning_evening, "cron", hour=22, minute=0, args=[app.bot])

    scheduler.start()

    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())




