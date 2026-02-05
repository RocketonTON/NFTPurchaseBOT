"""
Precious Peach NFT Tracker Bot - No recursion version
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import httpx
from telegram import Bot

# --- CONFIGURATION ---
TONCENTER_API = "https://toncenter.com/api/v2"
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"
POLL_INTERVAL = 15
COMMAND_CHECK_INTERVAL = 2

STATE_FILE = "last_lt.txt"
UPDATE_ID_FILE = "last_update_id.txt"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.environ.get("TELEGRAM_GROUP_ID")

# Cache per username del bot
_bot_username_cache = None

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── UTILITY FUNCTIONS ─────────────────────────────────────────────────────
async def get_bot_username(bot: Bot) -> str:
    """Get bot username with cache to avoid repeated API calls"""
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await bot.get_me()
        _bot_username_cache = me.username
        log.info(f"🤖 Bot username cached: @{_bot_username_cache}")
    return _bot_username_cache

def load_last_lt() -> int:
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_last_lt(lt: int) -> None:
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))

def load_last_update_id() -> int:
    try:
        with open(UPDATE_ID_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return -1

def save_last_update_id(update_id: int) -> None:
    with open(UPDATE_ID_FILE, "w") as f:
        f.write(str(update_id))

# ─── TON CENTER API ────────────────────────────────────────────────────────
async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    try:
        params = {"address": address, "limit": limit, "archival": "false"}
        if to_lt and to_lt > 0:
            params["to_lt"] = to_lt
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{TONCENTER_API}/getTransactions", params=params)
            return resp.json().get("result", [])
    except Exception as e:
        log.error(f"API error: {e}")
        return []

# ─── NFT PROCESSING ────────────────────────────────────────────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    purchases = []
    for tx in transactions:
        in_msg = tx.get("in_msg", {})
        out_msgs = tx.get("out_msgs", [])
        
        buyer = in_msg.get("source", "")
        price = int(in_msg.get("value", "0"))
        
        if price == 0 or not buyer:
            continue
        
        for out_msg in out_msgs:
            dest = out_msg.get("destination", "")
            if dest and dest != COLLECTION_ADDRESS and dest != buyer:
                purchases.append({
                    "lt": tx.get("transaction_id", {}).get("lt", 0),
                    "timestamp": tx.get("utime", 0),
                    "nft_address": dest,
                    "buyer": buyer,
                    "price_nanoton": price,
                })
                break
    return purchases

async def send_nft_notification(purchase: dict, bot: Bot, group_id: int):
    price_ton = purchase["price_nanoton"] / 1_000_000_000
    time_str = datetime.fromtimestamp(purchase["timestamp"], tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    
    nft_link = f"https://getgems.io/nft/{purchase['nft_address']}"
    buyer_link = f"https://tonviewer.com/{purchase['buyer']}"
    
    def shorten(addr: str) -> str:
        return addr[:6] + "…" + addr[-4:] if len(addr) > 12 else addr
    
    message = (
        f"🍑 *Precious Peach Purchased!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *NFT:* [Precious Peach]({nft_link})\n"
        f"💰 *Price:* {price_ton:.4f} TON\n"
        f"🛒 *Buyer:* [{shorten(purchase['buyer'])}]({buyer_link})\n"
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
        log.info("✅ NFT notification sent")
    except Exception as e:
        log.error(f"❌ Send error: {e}")

# ─── COMMAND HANDLING ──────────────────────────────────────────────────────
async def check_commands(bot: Bot):
    """Check for new commands - optimized version"""
    try:
        last_id = load_last_update_id()
        updates = await bot.get_updates(offset=last_id + 1, timeout=1, limit=10)
        
        if not updates:
            return
        
        # Get username ONCE (cached)
        bot_username = await get_bot_username(bot)
        
        for update in updates:
            if not update.message or not update.message.text:
                continue
            
            text = update.message.text.strip()
            chat_id = update.message.chat.id
            
            # Skip non-commands
            if not text.startswith("/"):
                continue
            
            # Check if command is for our bot
            is_for_us = False
            
            if update.message.chat.type == "private":
                # In private chat, all commands are for us
                is_for_us = True
            else:
                # In group: check if bot is mentioned or it's a basic command
                if f"@{bot_username}" in text:
                    is_for_us = True
                elif text in ["/start", "/test", "/status", "/help"]:
                    is_for_us = True
                # If it starts with / but doesn't mention any bot, assume it's for us
                elif "@" not in text:
                    is_for_us = True
            
            if not is_for_us:
                continue
            
            # Process command
            if "/start" in text.lower() or "/help" in text.lower():
                await bot.send_message(
                    chat_id=chat_id,
                    text="🍑 *Precious Peaches Bot*\n\nCommands: /test /status",
                    parse_mode="Markdown"
                )
                log.info(f"✅ /start in chat {chat_id}")
            
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
                    f"🔄 Polling every {POLL_INTERVAL}s\n"
                    f"⏱️ Last LT: {last_lt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=status_msg,
                    parse_mode="Markdown"
                )
                log.info(f"✅ /status in chat {chat_id}")
            
            # Save update ID
            if update.update_id > last_id:
                last_id = update.update_id
                save_last_update_id(last_id)
                
    except Exception as e:
        log.error(f"Command error: {e}")

# ─── MAIN LOOPS ────────────────────────────────────────────────────────────
async def nft_loop(bot: Bot, group_id: int):
    """NFT monitoring loop"""
    last_lt = load_last_lt()
    
    if last_lt == 0:
        log.info("🔧 Calibrating NFT tracker...")
        try:
            txs = await fetch_transactions(COLLECTION_ADDRESS, limit=5)
            if txs:
                lts = []
                for tx in txs:
                    lt = tx.get("transaction_id", {}).get("lt")
                    if lt:
                        lts.append(int(lt))
                if lts:
                    last_lt = max(lts)
                    save_last_lt(last_lt)
                    log.info(f"✅ Calibrated: LT {last_lt}")
        except Exception as e:
            log.error(f"Calibration error: {e}")
    
    log.info(f"🎯 NFT monitoring started (LT: {last_lt})")
    
    while True:
        try:
            to_lt = last_lt if last_lt > 0 else None
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=50, to_lt=to_lt)
            
            if transactions:
                transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
                highest_lt = last_lt
                
                for tx in transactions:
                    current_lt = int(tx.get("transaction_id", {}).get("lt", 0))
                    if current_lt > last_lt:
                        purchases = parse_nft_purchases([tx])
                        for purchase in purchases:
                            await send_nft_notification(purchase, bot, group_id)
                        highest_lt = max(highest_lt, current_lt)
                
                if highest_lt > last_lt:
                    last_lt = highest_lt
                    save_last_lt(last_lt)
                    log.info(f"📈 New LT: {last_lt}")
            
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f"NFT loop error: {e}")
            await asyncio.sleep(10)

async def command_loop(bot: Bot):
    """Command checking loop"""
    log.info("⚡ Command loop started")
    while True:
        try:
            await check_commands(bot)
            await asyncio.sleep(COMMAND_CHECK_INTERVAL)
        except Exception as e:
            log.error(f"Command loop error: {e}")
            await asyncio.sleep(5)

# ─── MAIN FUNCTION ─────────────────────────────────────────────────────────
async def main():
    """Main function - no recursion"""
    log.info("🚀 Starting Precious Peach Bot...")
    
    # Check environment variables
    if not TELEGRAM_BOT_TOKEN:
        log.error("❌ TELEGRAM_BOT_TOKEN not set!")
        return
    
    # Convert TELEGRAM_GROUP_ID to int if provided
    if TELEGRAM_GROUP_ID:
        try:
            target_group_id = int(TELEGRAM_GROUP_ID)
            log.info(f"✅ Using provided group ID: {target_group_id}")
        except ValueError:
            log.error(f"❌ Invalid TELEGRAM_GROUP_ID: {TELEGRAM_GROUP_ID}")
            return
    else:
        # Try to auto-detect
        log.info("🔍 Auto-detecting group ID...")
        temp_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        try:
            updates = await temp_bot.get_updates(timeout=5, limit=10)
            for update in updates:
                if update.message and update.message.chat.type in ["group", "supergroup"]:
                    target_group_id = update.message.chat.id
                    log.info(f"✅ Auto-detected group: {target_group_id}")
                    break
            else:
                log.error("❌ No group found in updates")
                return
        except Exception as e:
            log.error(f"❌ Auto-detect error: {e}")
            return
    
    # Initialize main bot instance
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    # Pre-cache username
    await get_bot_username(bot)
    
    # Send startup message
    try:
        await bot.send_message(
            chat_id=target_group_id,
            text="🤖 *Bot Restarted Successfully!*\n\n✅ NFT monitoring active\n✅ Commands ready",
            parse_mode="Markdown"
        )
        log.info(f"✅ Startup message sent to group {target_group_id}")
    except Exception as e:
        log.error(f"⚠️ Could not send startup message: {e}")
        # Continue anyway
    
    # Run both loops
    log.info("🔄 Starting main loops...")
    try:
        await asyncio.gather(
            nft_loop(bot, target_group_id),
            command_loop(bot)
        )
    except KeyboardInterrupt:
        log.info("👋 Bot stopped by user")
    except Exception as e:
        log.error(f"💥 Fatal error in main loops: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        import traceback
        traceback.print_exc()