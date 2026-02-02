"""
Precious Peach NFT Tracker Bot
Monitorizza gli acquisti della collezione Precious Peaches su TON
e invia notifiche in tempo reale nel gruppo Telegram.

Usa TON Center (toncenter.com) come API — funziona senza chiave (1 req/s).

Richiede:
  - TELEGRAM_BOT_TOKEN   : token del bot da @BotFather
  - TELEGRAM_GROUP_ID    : (OPZIONALE) se non lo metti, il bot lo recupera da solo
"""

import asyncio
import logging
import os
import json
import time
from typing import List, Dict, Optional
import httpx
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

# --- COSTANTI GLOBALI ---
TONCENTER_API = "https://toncenter.com/api/v2"

# ─── CONFIGURAZIONE ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID  = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None

# Indirizzo della collezione Precious Peaches su TON (formato UQ)
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"

# Intervallo di polling in secondi
POLL_INTERVAL = 12

# File locale dove salvare l'ultimo lt processato
STATE_FILE = "last_lt.txt"

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── AUTO-DETECT dell'ID del gruppo ─────────────────────────────────────────
async def detect_group_id(bot: Bot) -> int:
    updates = await bot.get_updates(timeout=5)
    for update in updates:
        chat = None
        if update.message:
            chat = update.message.chat
        elif update.my_chat_member:
            chat = update.my_chat_member.chat
        if chat and chat.type in ("supergroup", "group"):
            log.info(f"Gruppo trovato automaticamente: {chat.title} (ID: {chat.id})")
            return chat.id

    raise RuntimeError(
        "Nessun gruppo trovato. Assicurati che il bot sia amministratore "
        "e che qualcuno abbia scritto nel gruppo, oppure imposta TELEGRAM_GROUP_ID."
    )


# ─── STATO PERSISTENTE ───────────────────────────────────────────────────────
def load_last_lt() -> int:
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_last_lt(lt: int) -> None:
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))


# ─── TON CENTER – recupero transazioni ──────────────────────────────────────
TONCENTER_API = "https://toncenter.com/api/v2"

