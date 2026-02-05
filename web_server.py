"""
web_server.py – Wrapper for Render.com with DEBUG
"""

import os
import sys
import time
import types

# === DEBUG: Find where we are ===
print("=== RENDER DEBUG INFO ===")
print(f"Python version: {sys.version}")
print(f"Current dir: {os.getcwd()}")
print(f"Script path: {__file__}")
print(f"Files here: {os.listdir('.')}")

# Check common locations
paths_to_check = [
    '.',
    '/opt/render/project',
    '/opt/render/project/src', 
    '/app',
    '/var/task'
]

for path in paths_to_check:
    if os.path.exists(path):
        print(f"\n📁 Checking {path}:")
        try:
            files = os.listdir(path)
            print(f"   Files: {files[:10]}...")  # First 10 files
            if 'web_server.py' in files:
                print(f"   ✅ FOUND web_server.py!")
            if 'main.py' in files:
                print(f"   ✅ FOUND main.py!")
        except:
            print(f"   ❌ Cannot access")

# Look for web_server.py everywhere
print("\n🔍 Searching for web_server.py...")
for root, dirs, files in os.walk('/opt/render'):
    if 'web_server.py' in files:
        print(f"✅ Found at: {os.path.join(root, 'web_server.py')}")
        break
else:
    print("❌ Not found in /opt/render")

print("==========================\n")
time.sleep(2)  # Give time to read logs

# Now continue with normal imports
import asyncio
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import urllib.request

# Monkey patch for imghdr in Python 3.13
if sys.version_info >= (3, 13):
    sys.modules['imghdr'] = types.ModuleType('imghdr')
    imghdr_module = sys.modules['imghdr']
    imghdr_module.what = lambda *args, **kwargs: None
    imghdr_module.test = lambda *args, **kwargs: None

import os
import asyncio
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
PORT = int(os.environ.get("PORT", "8000"))

# ─── HTTP Server for Health Check ──────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Bot is running")
    
    def log_message(self, format, *args):
        pass

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    log.info(f"Health check server on port {PORT}")
    server.serve_forever()

# ─── Keep-alive Ping ───────────────────────────────────────────────────────
async def keep_alive():
    while True:
        await asyncio.sleep(600)  # 10 minutes
        try:
            response = urllib.request.urlopen(RENDER_URL, timeout=10)
            log.info(f"Keep-alive ping: {response.status}")
        except Exception as e:
            log.warning(f"Keep-alive failed: {e}")

# ─── Start Bot ─────────────────────────────────────────────────────────────
async def start_bot():
    from main import main as bot_main
    await bot_main()

# ─── Main ──────────────────────────────────────────────────────────────────
async def main():
    # Start HTTP server in separate thread
    Thread(target=run_http_server, daemon=True).start()
    
    # Start keep-alive
    asyncio.create_task(keep_alive())
    
    # Start the bot
    log.info("Starting Telegram bot...")
    await start_bot()

if __name__ == "__main__":
    asyncio.run(main())
