import asyncio
import os
import subprocess
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

# CONFIG
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

NESCO_URL = "https://customer.nesco.gov.bd/pre/panel"
BD_TZ = pytz.timezone("Asia/Dhaka")

USER_DATA: Dict[int, Dict[str, str]] = {}
FETCH_SEMAPHORE = asyncio.Semaphore(2)

# Ensure Playwright browser binaries installed
try:
    subprocess.run(["playwright", "install", "chromium"], check=True)
except Exception as e:
    print("❌ Playwright install failed:", e)

# DEBUG fetch — logs HTML + network JSON
async def debug_fetch(account: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with FETCH_SEMAPHORE:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            contextp = await browser.new_context()
            page = await contextp.new_page()

            all_responses = []

            async def log_response(response):
                url = response.url
                try:
                    if "json" in response.headers.get("content-type", ""):
                        txt = await response.text()
                        all_responses.append((url, txt))
                except Exception:
                    pass

            page.on("response", log_response)

            await page.goto(NESCO_URL, timeout=60000)
            await page.wait_for_load_state("networkidle")

            # Try to find any input fields
            inputs = await page.locator("input").all_inner_texts()
            html = await page.content()

            # Log HTML snapshot
            await context.bot.send_message(
                update.effective_chat.id,
                "📄 Page HTML snapshot (first 3000 chars):\n" + html[:3000],
            )

            # Log network responses
            for url, txt in all_responses:
                msg = f"🔹 URL: {url}\nResponse snippet:\n{txt[:1000]}"
                await context.bot.send_message(update.effective_chat.id, msg)

            await browser.close()

# TELEGRAM HANDLERS
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Send your *NESCO prepaid account number* for debug.", parse_mode="Markdown")

async def receive_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text.strip()
    if not msg.isdigit():
        await update.message.reply_text("❌ Digits only please.")
        return

    await update.message.reply_text("🔍 Debugging… this will log HTML & JSON responses.")
    await debug_fetch(msg, update, context)
    await update.message.reply_text("✅ Done debugging — check logs above.")

# MAIN
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_account))
    app.run_polling()

if __name__ == "__main__":
    main()




