"""
web_server.py - Health check + self-ping for Render free tier
"""

import os
import asyncio
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", "8000"))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot alive")
    
    def log_message(self, *args):
        pass

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"✅ Health server on port {PORT}")
    server.serve_forever()

async def self_ping():
    """Ping itself every 10 minutes to stay awake"""
    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            req = urllib.request.Request(RENDER_URL)
            response = urllib.request.urlopen(req, timeout=10)
            log.info(f"🔄 Self-ping: {response.status}")
        except Exception as e:
            log.warning(f"⚠️ Self-ping failed: {e}")

async def main():
    # Start HTTP server in background
    Thread(target=run_server, daemon=True).start()
    
    # Start self-ping in background
    asyncio.create_task(self_ping())
    
    # Run the bot
    from main import main as bot_main
    await bot_main()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped")
    except Exception as e:
        log.error(f"Bot crashed: {e}")
        raise
