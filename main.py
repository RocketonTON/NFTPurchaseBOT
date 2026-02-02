"""
Precious Peach NFT Tracker Bot
Monitors purchases of the Precious Peaches collection on TON
and sends real-time notifications to the Telegram group.

Uses TON Center (toncenter.com) as API — works without key (1 req/s).

Requirements:
  - TELEGRAM_BOT_TOKEN   : bot token from @BotFather
  - TELEGRAM_GROUP_ID    : (OPTIONAL) if not set, the bot will auto-detect it
"""

import asyncio
import logging
import os
import json
import time
from typing import List, Dict, Optional
from datetime import datetime, timezone
import httpx
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# --- GLOBAL CONSTANTS ---
TONCENTER_API = "https://toncenter.com/api/v2"

# ─── CONFIGURATION ───────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID  = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None

# Precious Peaches collection address on TON
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"

# Polling interval in seconds
POLL_INTERVAL = 12

# Local file to save the last processed lt
STATE_FILE = "last_lt.txt"

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── AUTO-DETECT GROUP ID ────────────────────────────────────────────────────
async def detect_group_id(bot: Bot) -> int:
    updates = await bot.get_updates(timeout=5)
    for update in updates:
        chat = None
        if update.message:
            chat = update.message.chat
        elif update.my_chat_member:
            chat = update.my_chat_member.chat
        if chat and chat.type in ("supergroup", "group"):
            log.info(f"Group found automatically: {chat.title} (ID: {chat.id})")
            return chat.id

    raise RuntimeError(
        "No group found. Make sure the bot is an admin "
        "and someone has written in the group, or set TELEGRAM_GROUP_ID manually."
    )


# ─── PERSISTENT STATE ────────────────────────────────────────────────────────
def load_last_lt() -> int:
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_last_lt(lt: int) -> None:
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))


# ─── TON CENTER – FETCH TRANSACTIONS ─────────────────────────────────────────
TONCENTER_API = "https://toncenter.com/api/v2"

async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    """
    Fetches transactions for a TON address.
    """
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


async def fetch_nft_data(nft_address: str) -> dict | None:
    """
    Fetches NFT item data using getNftData.
    Returns dict with init, index, collection_address, owner_address, individual_data.
    """
    url = f"{TONCENTER_API}/getNftData"
    params = {"address": nft_address}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        log.warning(f"Error fetching NFT data: {e}")
    return None


# ─── PARSE TRANSACTIONS FOR NFT PURCHASES ────────────────────────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    """
    Looks for transactions where:
    - The incoming message is from an external address (the buyer)
    - Contains a TON value > 0 (the payment)
    - There's at least one outgoing message to an address different from the collection
      (the NFT item transfer to the new owner)
    """
    purchases = []

    for tx in transactions:
        lt = tx.get("lt", 0)
        utime = tx.get("utime", 0)
        in_msg = tx.get("in_msg", {})
        out_messages = tx.get("out_messages", [])

        # The buyer is the source of the incoming message
        buyer = in_msg.get("source", "")
        price_nanoton = int(in_msg.get("value", "0"))

        # If there's no value or source, it's not a purchase
        if price_nanoton == 0 or not buyer:
            continue

        # Look for the NFT item transfer in outgoing messages
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
                break  # one purchase per transaction

    return purchases


# ─── TELEGRAM MESSAGE FORMATTING ─────────────────────────────────────────────
def format_purchase_message(purchase: dict, nft_name: str = "Precious Peach") -> str:
    ts = purchase["timestamp"]
    time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    price_ton = purchase["price_nanoton"] / 1_000_000_000

    nft_addr = purchase["nft_address"]
    buyer_addr = purchase["buyer"]

    nft_link   = f"https://getgems.io/nft/{nft_addr}"
    buyer_link = f"https://tonviewer.com/{buyer_addr}"

    def shorten(addr: str) -> str:
        return addr[:6] + "…" + addr[-4:] if len(addr) > 12 else addr

    msg = (
        f"🍑 *Precious Peach Purchased!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *NFT:* [{nft_name}]({nft_link})\n"
        f"💰 *Price:* {price_ton:.4f} TON\n"
        f"🛒 *Buyer:* [{shorten(buyer_addr)}]({buyer_link})\n"
        f"🕐 *Time:* {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


# ─── SEND MESSAGE TO GROUP ───────────────────────────────────────────────────
async def send_to_group(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=text,
            parse_mode="Markdown",
        )
        log.info("Message sent to group.")
    except TelegramError as e:
        log.error(f"Error sending Telegram message: {e}")


# ─── /test COMMAND ───────────────────────────────────────────────────────────
async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a test message when someone writes /test in the group."""
    fake_purchase = {
        "timestamp": int(datetime.now(timezone.utc).timestamp()),
        "nft_address": "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8",
        "buyer": "EQD9XcPkrT1qJ3HXz5vnKbiYUBpWXQOe3dEUCcaB3f",
        "price_nanoton": 85_000_000_000,  # 85 TON
    }
    
    msg = format_purchase_message(fake_purchase, "Precious Peach #42 (TEST)")
    await update.message.reply_text(msg, parse_mode="Markdown")
    log.info(f"/test command executed by {update.effective_user.first_name}")


