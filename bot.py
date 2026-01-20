import os
import asyncio
from playwright.async_api import async_playwright
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Load environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")  # your Telegram ID
ACCOUNT_NUMBER = os.environ.get("ACCOUNT_NUMBER")  # NESCO account number

bot = Bot(token=TELEGRAM_TOKEN)

async def fetch_balance(account_number: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://customer.nesco.gov.bd/pre/panel")
        
        # Fill account number
        await page.fill('input[name="customer_id"]', account_number)
        await page.click('button[type="submit"]')
        
        # Wait for balance to appear
        await page.wait_for_selector("text=অবশিষ্ট ব্যালেন্স")
        balance_element = await page.query_selector("xpath=//*[contains(text(),'অবশিষ্ট ব্যালেন্স (টাকা)')]/following-sibling::*")
        balance = await balance_element.text_content() if balance_element else "Balance not found"
        
        await browser.close()
        return balance.strip()

async def send_balance(context: ContextTypes.DEFAULT_TYPE):
    balance = await fetch_balance(ACCOUNT_NUMBER)
    await bot.send_message(chat_id=CHAT_ID, text=f"Your NESCO balance is: {balance} ৳")

async def start(update, context):
    await update.message.reply_text("Hi! I will send you your NESCO balance twice a day.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    # Schedule balance notifications: 10am & 10pm
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(send_balance(None)), 'cron', hour=10, minute=0)
    scheduler.add_job(lambda: asyncio.create_task(send_balance(None)), 'cron', hour=22, minute=0)
    scheduler.start()

    print("Bot is running...")
    app.run_polling()
