"""
Precious Peach NFT Tracker Bot
Monitors purchases of the Precious Peaches collection on TON
and sends real-time notifications to Telegram group.

Uses TON Center (toncenter.com) API — works without API key (1 req/s).

Required:
  - TELEGRAM_BOT_TOKEN   : bot token from @BotFather
  - TELEGRAM_GROUP_ID    : (OPTIONAL) if not set, bot auto-detects it
"""

import asyncio
import logging
import os
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
import httpx
from dotenv import load_dotenv
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Load environment variables
load_dotenv()

# --- GLOBAL CONSTANTS ---
TONCENTER_API = "https://toncenter.com/api/v2"

# ─── CONFIGURATION ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID  = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None

# Precious Peaches collection address on TON
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"

# Polling interval in seconds
POLL_INTERVAL = 12

# Local file to save last processed lt
STATE_FILE = "last_lt.txt"

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── AUTO-DETECT group ID ──────────────────────────────────────────────────
async def detect_group_id(bot: Bot) -> int:
    """Auto-detect Telegram group ID where bot is admin."""
    updates = await bot.get_updates(timeout=5)
    for update in updates:
        chat = None
        if update.message:
            chat = update.message.chat
        elif update.my_chat_member:
            chat = update.my_chat_member.chat
        if chat and chat.type in ("supergroup", "group"):
            log.info(f"Auto-detected group: {chat.title} (ID: {chat.id})")
            return chat.id

    raise RuntimeError(
        "No group found. Make sure bot is admin and someone has written in the group, "
        "or set TELEGRAM_GROUP_ID manually."
    )


# ─── PERSISTENT STATE ───────────────────────────────────────────────────────
def load_last_lt() -> int:
    """Load last processed Logical Time from file."""
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_last_lt(lt: int) -> None:
    """Save last processed Logical Time to file."""
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))


# ─── TON CENTER API – fetch transactions ───────────────────────────────────
async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    """Fetch transactions for a TON address."""
    try:
        params = {
            "address": address,
            "limit": limit,
            "archival": "false"
        }
        if to_lt:
            params["to_lt"] = to_lt
        
        url = f"{TONCENTER_API}/getTransactions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
    except Exception as e:
        logging.error(f"Error fetching transactions for {address}: {e}")
        return []


