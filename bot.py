import os
import asyncio
from playwright.async_api import async_playwright
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")  # optional default chat for scheduled messages

bot = Bot(token=TELEGRAM_TOKEN)

async def fetch_balance(account_number: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://customer.nesco.gov.bd/pre/panel")
        
        # Enter account number
        await page.fill('input[name="customer_id"]', account_number)
        await page.click('button[type="submit"]')
        
        # Wait for balance element
        await page.wait_for_selector("text=অবশিষ্ট ব্যালেন্স")
        balance_element = await page.query_selector(
            "xpath=//*[contains(text(),'অবশিষ্ট ব্যালেন্স (টাকা)')]/following-sibling::*"
        )
        balance = await balance_element.text_content() if balance_element else "Balance not found"
        
        await browser.close()
        return balance.strip()

# Command: /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Send /balance <account_number> to get your NESCO balance."
    )

# Command: /balance <account_number>
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Please provide an account number: /balance <account_number>")
        return
    
    account_number = context.args[0]
    await update.message.reply_text(f"Fetching balance for account: {account_number}...")
    
    try:
        balance = await fetch_balance(account_number)
        await update.message.reply_text(f"Account {account_number} balance: {balance} ৳")
    except Exception as e:
        await update.message.reply_text(f"Failed to fetch balance: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_command))

    print("Bot is running...")
    app.run_polling()


