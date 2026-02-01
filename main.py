"""
Precious Peach NFT Tracker Bot
Monitorizza gli acquisti della collezione Precious Peaches su TON
e invia notifiche in tempo reale nel gruppo Telegram.

Usa TON Center (toncenter.com) come API — funziona senza chiave (1 req/s).

Richiede:
  - TELEGRAM_BOT_TOKEN   : token del bot da @BotFather
  - TELEGRAM_GROUP_ID    : (OPZIONALE) se non lo metti, il bot lo recupera da solo
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from telegram import Bot
from telegram.error import TelegramError

# ─── CONFIGURAZIONE ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID  = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None

# Indirizzo della collezione Precious Peaches su TON (formato UQ)
COLLECTION_ADDRESS = "UQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"

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
TONCENTER_BASE = "https://toncenter.com/api/v2"


async def fetch_transactions(address: str, limit: int = 20, to_lt: int = 0) -> list[dict]:
    """
    Recupera le transazioni più recenti per un indirizzo.
    to_lt=0 significa "dalle più recenti".
    """
    url = f"{TONCENTER_BASE}/getTransactions"
    params = {
        "address": address,
        "limit": limit,
        "to_lt": to_lt,
        "archival": False,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            log.error(f"TON Center errore: {data.get('error', 'sconosciuto')}")
            return []
        return data.get("result", [])


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
        f"🍑 *Precious Peach acquistata!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *NFT:* [{nft_name}]({nft_link})\n"
        f"💰 *Prezzo:* {price_ton:.4f} TON\n"
        f"🛒 *Compratore:* [{shorten(buyer_addr)}]({buyer_link})\n"
        f"🕐 *Orario:* {time_str}\n"
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
async def polling_loop(bot: Bot) -> None:
    last_lt = load_last_lt()
    log.info(f"Bot avviato. Ultimo lt processato: {last_lt}")

    # Prima esecuzione: calibra senza inviare notifiche
    if last_lt == 0:
        log.info("Prima esecuzione – calibrazione senza notifiche…")
        transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=5)
        if transactions:
            max_lt = max(tx.get("lt", 0) for tx in transactions)
            save_last_lt(max_lt)
            last_lt = max_lt
            log.info(f"Calibrazione completata. lt iniziale: {last_lt}")

    while True:
        try:
            transactions = await fetch_transactions(COLLECTION_ADDRESS, limit=30)

            # Filtra solo transazioni più recenti del nostro last_lt
            new_txs = [tx for tx in transactions if tx.get("lt", 0) > last_lt]

            if new_txs:
                new_txs.sort(key=lambda tx: tx["lt"])

                purchases = parse_nft_purchases(new_txs)

                for purchase in purchases:
                    # Prova a recuperare il numero dell'NFT
                    nft_name = "Precious Peach"
                    nft_data = await fetch_nft_data(purchase["nft_address"])
                    if nft_data:
                        idx = nft_data.get("index")
                        if idx is not None:
                            nft_name = f"Precious Peach #{idx}"

                    msg = format_purchase_message(purchase, nft_name)
                    log.info(
                        f"Nuovo acquisto – NFT: {purchase['nft_address']}, "
                        f"Prezzo: {purchase['price_nanoton'] / 1e9:.4f} TON"
                    )
                    await send_to_group(bot, msg)

                # Aggiorna last_lt
                max_lt = max(tx.get("lt", 0) for tx in new_txs)
                save_last_lt(max_lt)
                last_lt = max_lt
                log.info(f"lt aggiornato a: {last_lt}")
            else:
                log.debug("Nessuna nuova transazione.")

        except httpx.HTTPStatusError as e:
            log.error(f"Errore HTTP TON Center: {e.response.status_code} – {e.response.text[:200]}")
        except Exception as e:
            log.error(f"Errore nel loop: {e}")

        await asyncio.sleep(POLL_INTERVAL)


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