async def fetch_transactions(address: str, limit: int = 100, to_lt: int = None) -> list:
    """
    Recupera le transazioni per un indirizzo TON.
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
        logging.error(f"Errore nel fetch delle transazioni per {address}: {e}")
        return []


async def fetch_nft_data(nft_address: str) -> dict | None:
    """
    Recupera i dati di un NFT item usando getNftData.
    Restituisce il dict con init, index, collection_address, owner_address, individual_data.
    """
    url = f"{TONCENTER_BASE}/getNftData"
    params = {"address": nft_address}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        log.warning(f"Errore recupero NFT data: {e}")
    return None


# ─── PARSING delle transazioni per trovare gli acquisti NFT ─────────────────
def parse_nft_purchases(transactions: list[dict]) -> list[dict]:
    """
    Cerca le transazioni dove:
    - Il messaggio in ingresso viene da un indirizzo esterno (il buyer)
    - Contiene un valore TON > 0 (il pagamento)
    - C'è almeno un messaggio in uscita verso un indirizzo diverso dalla collezione
      (il trasferimento dell'NFT item al nuovo owner)
    """
    purchases = []

    for tx in transactions:
        lt = tx.get("lt", 0)
        utime = tx.get("utime", 0)
        in_msg = tx.get("in_msg", {})
        out_messages = tx.get("out_messages", [])

        # Il buyer è la sorgente del messaggio in ingresso
        buyer = in_msg.get("source", "")
        price_nanoton = int(in_msg.get("value", "0"))

        # Se non c'è un valore o una sorgente, non è un acquisto
        if price_nanoton == 0 or not buyer:
            continue

        # Cerca nei messaggi in uscita il trasferimento verso l'NFT item
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
                break  # un solo acquisto per transazione

    return purchases


# ─── FORMATTAZIONE del messaggio Telegram ───────────────────────────────────
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


# ─── INVIO messaggio nel gruppo ──────────────────────────────────────────────
async def send_to_group(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(
            chat_id=TELEGRAM_GROUP_ID,
            text=text,
            parse_mode="Markdown",
        )
        log.info("Messaggio inviato nel gruppo.")
    except TelegramError as e:
        log.error(f"Errore invio messaggio Telegram: {e}")


# ─── LOOP PRINCIPALE ─────────────────────────────────────────────────────────
async def polling_loop(bot: Bot):
    """Loop principale di polling delle transazioni."""
    last_processed_lt = load_last_lt()
    
    # PRIMA ESECUZIONE: calibrazione
    if last_processed_lt == 0:
        logging.info("🎯 Prima esecuzione - calibrazione iniziale...")
        
        try:
            # Prima chiamata SENZA to_lt per ottenere le transazioni più recenti
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=10, to_lt=None)
            
            if transactions:
                # Trova l'ultimo LT (Logical Time)
                lts = []
                for tx in transactions:
                    tx_id = tx.get("transaction_id", {})
                    lt = tx_id.get("lt")
                    if lt:
                        lts.append(int(lt))
                
                if lts:
                    last_processed_lt = max(lts)
                    save_last_lt(last_processed_lt)
                    logging.info(f"✅ Calibrazione completata. Ultimo LT: {last_processed_lt}")
                    
                    # NON inviare notifiche per le transazioni vecchie
                    logging.info("⏭️  Saltate notifiche per transazioni esistenti")
                else:
                    logging.warning("⚠️  Nessun LT trovato nelle transazioni")
                    last_processed_lt = int(time.time() * 1000)  # Fallback a timestamp corrente
            else:
                logging.info("📭 Nessuna transazione trovata per la collezione")
                last_processed_lt = int(time.time() * 1000)
                
        except Exception as e:
            logging.error(f"❌ Errore durante la calibrazione: {e}")
            last_processed_lt = int(time.time() * 1000)
            save_last_lt(last_processed_lt)
    
    logging.info(f"🚀 Polling avviato. Ultimo LT processato: {last_processed_lt}")
    
    # Loop principale
    while True:
        try:
            # Usa to_lt solo se > 0
            to_lt_param = last_processed_lt if last_processed_lt > 0 else None
            
            transactions = await fetch_transactions(
                COLLECTION_ADDRESS, 
                limit=100, 
                to_lt=to_lt_param
            )
            
            if transactions:
                # Ordina per LT crescente (dal più vecchio al più nuovo)
                transactions.sort(key=lambda x: int(x.get("transaction_id", {}).get("lt", 0)))
                
                new_last_lt = last_processed_lt
                
                for tx in transactions:
                    tx_id = tx.get("transaction_id", {})
                    current_lt = int(tx_id.get("lt", 0))
                    
                    # Processa solo transazioni NUOVE
                    if current_lt > last_processed_lt:
                        await process_transaction(tx, bot)
                        new_last_lt = max(new_last_lt, current_lt)
                
                # Aggiorna l'ultimo LT processato
                if new_last_lt > last_processed_lt:
                    last_processed_lt = new_last_lt
                    save_last_lt(last_processed_lt)
                    logging.info(f"📈 Aggiornato ultimo LT a: {last_processed_lt}")
            
            # Attesa prima del prossimo poll
            await asyncio.sleep(POLL_INTERVAL)
            
        except Exception as e:
            logging.error(f"❌ Errore nel polling loop: {e}")
            await asyncio.sleep(10)  # Breve pausa in caso di errore

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
async def main() -> None:
    global TELEGRAM_GROUP_ID

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    me = await bot.get_me()
    log.info(f"Bot connesso come: {me.first_name} (@{me.username})")

    if TELEGRAM_GROUP_ID is None:
        log.info("TELEGRAM_GROUP_ID non impostato — ricerca automatica…")
        TELEGRAM_GROUP_ID = await detect_group_id(bot)

    log.info(f"Grup target ID: {TELEGRAM_GROUP_ID}")
    await polling_loop(bot)


if __name__ == "__main__":
    asyncio.run(main())
