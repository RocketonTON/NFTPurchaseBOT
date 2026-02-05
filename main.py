"""
Precious Peach NFT Tracker Bot - Real-time Commands
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import httpx
from telegram import Bot
from telegram.error import TelegramError

# --- CONFIGURATION ---
TONCENTER_API = "https://toncenter.com/api/v2"
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"
POLL_INTERVAL = 15  # 15 seconds for NFT checks
COMMAND_CHECK_INTERVAL = 2  # 2 seconds for commands! 🚀

STATE_FILE = "last_lt.txt"
UPDATE_ID_FILE = "last_update_id.txt"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.environ.get("TELEGRAM_GROUP_ID")

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

def load_last_update_id() -> int:
    try:
        with open(UPDATE_ID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return -1

def save_last_update_id(update_id: int) -> None:
    with open(UPDATE_ID_FILE, "w") as f:
        f.write(str(update_id))

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

# ─── PROCESS NFT TRANSACTIONS ──────────────────────────────────────────────
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
            log.info(f"✅ NFT notification sent")
        except Exception as e:
            log.error(f"❌ Telegram error: {e}")

# ─── REAL-TIME COMMAND HANDLER ────────────────────────────────────────────
async def check_commands_realtime(bot: Bot):
    """Check for commands in REAL-TIME (every 2 seconds)"""
    try:
        last_id = load_last_update_id()
        
        # Get ONLY new updates with short timeout
        updates = await bot.get_updates(
            offset=last_id + 1, 
            timeout=1,  # Breve timeout per risposta veloce
            limit=10
        )
        
        for update in updates:
            if not update.message or not update.message.text:
                continue
                
            text = update.message.text.strip()
            chat_id = update.message.chat.id
            
            # Check if this is a command for our bot
            bot_username = (await bot.get_me()).username
            
            # In private chat: all commands are for us
            if update.message.chat.type == "private":
                is_for_our_bot = text.startswith("/")
            # In group: only if bot is mentioned or simple commands
            else:
                is_for_our_bot = (
                    f"@{bot_username}" in text or 
                    text in ["/start", "/test", "/status", "/help"]
                )
            
            if not is_for_our_bot or not text.startswith("/"):
                continue
            
            # ─── PROCESS COMMAND ──────────────────────────────────────
            if "/start" in text.lower() or "/help" in text.lower():
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "🍑 *Precious Peaches Purchase Bot*\n\n"
                        "I automatically notify when someone buys a Precious Peach NFT.\n\n"
                        "📋 *Commands:*\n"
                        "/test - Send a test notification\n"
                        "/status - Check bot status\n"
                        "/help - Show this message"
                    ),
                    parse_mode="Markdown"
                )
                log.info(f"✅ /start or /help in chat {chat_id}")
            
            elif "/test" in text.lower():
                test_msg = (
                    f"🍑 *Test Notification*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Bot is working!\n"
                    f"🕐 {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=test_msg,
                    parse_mode="Markdown"
                )
                log.info(f"✅ /test in chat {chat_id}")
            
            elif "/status" in text.lower():
                last_lt = load_last_lt()
                status_msg = (
                    f"🤖 *Bot Status*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Running\n"
                    f"🔄 NFT check: every {POLL_INTERVAL}s\n"
                    f"⚡ Command check: every {COMMAND_CHECK_INTERVAL}s\n"
                    f"⏱️ Last LT: {last_lt}\n"
                    f"📊 Group ID: {TELEGRAM_GROUP_ID}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=status_msg,
                    parse_mode="Markdown"
                )
                log.info(f"✅ /status in chat {chat_id}")
            
            # Save update ID IMMEDIATELY
            if update.update_id > last_id:
                last_id = update.update_id
                save_last_update_id(last_id)
                
    except Exception as e:
        log.error(f"Command check error: {e}")

# ─── DUAL LOOP SYSTEM ─────────────────────────────────────────────────────
async def nft_polling_loop(bot: Bot, group_id: int):
    """NFT polling loop (every 15 seconds)"""
    last_processed_lt = load_last_lt()
    
    # Calibration
    if last_processed_lt == 0:
        log.info("🔧 Calibrating NFT tracker...")
        try:
            txs = await fetch_transactions(COLLECTION_ADDRESS, limit=5)
            if txs:
                lts = [int(tx.get("transaction_id", {}).get("lt", 0)) for tx in txs if tx.get("transaction_id", {}).get("lt")]
                if lts:
                    last_processed_lt = max(lts)
                    save_last_lt(last_processed_lt)
                    log.info(f"✅ Calibrated to LT: {last_processed_lt}")
        except Exception as e:
            log.error(f"Calibration error: {e}")
    
    log.info(f"🎯 NFT polling started. Last LT: {last_processed_lt}")
    
    while True:
        try:
            # Check for new NFT transactions
            to_lt = last_processed_lt if last_processed_lt > 0 else None
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=50, to_lt=to_lt)
            
            if transactions:
                transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
                
                highest_lt = last_processed_lt
                
                for tx in transactions:
                    current_lt = int(tx.get("transaction_id", {}).get("lt", 0))
                    if current_lt > last_processed_lt:
                        await process_transaction(tx, bot, group_id)
                        highest_lt = max(highest_lt, current_lt)
                
                if highest_lt > last_processed_lt:
                    last_processed_lt = highest_lt
                    save_last_lt(last_processed_lt)
                    log.info(f"📈 New NFT transaction! LT: {last_processed_lt}")
            
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f"❌ NFT polling error: {e}")
            await asyncio.sleep(10)

async def command_check_loop(bot: Bot):
    """Real-time command checking loop (every 2 seconds)"""
    log.info(f"⚡ Command loop started (every {COMMAND_CHECK_INTERVAL}s)")
    
    while True:
        try:
            await check_commands_realtime(bot)
            await asyncio.sleep(COMMAND_CHECK_INTERVAL)
        except Exception as e:
            log.error(f"❌ Command loop error: {e}")
            await asyncio.sleep(5)

# ─── MAIN FUNCTION ─────────────────────────────────────────────────────────
async def main():
    # Initialize bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    log.info(f"🤖 Bot: {me.first_name} (@{me.username})")
    
    # Get group ID
    if not TELEGRAM_GROUP_ID:
        log.info("🔍 Looking for group ID...")
        try:
            updates = await bot.get_updates(timeout=3, limit=5)
            for update in updates:
                if update.message and update.message.chat.type in ["group", "supergroup"]:
                    TELEGRAM_GROUP_ID = update.message.chat.id
                    log.info(f"✅ Found group: {TELEGRAM_GROUP_ID}")
                    break
        except Exception as e:
            log.error(f"Group detection error: {e}")
    
    if not TELEGRAM_GROUP_ID:
        log.error("❌ ERROR: No TELEGRAM_GROUP_ID found!")
        log.error("Set TELEGRAM_GROUP_ID environment variable")
        return
    
    log.info(f"🎯 Target group: {TELEGRAM_GROUP_ID}")
    
    # Send startup message
    try:
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text="🤖 *Bot Started Successfully!*\n\n✅ NFT monitoring active\n✅ Commands ready\n✅ Real-time updates enabled",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"⚠️ Could not send startup message: {e}")
    
    # Run BOTH loops concurrently
    nft_task = asyncio.create_task(nft_polling_loop(bot, TELEGRAM_GROUP_ID))
    command_task = asyncio.create_task(command_check_loop(bot))
    
    # Wait for both tasks (they run forever)
    await asyncio.gather(nft_task, command_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot stopped by user")
    except Exception as e:
        log.error(f"💥 Fatal error: {e}")
