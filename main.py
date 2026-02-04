"""
Precious Peach NFT Tracker Bot
Monitors purchases of the Precious Peaches collection on TON
and sends real-time notifications to the Telegram group.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import httpx
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# --- CONFIGURATION ---
TONCENTER_API = "https://toncenter.com/api/v2"
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"
POLL_INTERVAL = 12
STATE_FILE = "last_lt.txt"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── PERSISTENT STATE ──────────────────────────────────────────────────────
def load_last_lt() -> int:
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0

def save_last_lt(lt: int) -> None:
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))

# ─── TON CENTER API ────────────────────────────────────────────────────────
async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    try:
        params = {
            "address": address,
            "limit": limit,
            "archival": "false"
        }
        if to_lt and to_lt > 0:
            params["to_lt"] = to_lt
        
        url = f"{TONCENTER_API}/getTransactions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json().get("result", [])
    except Exception as e:
        log.error(f"Error fetching transactions: {e}")
        return []

# ─── PROCESS TRANSACTIONS ──────────────────────────────────────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    purchases = []
    
    for tx in transactions:
        in_msg = tx.get("in_msg", {})
        out_msgs = tx.get("out_msgs", [])
        
        buyer = in_msg.get("source", "")
        price_nanoton = int(in_msg.get("value", "0"))
        
        if price_nanoton == 0 or not buyer:
            continue
        
        for out_msg in out_msgs:
            dest = out_msg.get("destination", "")
            if dest and dest != COLLECTION_ADDRESS and dest != buyer:
                purchases.append({
                    "lt": tx.get("transaction_id", {}).get("lt", 0),
                    "timestamp": tx.get("utime", 0),
                    "nft_address": dest,
                    "buyer": buyer,
                    "price_nanoton": price_nanoton,
                })
                break
    
    return purchases

async def process_transaction(tx: dict, bot: Bot, group_id: int):
    purchases = parse_nft_purchases([tx])
    
    for purchase in purchases:
        price_ton = purchase["price_nanoton"] / 1_000_000_000
        time_str = datetime.fromtimestamp(purchase["timestamp"], tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
        
        nft_addr = purchase["nft_address"]
        buyer_addr = purchase["buyer"]
        
        nft_link = f"https://getgems.io/nft/{nft_addr}"
        buyer_link = f"https://tonviewer.com/{buyer_addr}"
        
        def shorten(addr: str) -> str:
            return addr[:6] + "…" + addr[-4:] if len(addr) > 12 else addr
        
        message = (
            f"🍑 *Precious Peach Purchased!*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏷️ *NFT:* [Precious Peach]({nft_link})\n"
            f"💰 *Price:* {price_ton:.4f} TON\n"
            f"🛒 *Buyer:* [{shorten(buyer_addr)}]({buyer_link})\n"
            f"🕐 *Time:* {time_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        
        try:
            await bot.send_message(
                chat_id=group_id,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            log.info(f"✅ Notification sent")
        except Exception as e:
            log.error(f"❌ Telegram error: {e}")

# ─── COMMAND HANDLERS ──────────────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "🍑 *Precious Peaches Purchase Bot*\n\n"
        "I monitor NFT purchases and send notifications automatically.\n\n"
        "Commands:\n"
        "/test - Send test notification\n"
        "/status - Check bot status",
        parse_mode="Markdown"
    )
    log.info("/start command executed")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a test notification."""
    price_ton = 85.0
    time_str = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    
    message = (
        f"🍑 *Precious Peach Purchased! (TEST)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *NFT:* Precious Peach #42\n"
        f"💰 *Price:* {price_ton:.4f} TON\n"
        f"🛒 *Buyer:* EQD9Xc…aB3f\n"
        f"🕐 *Time:* {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    
    chat_id = update.effective_chat.id
    await context.bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode="Markdown"
    )
    log.info(f"/test command executed in chat {chat_id}")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send bot status information."""
    last_lt = load_last_lt()
    status_msg = (
        f"🤖 *Bot Status*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Running\n"
        f"🔄 Polling every {POLL_INTERVAL}s\n"
        f"🎯 Collection: `{COLLECTION_ADDRESS[:20]}...`\n"
        f"📊 Chat ID: {update.effective_chat.id}\n"
        f"⏱️ Last LT: {last_lt}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(
        status_msg,
        parse_mode="Markdown"
    )
    log.info(f"/status command executed in chat {update.effective_chat.id}")

# ─── POLLING LOOP ──────────────────────────────────────────────────────────
async def polling_loop(bot: Bot, group_id: int, application: Application):
    last_processed_lt = load_last_lt()
    
    # Initial calibration
    if last_processed_lt == 0:
        log.info("🎯 First execution - calibration...")
        try:
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=10, to_lt=None)
            if transactions:
                lts = []
                for tx in transactions:
                    lt = tx.get("transaction_id", {}).get("lt")
                    if lt:
                        lts.append(int(lt))
                
                if lts:
                    last_processed_lt = max(lts)
                    save_last_lt(last_processed_lt)
                    log.info(f"✅ Calibration complete. Last LT: {last_processed_lt}")
                    log.info("⏭️ Skipping existing transactions")
        except Exception as e:
            log.error(f"❌ Calibration error: {e}")
    
    log.info(f"🚀 Polling started. Last LT: {last_processed_lt}")
    
    # Main loop
    while True:
        try:
            # Check for new transactions
            to_lt_param = last_processed_lt if last_processed_lt > 0 else None
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=100, to_lt=to_lt_param)
            
            if transactions:
                transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
                
                new_last_lt = last_processed_lt
                
                for tx in transactions:
                    current_lt = int(tx.get("transaction_id", {}).get("lt", 0))
                    if current_lt > last_processed_lt:
                        await process_transaction(tx, bot, group_id)
                        new_last_lt = max(new_last_lt, current_lt)
                
                if new_last_lt > last_processed_lt:
                    last_processed_lt = new_last_lt
                    save_last_lt(last_processed_lt)
                    log.info(f"📈 Updated last LT: {last_processed_lt}")
            
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f"❌ Polling error: {e}")
            await asyncio.sleep(10)

# ─── MAIN FUNCTION ─────────────────────────────────────────────────────────
async def main():
    global TELEGRAM_GROUP_ID
    
    # Create Application and set up commands
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # Set bot commands for menu
    await application.bot.set_my_commands([
        ("start", "Start the bot"),
        ("test", "Send test notification"),
        ("status", "Check bot status")
    ])
    
    me = await application.bot.get_me()
    log.info(f"Bot connected: {me.first_name} (@{me.username})")
    
    # Auto-detect group if needed
    if not TELEGRAM_GROUP_ID:
        log.info("Auto-detecting group...")
        try:
            updates = await application.bot.get_updates(timeout=5)
            for update in updates:
                if update.message and update.message.chat.type in ("supergroup", "group"):
                    TELEGRAM_GROUP_ID = update.message.chat.id
                    log.info(f"Auto-detected group ID: {TELEGRAM_GROUP_ID}")
                    break
        except Exception as e:
            log.error(f"Error detecting group: {e}")
    
    if not TELEGRAM_GROUP_ID:
        log.error("❌ No group ID found! Set TELEGRAM_GROUP_ID env var.")
        return
    
    log.info(f"Target group ID: {TELEGRAM_GROUP_ID}")
    
    # Send startup message
    try:
        await application.bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text="🤖 *Bot Started*\nMonitoring Precious Peaches collection...",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"Could not send startup message: {e}")
    
    # Start the bot (for commands)
    await application.initialize()
    await application.start()
    
    # Start polling loop (for NFT monitoring)
    polling_task = asyncio.create_task(
        polling_loop(application.bot, TELEGRAM_GROUP_ID, application)
    )
    
    try:
        # Run application until stopped
        await application.updater.start_polling()
        await polling_task
    finally:
        await application.stop()

if __name__ == "__main__":
    asyncio.run(main())