# ─── PARSING transactions to find NFT purchases ─────────────────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    """
    Find NFT purchase transactions where:
    - Incoming message is from external address (buyer)
    - Contains TON value > 0 (payment)
    - Has outgoing message to different address (NFT transfer)
    """
    purchases = []

    for tx in transactions:
        lt = tx.get("lt", 0)
        utime = tx.get("utime", 0)
        in_msg = tx.get("in_msg", {})
        out_messages = tx.get("out_messages", [])

        # Buyer is the source of incoming message
        buyer = in_msg.get("source", "")
        price_nanoton = int(in_msg.get("value", "0"))

        # If no value or source, not a purchase
        if price_nanoton == 0 or not buyer:
            continue

        # Find outgoing message with NFT transfer
        for out_msg in out_messages:
            dest = out_msg.get("destination", "")
            if dest and dest != COLLECTION_ADDRESS and dest != buyer:
                purchases.append({
                    "lt": lt,
                    "timestamp": utime,
                    "nft_address": dest,
                    "buyer": buyer,
                    "price_nanoton": price_nanoton,
                })
                break  # only one purchase per transaction

    return purchases


# ─── PROCESS single transaction ─────────────────────────────────────────────
async def process_transaction(tx: dict, bot: Bot) -> None:
    """Process a single transaction and send notification if it's a purchase."""
    # Parse purchases from this transaction
    purchases = parse_nft_purchases([tx])
    
    for purchase in purchases:
        # Format purchase message
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
        
        # Send to Telegram group
        try:
            await bot.send_message(
                chat_id=TELEGRAM_GROUP_ID,
                text=message,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            log.info(f"✅ Notification sent for purchase at LT: {purchase['lt']}")
        except TelegramError as e:
            log.error(f"❌ Error sending Telegram message: {e}")


# ─── TEST COMMAND HANDLERS ─────────────────────────────────────────────────
async def send_test_notification(chat_id: int, bot: Bot = None) -> bool:
    """Send a test notification to specified chat."""
    if bot is None:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    test_message = """
🧪 *TEST NOTIFICATION - Precious Peach Purchased!*
━━━━━━━━━━━━━━━━━━━━
🏷️ *NFT:* [Precious Peach #9999](https://getgems.io/test)
💰 *Price:* 99.9999 TON
🛒 *Buyer:* [EQBv4f...W3c7d](https://tonviewer.com/test)
🕐 *Time:* Now (Test)
━━━━━━━━━━━━━━━━━━━━
#Test #PreciousPeaches #TON
"""
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=test_message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        log.info(f"✅ Test notification sent to chat {chat_id}")
        return True
    except Exception as e:
        log.error(f"❌ Error sending test notification: {e}")
        return False


async def handle_telegram_commands():
    """Handle Telegram commands in parallel with polling."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # ─── /start command ───────────────────────────────────────────────────
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "🍑 *Precious Peaches Purchase Bot*\n\n"
            "I monitor NFT purchases and send notifications automatically.\n\n"
            "Commands:\n"
            "/test - Send test notification\n"
            "/status - Check bot status\n"
            "/help - Show this help",
            parse_mode="Markdown"
        )
    
    # ─── /test command ───────────────────────────────────────────────────
    async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test command with interactive buttons."""
        chat_type = update.message.chat.type
        
        if chat_type == "private":
            # In private chat: ask where to send
            keyboard = [
                [
                    InlineKeyboardButton("📢 Send to NOTIFICATION GROUP", callback_data="test_group"),
                    InlineKeyboardButton("💬 Send HERE", callback_data="test_here")
                ]
            ]
            await update.message.reply_text(
                "Where should I send the test notification?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Already in a group: send here
            await update.message.reply_text("Sending test notification to this group...")
            success = await send_test_notification(update.message.chat.id)
            if success:
                await update.message.reply_text("✅ Test notification sent!")
            else:
                await update.message.reply_text("❌ Failed to send test notification.")
    
    # ─── /status command ─────────────────────────────────────────────────
    async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        try:
            last_lt = load_last_lt()
            status_msg = (
                f"🤖 *Bot Status*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Running on Render\n"
                f"🔄 Polling every {POLL_INTERVAL}s\n"
                f"🎯 Collection: `{COLLECTION_ADDRESS[:20]}...`\n"
                f"📊 Notification Group: {TELEGRAM_GROUP_ID}\n"
                f"⏱️ Last LT: {last_lt}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Use /test to send a test notification."
            )
            await update.message.reply_text(status_msg, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting status: {str(e)}")
    
    # ─── /help command ───────────────────────────────────────────────────
    async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await start_command(update, context)  # Same as start
    
    # ─── Button callback handler ──────────────────────────────────────────
    async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks."""
        query = update.callback_query
        await query.answer()
        
        if query.data == "test_group":
            # Send to main notification group
            await query.edit_message_text("Sending test to notification group...")
            success = await send_test_notification(TELEGRAM_GROUP_ID)
            if success:
                await query.edit_message_text("✅ Test sent to notification group!")
            else:
                await query.edit_message_text("❌ Failed to send to group.")
        
        elif query.data == "test_here":
            # Send to current chat
            await query.edit_message_text("Sending test here...")
            success = await send_test_notification(query.message.chat.id)
            if success:
                await query.edit_message_text("✅ Test sent here!")
            else:
                await query.edit_message_text("❌ Failed to send test.")
    
    # Add all handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Start command polling
    log.info("🤖 Telegram command handler started")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)


# ─── MAIN POLLING LOOP ─────────────────────────────────────────────────────
async def polling_loop(bot: Bot):
    """Main transaction polling loop."""
    last_processed_lt = load_last_lt()
    
    # FIRST EXECUTION: calibration
    if last_processed_lt == 0:
        log.info("🎯 First execution - initial calibration...")
        
        try:
            # First call WITHOUT to_lt to get latest transactions
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=10, to_lt=None)
            
            if transactions:
                # Find latest LT (Logical Time)
                lts = []
                for tx in transactions:
                    tx_id = tx.get("transaction_id", {})
                    lt = tx_id.get("lt")
                    if lt:
                        lts.append(int(lt))
                
                if lts:
                    last_processed_lt = max(lts)
                    save_last_lt(last_processed_lt)
                    log.info(f"✅ Calibration completed. Last LT: {last_processed_lt}")
                    
                    # DON'T send notifications for existing transactions
                    log.info("⏭️ Skipped notifications for existing transactions")
                else:
                    log.warning("⚠️ No LT found in transactions")
                    last_processed_lt = int(time.time() * 1000)  # Fallback to current timestamp
            else:
                log.info("📭 No transactions found for collection")
                last_processed_lt = int(time.time() * 1000)
                
        except Exception as e:
            log.error(f"❌ Error during calibration: {e}")
            last_processed_lt = int(time.time() * 1000)
            save_last_lt(last_processed_lt)
    
    log.info(f"🚀 Polling started. Last processed LT: {last_processed_lt}")
    
    # Main loop
    while True:
        try:
            # Use to_lt only if > 0
            to_lt_param = last_processed_lt if last_processed_lt > 0 else None
            
            transactions = await fetch_transactions(
                COLLECTION_ADDRESS, 
                limit=100, 
                to_lt=to_lt_param
            )
            
            if transactions:
                # Sort by LT ascending (oldest to newest)
                transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
                
                new_last_lt = last_processed_lt
                
                for tx in transactions:
                    tx_id = tx.get("transaction_id", {})
                    current_lt = int(tx_id.get("lt", 0))
                    
                    # Process only NEW transactions
                    if current_lt > last_processed_lt:
                        await process_transaction(tx, bot)
                        new_last_lt = max(new_last_lt, current_lt)
                
                # Update last processed LT
                if new_last_lt > last_processed_lt:
                    last_processed_lt = new_last_lt
                    save_last_lt(last_processed_lt)
                    log.info(f"📈 Updated last LT to: {last_processed_lt}")
            
            # Wait before next poll
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f"❌ Error in polling loop: {e}")
            await asyncio.sleep(10)  # Short pause on error


# ─── MAIN ENTRY POINT ──────────────────────────────────────────────────────
async def main() -> None:
    """Main async entry point."""
    global TELEGRAM_GROUP_ID

    # Initialize bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    log.info(f"Bot connected as: {me.first_name} (@{me.username})")

    # Auto-detect group if not set
    if TELEGRAM_GROUP_ID is None:
        log.info("TELEGRAM_GROUP_ID not set — auto-detecting...")
        TELEGRAM_GROUP_ID = await detect_group_id(bot)

    log.info(f"Target group ID: {TELEGRAM_GROUP_ID}")
    
    # Send startup message to group
    try:
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text="🤖 *Bot Started Successfully!*\n\n"
                 "I'm now monitoring Precious Peaches collection for purchases.\n"
                 "Notifications will be sent here automatically.",
            parse_mode="Markdown"
        )
        log.info("✅ Startup message sent to group")
    except Exception as e:
        log.warning(f"Could not send startup message: {e}")
    
    # Run both polling and command handlers in parallel
    polling_task = asyncio.create_task(polling_loop(bot))
    commands_task = asyncio.create_task(handle_telegram_commands())
    
    # Wait for both tasks (they should run forever)
    await asyncio.gather(polling_task, commands_task)


if __name__ == "__main__":
    # Run the async main function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    except Exception as e:
        log.error(f"Fatal error: {e}")
