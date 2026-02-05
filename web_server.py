"""
web_server.py - Minimal health check for Render
"""

import os
import asyncio
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PORT = int(os.environ.get("PORT", "8000"))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
    
    def log_message(self, *args):
        pass

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"✅ Health server on port {PORT}")
    server.serve_forever()

if __name__ == "__main__":
    # Run HTTP server in background thread
    Thread(target=run_server, daemon=True).start()
    
    # Import and run the bot
    from main import main as bot_main
    
    try:
        asyncio.run(bot_main())
    except KeyboardInterrupt:
        log.info("Bot stopped")
    except Exception as e:
        log.error(f"Bot crashed: {e}")
