"""
web_server.py – Wrapper per Render.com free tier.

Render mette a dormire i servizi dopo 15 min di inattività.
Questo file:
  1. Avvia un HTTP server minimo che risponde ai health-check di Render.
  2. Esegue un self-ping ogni 10 minuti per tenere il servizio sveglio.
  3. Lancia il polling loop del bot in background.
"""


import sys
import types

# Monkey patch per imghdr su Python 3.13
if sys.version_info >= (3, 13):
    sys.modules['imghdr'] = types.ModuleType('imghdr')
    # Aggiungi funzioni base se necessario
    imghdr_module = sys.modules['imghdr']
    imghdr_module.what = lambda *args, **kwargs: None
    imghdr_module.test = lambda *args, **kwargs: None

import os
import asyncio
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# URL del servizio stesso su Render (impostato come env var durante il deploy)
# Render lo fornisce automaticamente come RENDER_EXTERNAL_URL
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
PORT = int(os.environ.get("PORT", "8000"))

# ─── HTTP Handler per health-check ───────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Precious Peach Bot is alive")

    # Silenzia i log di accesso HTTP
    def log_message(self, format, *args):
        pass


def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"HTTP health-check server avviato sulla porta {PORT}")
    server.serve_forever()


# ─── Self-ping per evitare il sleep di Render ───────────────────────────────
async def self_ping_loop():
    """Ogni 10 minuti invia un GET a se stesso per restare sveglio."""
    import urllib.request
    while True:
        await asyncio.sleep(600)  # 10 minuti
        try:
            req = urllib.request.Request(RENDER_URL)
            response = urllib.request.urlopen(req, timeout=10)
            log.info(f"Self-ping: {response.status}")
        except Exception as e:
            log.warning(f"Self-ping fallito: {e}")


# ─── MAIN ────────────────────────────────────────────────────────────────────
async def main():
    # 1. Avvia il server HTTP in un thread separato (bloccante)
    http_thread = Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # 2. Avvia il self-ping in background
    asyncio.create_task(self_ping_loop())

    # 3. Avvia il bot (importa e lancia il polling loop)
    # CAMBIA QUESTA RIGA:
    from main import main as bot_main
    await bot_main()


if __name__ == "__main__":
    asyncio.run(main())