# ─── PROCESS SINGLE TRANSACTION ──────────────────────────────────────────────
async def process_transaction(tx: dict, bot: Bot) -> None:
    """Processes a single transaction and sends notification if it's a purchase."""
    purchases = parse_nft_purchases([tx])
    
    for purchase in purchases:
        # Try to fetch the NFT number
        nft_name = "Precious Peach"
        nft_data = await fetch_nft_data(purchase["nft_address"])
        if nft_data:
            idx = nft_data.get("index")
            if idx is not None:
                nft_name = f"Precious Peach #{idx}"

        msg = format_purchase_message(purchase, nft_name)
        log.info(
            f"New purchase – NFT: {purchase['nft_address']}, "
            f"Price: {purchase['price_nanoton'] / 1e9:.4f} TON"
        )
        await send_to_group(bot, msg)


# ─── MAIN POLLING LOOP ───────────────────────────────────────────────────────
async def polling_loop(bot: Bot):
    """Main transaction polling loop."""
    last_processed_lt = load_last_lt()
    
    # FIRST EXECUTION: calibration
    if last_processed_lt == 0:
        logging.info("🎯 First execution - initial calibration...")
        
        try:
            # First call WITHOUT to_lt to get the most recent transactions
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=10, to_lt=None)
            
            if transactions:
                # Find the latest LT (Logical Time)
                lts = []
                for tx in transactions:
                    tx_id = tx.get("transaction_id", {})
                    lt = tx_id.get("lt")
                    if lt:
                        lts.append(int(lt))
                
                if lts:
                    last_processed_lt = max(lts)
                    save_last_lt(last_processed_lt)
                    logging.info(f"✅ Calibration completed. Last LT: {last_processed_lt}")
                    
                    # DO NOT send notifications for old transactions
                    logging.info("⏭️  Skipped notifications for existing transactions")
                else:
                    logging.warning("⚠️  No LT found in transactions")
                    last_processed_lt = int(time.time() * 1000)  # Fallback to current timestamp
            else:
                logging.info("📭 No transactions found for collection")
                last_processed_lt = int(time.time() * 1000)
                
        except Exception as e:
            logging.error(f"❌ Error during calibration: {e}")
            last_processed_lt = int(time.time() * 1000)
            save_last_lt(last_processed_lt)
    
    logging.info(f"🚀 Polling started. Last processed LT: {last_processed_lt}")
    
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
                # Sort by ascending LT (oldest to newest)
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
                    logging.info(f"📈 Updated last LT to: {last_processed_lt}")
            
            # Wait before next poll
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            logging.error(f"❌ Error in polling loop: {e}")
            await asyncio.sleep(10)  # Short pause on error

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
async def main() -> None:
    global TELEGRAM_GROUP_ID

    # Create Application to handle commands
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot = app.bot

    me = await bot.get_me()
    log.info(f"Bot connected as: {me.first_name} (@{me.username})")

    if TELEGRAM_GROUP_ID is None:
        log.info("TELEGRAM_GROUP_ID not set — auto-detecting...")
        TELEGRAM_GROUP_ID = await detect_group_id(bot)

    log.info(f"Target group ID: {TELEGRAM_GROUP_ID}")

    # Add /test command
    app.add_handler(CommandHandler("test", test_command))

    # Start Application in background
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Start NFT monitoring loop
    await polling_loop(bot)


if __name__ == "__main__":
    asyncio.run(main())
