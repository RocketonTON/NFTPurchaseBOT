"""
Precious Peach NFT Tracker Bot - WITH DEBUG LOGGING
Monitors purchases and logs detailed information for troubleshooting
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
import httpx
from telegram import Bot

# --- CONFIGURATION ---
TONCENTER_API = "https://toncenter.com/api/v3"
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"
POLL_INTERVAL = 15
COMMAND_CHECK_INTERVAL = 2

STATE_FILE = "last_lt.txt"
UPDATE_ID_FILE = "last_update_id.txt"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_ID = os.environ.get("TELEGRAM_GROUP_ID")

_bot_username_cache = None

# Setup logging with DEBUG level to see all information
logging.basicConfig(
    level=logging.DEBUG,  # DEBUG level to see all logs
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── DEBUG UTILITIES ──────────────────────────────────────────────────────
def debug_transaction(tx: dict):
    """Log detailed transaction information for debugging"""
    tx_id = tx.get("transaction_id", {})
    log.debug(f"🔍 Transaction Details:")
    log.debug(f"   LT: {tx_id.get('lt', 'N/A')}")
    log.debug(f"   Hash: {tx_id.get('hash', 'N/A')[:20]}...")
    
    in_msg = tx.get("in_msg", {})
    if in_msg:
        log.debug(f"   📥 IN: {in_msg.get('source', 'N/A')[:10]}... → {in_msg.get('destination', 'N/A')[:10]}...")
        log.debug(f"   💰 Value: {int(in_msg.get('value', '0')) / 1e9:.4f} TON")
    
    out_msgs = tx.get("out_msgs", [])
    if out_msgs:
        log.debug(f"   📤 OUT messages: {len(out_msgs)}")
        for i, out_msg in enumerate(out_msgs):
            dest = out_msg.get("destination", "")
            log.debug(f"     {i+1}. To: {dest[:10]}...")
            log.debug(f"        Op code: {out_msg.get('op_code', 'N/A')}")
            # Check if this could be an NFT
            if dest and dest != COLLECTION_ADDRESS:
                log.debug(f"        ⚠️ Potential NFT: {dest[:10]}...")

async def get_bot_username(bot: Bot) -> str:
    """Get bot username with cache"""
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await bot.get_me()
        _bot_username_cache = me.username
        log.info(f"🤖 Bot username cached: @{_bot_username_cache}")
    return _bot_username_cache

def load_last_lt() -> int:
    """Load last processed logical time with debug logging"""
    try:
        with open(STATE_FILE, "r") as f:
            value = int(f.read().strip())
            log.debug(f"📖 Loaded last_lt from file: {value}")
            return value
    except FileNotFoundError:
        log.debug("📖 last_lt.txt not found, using 0")
        return 0
    except ValueError:
        log.debug("📖 last_lt.txt contains invalid data, using 0")
        return 0

def save_last_lt(lt: int) -> None:
    """Save last processed logical time with debug logging"""
    log.info(f"💾 SAVING new last_lt to file: {lt}")
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))
    log.debug(f"✅ Successfully saved last_lt: {lt}")

def load_last_update_id() -> int:
    """Load last processed Telegram update ID"""
    try:
        with open(UPDATE_ID_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return -1

def save_last_update_id(update_id: int) -> None:
    """Save last processed Telegram update ID"""
    with open(UPDATE_ID_FILE, "w") as f:
        f.write(str(update_id))

# ─── TON CENTER API ────────────────────────────────────────────────────────
async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    """Fetch transactions from TON Center API v3 with debug logging"""
    try:
        # Costruisci l'URL per l'API V3
        url = f"{TONCENTER_API}/transactions"
        
        # Parametri per l'API V3 (account invece di address, niente più archival)
        params = {
            "account": address,  # <-- PARAMETRO CAMBIATO: account invece di address
            "limit": limit
        }
        if to_lt and to_lt > 0:
            params["before_lt"] = to_lt  # <-- PARAMETRO OPZIONALE CAMBIATO: before_lt invece di to_lt
            log.debug(f"🌐 API V3 Request with before_lt: {to_lt}")
        else:
            log.debug(f"🌐 API V3 Request (latest transactions)")
        
        log.debug(f"🌐 Requesting {limit} transactions for account: {address[:20]}...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            # L'API V3 restituisce le transazioni in una chiave "transactions"
            transactions = data.get("transactions", [])
            log.debug(f"🌐 API V3 Response: {len(transactions)} transactions received")
            return transactions
            
    except httpx.RequestError as e:
        log.error(f"🌐 Network error fetching transactions: {e}")
        return []
    except Exception as e:
        log.error(f"🌐 API V3 error: {e}")
        return []

def reset_state_if_outdated(api_transactions: list, current_last_lt: int) -> int:
    """
    Controlla se il last_lt salvato è troppo alto rispetto ai dati ricevuti.
    Se sì, resetta automaticamente alla transazione più recente disponibile.
    """
    if not api_transactions:
        return current_last_lt
    
    # Trova il LT più alto nelle transazioni ricevute dall'API
    latest_api_lt = max(int(tx.get("transaction_id", {}).get("lt", 0)) for tx in api_transactions)
    
    # Se il nostro last_lt è troppo più alto (es. > 1000 LT oltre il massimo dell'API)
    # probabilmente è uno stato corrotto e va resettato
    if current_last_lt > latest_api_lt and (current_last_lt - latest_api_lt) > 1000:
        log.warning(f"⚠️ Stato corroretto rilevato!")
        log.warning(f"   last_lt salvato: {current_last_lt}")
        log.warning(f"   Ultimo LT disponibile dall'API: {latest_api_lt}")
        log.warning(f"   Differenza: {current_last_lt - latest_api_lt} LT")
        log.warning(f"   ⚙️ Auto-resettando last_lt a {latest_api_lt}")
        
        # Resetta al massimo disponibile
        save_last_lt(latest_api_lt)
        return latest_api_lt
    
    return current_last_lt

# ─── NFT PURCHASE PARSING ─────────────────────────────────────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    """
    Parse transactions to identify NFT purchases
    Returns empty list if no NFT purchases found
    """
    purchases = []
    
    log.debug(f"🔍 Starting parse_nft_purchases on {len(transactions)} transactions")
    
    for tx_idx, tx in enumerate(transactions):
        in_msg = tx.get("in_msg", {})
        out_msgs = tx.get("out_msgs", [])
        
        buyer = in_msg.get("source", "")
        price_nanoton = int(in_msg.get("value", "0"))
        
        log.debug(f"  Transaction {tx_idx+1}:")
        log.debug(f"    Buyer address: {buyer[:10]}..." if buyer else "    No buyer address")
        log.debug(f"    Payment amount: {price_nanoton / 1e9:.4f} TON")
        log.debug(f"    Outgoing messages: {len(out_msgs)}")
        
        # Skip transactions with no payment
        if price_nanoton == 0:
            log.debug("    ⏭️ Skipped: No payment detected")
            continue
        
        # Skip transactions with no buyer
        if not buyer:
            log.debug("    ⏭️ Skipped: No buyer address")
            continue
        
        nft_found_in_transaction = False
        
        for out_idx, out_msg in enumerate(out_msgs):
            dest = out_msg.get("destination", "")
            
            log.debug(f"    Checking out message {out_idx+1}:")
            log.debug(f"      Destination: {dest[:10]}..." if dest else "      No destination")
            
            if not dest:
                log.debug("      ⏭️ Skipped: No destination address")
                continue
            
            # Check if this could be an NFT transfer
            is_different_from_collection = dest != COLLECTION_ADDRESS
            is_different_from_buyer = dest != buyer
            
            log.debug(f"      Different from collection? {is_different_from_collection}")
            log.debug(f"      Different from buyer? {is_different_from_buyer}")
            
            if is_different_from_collection and is_different_from_buyer:
                purchases.append({
                    "lt": tx.get("transaction_id", {}).get("lt", 0),
                    "timestamp": tx.get("utime", 0),
                    "nft_address": dest,
                    "buyer": buyer,
                    "price_nanoton": price_nanoton,
                })
                nft_found_in_transaction = True
                log.debug(f"      ✅ NFT identified: {dest[:10]}...")
                log.debug(f"      ✅ Price: {price_nanoton / 1e9:.4f} TON")
                break  # Assume one NFT per transaction
        
        if not nft_found_in_transaction:
            log.debug("    📭 No NFT found in this transaction")
    
    log.debug(f"🎯 parse_nft_purchases result: Found {len(purchases)} NFT purchase(s)")
    return purchases

async def send_nft_notification(purchase: dict, bot: Bot, group_id: int):
    """Send Telegram notification for an NFT purchase"""
    price_ton = purchase["price_nanoton"] / 1_000_000_000
    time_str = datetime.fromtimestamp(purchase["timestamp"], tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    
    nft_addr = purchase["nft_address"]
    buyer_addr = purchase["buyer"]
    
    nft_link = f"https://getgems.io/nft/{nft_addr}"
    buyer_link = f"https://tonviewer.com/{buyer_addr}"
    
    def shorten(addr: str) -> str:
        """Shorten address for display"""
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
        log.info(f"📤 Sending notification for NFT: {shorten(nft_addr)}")
        log.debug(f"   Buyer: {shorten(buyer_addr)}")
        log.debug(f"   Price: {price_ton:.4f} TON")
        log.debug(f"   Time: {time_str}")
        
        await bot.send_message(
            chat_id=group_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        log.info(f"✅ Notification sent for {shorten(nft_addr)}")
    except Exception as e:
        log.error(f"❌ Failed to send notification: {e}")

# ─── COMMAND HANDLING ──────────────────────────────────────────────────────
async def check_commands(bot: Bot):
    """Check for and process new Telegram commands with debug logging"""
    try:
        last_id = load_last_update_id()
        log.info(f"🔄 COMMAND CHECK STARTED - Last update ID: {last_id}")  # MODIFICATO: log.info invece di debug
        
        updates = await bot.get_updates(offset=last_id + 1, timeout=10, limit=100)  # MODIFICATO: timeout 10, limit 100
        
        log.info(f"📥 get_updates() returned {len(updates)} update(s)")  # MODIFICATO: log.info invece di debug
        
        if not updates:
            log.debug("📭 No new updates for commands")
            return
        
        log.debug(f"📥 Received {len(updates)} update(s) for commands")
        bot_username = await get_bot_username(bot)
        
        for update in updates:
            if not update.message or not update.message.text:
                log.debug("📭 Update has no message text")
                continue
            
            text = update.message.text.strip()
            chat_id = update.message.chat.id
            chat_type = update.message.chat.type
            
            log.info(f"📩 Message in {chat_type} chat {chat_id}: {text[:50]}...")  # MODIFICATO: log.info invece di debug
            
            if not text.startswith("/"):
                log.debug("⏭️ Not a command, skipping")
                continue
            
            is_for_us = False
            
            if chat_type == "private":
                is_for_us = True
                log.debug("✅ Private chat - command is for us")
            else:
                if f"@{bot_username}" in text:
                    is_for_us = True
                    log.debug(f"✅ Command mentions @{bot_username}")
                elif text in ["/start", "/test", "/status", "/help"]:
                    is_for_us = True
                    log.debug("✅ Basic command in group")
                elif "@" not in text:
                    is_for_us = True
                    log.debug("✅ Command without @mention, assuming for us")
                else:
                    log.debug("⏭️ Command for another bot")
            
            if not is_for_us:
                continue
            
            # Process the command
            if "/start" in text.lower() or "/help" in text.lower():
                log.info(f"📝 Processing /start or /help command in chat {chat_id}")  # MODIFICATO
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
                log.info(f"📝 Processing /test command in chat {chat_id}")  # MODIFICATO
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
                log.info(f"📝 Processing /status command in chat {chat_id}")  # MODIFICATO
                last_lt = load_last_lt()
                status_msg = (
                    f"🤖 *Bot Status*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Running\n"
                    f"🔄 NFT check: every {POLL_INTERVAL}s\n"
                    f"⚡ Command check: every {COMMAND_CHECK_INTERVAL}s\n"
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
                log.info(f"💾 Saved new last update ID: {last_id}")  # MODIFICATO
                
    except Exception as e:
        log.error(f"❌ Command handling error: {e}")
        import traceback
        log.error(f"❌ Traceback: {traceback.format_exc()}")

# ─── NFT MONITORING LOOP (WITH DEBUG) ────────────────────────────────────
async def nft_polling_loop(bot: Bot, group_id: int):
    """
    Main loop for monitoring NFT purchases with detailed debug logging
    
    Updates last_lt ONLY when an NFT purchase is detected
    """
    last_lt = load_last_lt()
    
    log.info("🎯 STARTING NFT MONITORING LOOP")
    log.info(f"📊 Initial last_lt value: {last_lt}")
    
    # Counter for periodic logging
    check_counter = 0
    
    while True:
        try:
            await asyncio.sleep(0)  # AGGIUNTO: Yield per permettere command loop di funzionare
            
            check_counter += 1
            
            # Log status periodically
            if check_counter % 20 == 0:  # Every ~5 minutes
                log.info(f"🔍 NFT monitor still running - Check #{check_counter}")
                log.info(f"📊 Current last_lt: {last_lt}")
            
            log.debug(f"🔄 NFT Check #{check_counter}")
            
            # Fetch transactions from API - INDENTAZIONE CORRETTA
            log.debug(f"📡 Requesting transactions for collection: {COLLECTION_ADDRESS[:20]}...")
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=30, to_lt=None)

            if transactions:
                last_lt = reset_state_if_outdated(transactions, last_lt)
            
            if not transactions:
                log.info(f"📭 Polling #{check_counter}: API returned NO transactions")
                log.debug("ℹ️ This could mean: 1) No recent transactions, 2) API issue, 3) Wrong address")
                await asyncio.sleep(POLL_INTERVAL)
                continue
            
            # ✅ NUOVO LOG: Conferma che sta leggendo le transazioni
            log.info(f"📥 Polling #{check_counter}: Reading {len(transactions)} transaction(s) from API")
            
            # Log aggiuntivo: mostra gli indirizzi coinvolti
            if len(transactions) > 0:
                tx_sources = set()
                for tx in transactions[:3]:  # Prime 3 transazioni
                    in_msg = tx.get("in_msg", {})
                    if in_msg and in_msg.get("source"):
                        tx_sources.add(in_msg.get("source")[:8] + "...")
                
                if tx_sources:
                    log.info(f"   👥 Involved addresses: {', '.join(list(tx_sources)[:3])}")
            
            # Sort transactions by LT (Logical Time)
            transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
            
            # Show LT range for debugging
            if transactions:
                first_lt = int(transactions[0].get("transaction_id", {}).get("lt", 0))
                last_received_lt = int(transactions[-1].get("transaction_id", {}).get("lt", 0))
                log.info(f"📊 Transaction LT range: {first_lt} to {last_received_lt}")
                log.info(f"📊 Our current last_lt: {last_lt}")
                
                # Check if we're getting newer transactions
                if last_received_lt <= last_lt:
                    log.warning(f"⚠️ All received transactions are OLDER than our last_lt!")
                    log.warning(f"⚠️ Last received LT: {last_received_lt}, Our last_lt: {last_lt}")
            
            # Variables to track during this check
            highest_nft_lt = last_lt
            new_purchases_count = 0
            
            # Process each transaction
            for tx_index, tx in enumerate(transactions):
                current_lt = int(tx.get("transaction_id", {}).get("lt", 0))
                
                log.debug(f"  Processing transaction #{tx_index+1}: LT={current_lt}")
                
                # Check if this transaction is newer than our last known NFT purchase
                if current_lt > last_lt:
                    log.info(f"🆕 NEW transaction detected! LT: {current_lt} > {last_lt}")
                    
                    # Show transaction details for debugging
                    debug_transaction(tx)
                    
                    # Parse transaction to check for NFT purchases
                    purchases = parse_nft_purchases([tx])
                    
                    if purchases:
                        log.info(f"💰 NFT PURCHASE FOUND! Count: {len(purchases)}")
                        
                        for purchase_index, purchase in enumerate(purchases):
                            nft_address = purchase.get("nft_address", "")
                            buyer_address = purchase.get("buyer", "")
                            price = purchase.get("price_nanoton", 0) / 1e9
                            
                            log.info(f"  🍑 NFT #{purchase_index+1}:")
                            log.info(f"     Address: {nft_address[:10]}...")
                            log.info(f"     Buyer: {buyer_address[:10]}...")
                            log.info(f"     Price: {price:.4f} TON")
                            
                            # Send notification
                            await send_nft_notification(purchase, bot, group_id)
                            new_purchases_count += 1
                        
                        # Update the highest LT we've seen with NFT purchases
                        if current_lt > highest_nft_lt:
                            highest_nft_lt = current_lt
                            log.info(f"📈 New highest NFT LT: {highest_nft_lt}")
                    else:
                        log.info("📭 Transaction is not an NFT purchase (parse_nft_purchases returned empty)")
                        log.info("ℹ️ This is normal - most transactions are not NFT purchases")
                else:
                    log.debug(f"⏭️ Skipping OLD transaction: LT {current_lt} <= {last_lt}")
            
            # Update last_lt if we found new NFT purchases
            if highest_nft_lt > last_lt:
                old_lt = last_lt
                last_lt = highest_nft_lt
                save_last_lt(last_lt)
                log.info(f"💾 UPDATED last_lt: {old_lt} → {last_lt}")
                log.info(f"🎯 Total new NFT purchases found: {new_purchases_count}")
            elif new_purchases_count > 0:
                log.info(f"✅ Sent {new_purchases_count} notification(s) (last_lt unchanged)")
            else:
                log.debug("🔍 No new NFT purchases in this check")
            
            # Wait before next check
            log.debug(f"⏱️ Waiting {POLL_INTERVAL} seconds before next check...")
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f"❌ ERROR in NFT monitoring loop: {e}")
            import traceback
            log.error(f"❌ Full traceback:\n{traceback.format_exc()}")
            log.error("🔄 Waiting 10 seconds before retrying...")
            await asyncio.sleep(10)

async def command_check_loop(bot: Bot):
    """Command checking loop with debug logging"""
    log.info(f"⚡ Starting command loop (checking every {COMMAND_CHECK_INTERVAL}s)")
    
    error_count = 0
    
    while True:
        try:
            await asyncio.sleep(0)  # AGGIUNTO: Yield importante per event loop
            
            log.debug(f"🔄 Command loop iteration #{error_count + 1}")  # AGGIUNTO: Debug
            await check_commands(bot)
            
            error_count = 0  # Reset error counter se successo
            await asyncio.sleep(COMMAND_CHECK_INTERVAL)
            
        except Exception as e:
            error_count += 1
            log.error(f"❌ Command loop error #{error_count}: {e}")
            
            if error_count >= 5:
                log.error(f"💥 Too many errors ({error_count}), restarting loop...")
                error_count = 0
            
            await asyncio.sleep(min(5 * error_count, 30))  # Backoff esponenziale

# ─── MAIN FUNCTION ─────────────────────────────────────────────────────────
async def main():
    """Main entry point with debug logging"""
    log.info("🚀 STARTING PRECIOUS PEACH NFT BOT - DEBUG VERSION")
    log.info("=" * 50)
    
    # Debug: Check environment variables
    log.debug(f"🔧 Environment check:")
    log.debug(f"   TELEGRAM_BOT_TOKEN present: {'YES' if TELEGRAM_BOT_TOKEN else 'NO'}")
    log.debug(f"   TELEGRAM_GROUP_ID: {TELEGRAM_GROUP_ID}")
    log.debug(f"   Collection address: {COLLECTION_ADDRESS[:20]}...")
    
    if not TELEGRAM_BOT_TOKEN:
        log.error("❌ CRITICAL: TELEGRAM_BOT_TOKEN environment variable is missing!")
        log.error("❌ Please set TELEGRAM_BOT_TOKEN in your Render environment variables")
        return
    
    # Process group ID
    target_group_id = None
    
    if TELEGRAM_GROUP_ID:
        try:
            target_group_id = int(TELEGRAM_GROUP_ID)
            log.info(f"✅ Using provided group ID: {target_group_id}")
        except ValueError:
            log.error(f"❌ Invalid TELEGRAM_GROUP_ID format: {TELEGRAM_GROUP_ID}")
            log.error("❌ TELEGRAM_GROUP_ID should be a numeric ID (like -1001234567890)")
            return
    else:
        log.info("🔍 Auto-detecting group ID...")
        temp_bot = Bot(token=TELEGRAM_BOT_TOKEN)
        try:
            updates = await temp_bot.get_updates(timeout=5, limit=10)
            log.debug(f"📥 Received {len(updates)} update(s) for auto-detection")
            
            for update in updates:
                if update.message and update.message.chat.type in ["group", "supergroup"]:
                    target_group_id = update.message.chat.id
                    log.info(f"✅ Auto-detected group: {target_group_id}")
                    break
            
            if not target_group_id:
                log.error("❌ No group found in recent updates")
                log.error("❌ Please add the bot to a group and send a message")
                log.error("❌ Or set TELEGRAM_GROUP_ID environment variable")
                return
                
        except Exception as e:
            log.error(f"❌ Auto-detect error: {e}")
            return
    
    # Initialize bot
    log.debug("🤖 Initializing Telegram bot...")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    
    try:
        me = await bot.get_me()
        log.info(f"🤖 Bot connected: {me.first_name} (@{me.username})")
        log.debug(f"   Bot ID: {me.id}")
    except Exception as e:
        log.error(f"❌ Failed to connect to Telegram: {e}")
        return
    
    # Send startup message
    try:
        log.debug(f"📤 Sending startup message to group {target_group_id}")
        await bot.send_message(
            chat_id=target_group_id,
            text="🤖 *Bot Started - DEBUG MODE*\n\n✅ NFT monitoring active\n✅ Commands ready\n✅ Debug logging enabled",
            parse_mode="Markdown"
        )
        log.info("✅ Startup message sent successfully")
    except Exception as e:
        log.error(f"⚠️ Could not send startup message: {e}")
        log.warning("⚠️ Bot will continue without startup message")
    
    # Run monitoring loops
    log.info("🔄 Starting monitoring loops...")
    log.info("   - NFT monitoring: Every 15 seconds")
    log.info("   - Command checking: Every 2 seconds")
    log.info("=" * 50)
    
    try:
        # MODIFICATO: Crea task separati per migliore gestione
        nft_task = asyncio.create_task(nft_polling_loop(bot, target_group_id))
        command_task = asyncio.create_task(command_check_loop(bot))
        
        # Attendi entrambi i task
        await asyncio.gather(nft_task, command_task)
        
    except KeyboardInterrupt:
        log.info("👋 Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        log.error(f"💥 FATAL ERROR in main loops: {e}")
        import traceback
        log.error(f"💥 Full traceback:\n{traceback.format_exc()}")

if __name__ == "__main__":
    log.info("=" * 50)
    log.info("🎬 Starting Precious Peach NFT Bot")
    log.info("=" * 50)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
        log.info("👋 Bot stopped by user")
    except Exception as e:
        print(f"💥 Fatal error: {e}")
        import traceback
        traceback.print_exc()
        log.error(f"💥 Fatal error on startup: {e}")
        log.error(f"💥 Traceback: {traceback.format_exc()}")
        exit(1)