"""
web_server.py - Health check server for Render
"""

import os
import asyncio
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", "8000"))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Precious Peach Bot")
    
    def log_message(self, *args):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"✅ Health server on port {PORT}")
    server.serve_forever()

async def keep_alive():
    """Ping itself every 10 minutes to stay awake"""
    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            urllib.request.urlopen(RENDER_URL, timeout=5)
            log.info("🔔 Keep-alive ping")
        except:
            log.warning("⚠️ Keep-alive failed")

async def main():
    # Start health server in background thread
    Thread(target=run_health_server, daemon=True).start()
    
    # Start keep-alive
    asyncio.create_task(keep_alive())
    
    # Import and run the bot
    try:
        from main import main as bot_main
        await bot_main()
    except ImportError as e:
        log.error(f"❌ Import error: {e}")
        raise
    except Exception as e:
        log.error(f"❌ Bot error: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("👋 Bot stopped")
    except Exception as e:
        log.error(f"💥 Fatal error: {e}")