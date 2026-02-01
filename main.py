"""
Precious Peach NFT Tracker Bot
Monitora gli acquisti della collezione Precious Peaches su TON
e invia notifiche in tempo reale nel gruppo Telegram.

Richiede:
  - TELEGRAM_BOT_TOKEN   : token del bot da @BotFather
  - TONAPI_KEY           : chiave API da https://tonconsole.com  (free tier va bene)
  - TELEGRAM_GROUP_ID    : (OPZIONALE) se non lo metti, il bot lo recupera da solo
"""

import os
import time
import asyncio
import logging
from datetime import datetime, timezone

import httpx
from telegram import Bot
from telegram.error import TelegramError

# ─── CONFIGURAZIONE ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_GROUP_ID  = int(os.environ["TELEGRAM_GROUP_ID"]) if os.environ.get("TELEGRAM_GROUP_ID") else None
TONAPI_KEY         = os.environ["TONAPI_KEY"]

# Indirizzo della collezione Precious Peaches su TON
COLLECTION_ADDRESS = "EQA4i58iuS9DUYRtUZ97sZo5mnkbiYUBpWXQOe3dEUCcP1W8"

# Intervallo di polling in secondi (ogni 10s controlla nuovi eventi)
POLL_INTERVAL = 10

# File locale dove salvare l'ultimo timestamp dei eventi già processati
STATE_FILE = "last_lt.txt"          # "lt" = logical time di TON

# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── AUTO-DETECT dell'ID del gruppo ─────────────────────────────────────────
async def detect_group_id(bot: Bot) -> int:
    """
    Chiama getUpdates per recuperare i messaggi recenti ricevuti dal bot.
    Cerca il primo chat di tipo 'supergroup' o 'group' e restituisce l'ID.
    Funziona anche con gruppi privati, purché il bot sia amministratore
    e qualcuno abbia scritto almeno un messaggio dopo l'aggiunta del bot.
    """
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
        "Nessun gruppo trovato. Assicurati che:\n"
        "  1. Il bot sia amministratore del gruppo.\n"
        "  2. Qualcuno abbia scritto almeno un messaggio nel gruppo dopo l'aggiunta del bot.\n"
        "  Oppure imposta manualmente TELEGRAM_GROUP_ID nelle env variables."
    )


# ─── STATO PERSISTENTE (ultimo lt processato) ───────────────────────────────
def load_last_lt() -> int:
    """Carica l'ultimo lt (logical time) processato dal file di stato."""
    try:
        with open(STATE_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_last_lt(lt: int) -> None:
    """Salva l'ultimo lt processato."""
    with open(STATE_FILE, "w") as f:
        f.write(str(lt))


# ─── TONAPI – recupero eventi della collezione ─────────────────────────────
TONAPI_BASE = "https://api.tonapi.io/v2"

HEADERS = {
    "Authorization": f"Bearer {TONAPI_KEY}",
    "Accept": "application/json",
}


async def fetch_collection_events(before_lt: int | None = None, limit: int = 20) -> list[dict]:
    """
    Chiama GET /v2/accounts/{account_id}/events
    che restituisce gli eventi alto-livello (inclusi NFT Purchase) per il contratto collezione.

    `before_lt` filtra eventi con lt < valore dato (paginazione verso il passato).
    Per ottenere i più RECENTI, non mettiamo before_lt la prima volta.
    """
    url = f"{TONAPI_BASE}/accounts/{COLLECTION_ADDRESS}/events"
    params: dict = {"limit": limit}
    if before_lt:
        params["before_lt"] = before_lt

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        return resp.json().get("events", [])


async def fetch_nft_item(nft_address: str) -> dict:
    """Recupera i dettagli di un singolo NFT item."""
    url = f"{TONAPI_BASE}/nfts/{nft_address}"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        return resp.json()


# ─── PARSING degli eventi per trovare gli acquisti NFT ──────────────────────
def extract_nft_purchases(events: list[dict]) -> list[dict]:
    """
    Dentro ogni evento cercano le azioni di tipo 'NftPurchase'.
    Ogni azione contiene: nft (indirizzo), buyer, seller, amount, price.
    Restituisce una lista di acquisti con i campi rilevanti.
    """
    purchases = []
    for event in events:
        lt      = event.get("lt", 0)
        ts      = event.get("timestamp", 0)
        actions = event.get("actions", [])

        for action in actions:
            if action.get("type") != "NftPurchase":
                continue

            details = action.get("details", {})
            nft_info = details.get("nft", {})
            nft_addr = nft_info.get("address", "")

            # Verifica che l'NFT appartenga alla nostra collezione
            collection = nft_info.get("collection", {})
            if collection.get("address", "") != COLLECTION_ADDRESS:
                continue

            buyer_addr  = details.get("buyer", {}).get("address", "unknown")
            seller_addr = details.get("seller", {}).get("address", "unknown")
            amount      = details.get("amount", {})
            price_value = amount.get("value", "0")          # in nanoTON (stringa)
            price_token = amount.get("token_name", "TON")

            # Conversione da nanoTON a TON
            try:
                price_ton = int(price_value) / 1_000_000_000
            except (ValueError, TypeError):
                price_ton = 0.0

            purchases.append({
                "lt":           lt,
                "timestamp":    ts,
                "nft_address":  nft_addr,
                "nft_name":     nft_info.get("name", ""),
                "buyer":        buyer_addr,
                "seller":       seller_addr,
                "price_ton":    price_ton,
                "price_token":  price_token,
                "event_hash":   event.get("event_id", ""),
            })

    return purchases


# ─── FORMATTAZIONE del messaggio Telegram ───────────────────────────────────
def format_purchase_message(purchase: dict, nft_details: dict | None = None) -> str:
    """Costruisce il messaggio formattato per il gruppo."""
    ts = purchase["timestamp"]
    time_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    # Nome dell'NFT: prova a usare i dettagli recuperati separatamente
    nft_name = purchase["nft_name"] or "Precious Peach"
    if nft_details:
        nft_name = nft_details.get("name", nft_name) or nft_name

    # Link all'NFT su getgems
    nft_link = f"https://getgems.io/nft/{purchase['nft_address']}"

    # Link buyer/seller su tonviewer
    buyer_link  = f"https://tonviewer.com/{purchase['buyer']}"
    seller_link = f"https://tonviewer.com/{purchase['seller']}"

    # Abbrevia gli indiririzzi per leggibilità
    buyer_short  = purchase["buyer"][:6] + "…" + purchase["buyer"][-4:]  if len(purchase["buyer"]) > 12 else purchase["buyer"]
    seller_short = purchase["seller"][:6] + "…" + purchase["seller"][-4:] if len(purchase["seller"]) > 12 else purchase["seller"]

    msg = (
        f"🍑 *Precious Peach acquistata!*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ *NFT:* [{nft_name}]({nft_link})\n"
        f"💰 *Prezzo:* {purchase['price_ton']:.4f} {purchase['price_token']}\n"
        f"🛒 *Compratore:* [{buyer_short}]({buyer_link})\n"
        f"🏪 *Venditore:* [{seller_short}]({seller_link})\n"
        f"🕐 *Orario:* {time_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


# ─── INVIO messaggio nel gruppo ──────────────────────────────────────────────
async def send_to_group(bot: Bot, text: str) -> None:
    """Invia un messaggio nel gruppo con formattazione Markdown."""
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
    """
    Loop principale che ogni POLL_INTERVAL secondi:
    1. Recupera gli ultimi eventi della collezione.
    2. Filtra gli acquisti NFT nuovi (lt > ultimo lt salvato).
    3. Per ogni acquisto, recupera dettagli NFT e invia notifica.
    """
    last_lt = load_last_lt()
    log.info(f"Bot avviato. Ultimo lt processato: {last_lt}")

    # Prima esecuzione: se last_lt è 0, recupera i primi eventi per calibrare
    # senza inviare notifiche (evita spam al primo avvio)
    if last_lt == 0:
        log.info("Prima esecuzione – calibrazione senza notifiche…")
        events = await fetch_collection_events(limit=5)
        if events:
            max_lt = max(e.get("lt", 0) for e in events)
            save_last_lt(max_lt)
            last_lt = max_lt
            log.info(f"Calibrazione completata. lt iniziale: {last_lt}")

    while True:
        try:
            events = await fetch_collection_events(limit=50)

            # Filtra solo eventi più recenti di last_lt
            new_events = [e for e in events if e.get("lt", 0) > last_lt]

            if new_events:
                # Ordina per lt crescente (dal più vecchio al più recente)
                new_events.sort(key=lambda e: e["lt"])

                purchases = extract_nft_purchases(new_events)

                for purchase in purchases:
                    # Recupera dettagli aggiuntivi dell'NFT
                    nft_details = None
                    try:
                        nft_details = await fetch_nft_item(purchase["nft_address"])
                    except Exception as e:
                        log.warning(f"Non ho potuto recuperare dettagli NFT: {e}")

                    msg = format_purchase_message(purchase, nft_details)
                    log.info(f"Nuovo acquisto rilevato – NFT: {purchase['nft_address']}, Prezzo: {purchase['price_ton']} TON")
                    await send_to_group(bot, msg)

                # Aggiorna last_lt con il massimo degli eventi processati
                max_lt = max(e.get("lt", 0) for e in new_events)
                save_last_lt(max_lt)
                last_lt = max_lt
                log.info(f"lt aggiornato a: {last_lt}")
            else:
                log.debug("Nessun nuovo evento.")

        except httpx.HTTPStatusError as e:
            log.error(f"Errore HTTP da TonAPI: {e.response.status_code} – {e.response.text[:200]}")
        except Exception as e:
            log.error(f"Errore nel loop di polling: {e}")

        await asyncio.sleep(POLL_INTERVAL)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
async def main() -> None:
    global TELEGRAM_GROUP_ID

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    # Verifica che il bot sia valido
    me = await bot.get_me()
    log.info(f"Bot connesso come: {me.first_name} (@{me.username})")

    # Se l'ID del gruppo non è stato impostato manualmente, recuperalo da solo
    if TELEGRAM_GROUP_ID is None:
        log.info("TELEGRAM_GROUP_ID non impostato — ricerca automatica…")
        TELEGRAM_GROUP_ID = await detect_group_id(bot)

    log.info(f"Grup target ID: {TELEGRAM_GROUP_ID}")
    await polling_loop(bot)


if __name__ == "__main__":
    asyncio.run(main())
