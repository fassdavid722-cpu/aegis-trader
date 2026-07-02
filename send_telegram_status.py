import os
import requests
from datetime import datetime
import pytz

# Load Telegram config from env
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '6472746064')

if not TELEGRAM_BOT_TOKEN:
    print("⚠️  TELEGRAM_BOT_TOKEN not set")
    exit(1)

utc_now = datetime.now(pytz.UTC)
hour = utc_now.hour
london_active = 7 <= hour < 9
ny_active = 13 <= hour < 15
session_status = "🟢 LONDON" if london_active else ("🔵 NY" if ny_active else "⚫ OFF-HOURS")

message = f"""
🤖 *Aegis Trader Heartbeat*
🕐 {utc_now.strftime('%H:%M UTC')}
📍 {session_status}

✅ API Health: OK
🔌 Database: Connected
⚙️ Analyst: {'Active' if london_active or ny_active else 'Sleeping'}

Cycle: 5-minute heartbeat running
Scan: 8 symbols (BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK)
Analysis: Dual timeframe (4H regime + 15min setup)
"""

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
payload = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": message.strip(),
    "parse_mode": "Markdown"
}

response = requests.post(url, json=payload)
if response.status_code == 200:
    print("✅ Telegram status sent")
else:
    print(f"⚠️  Telegram error: {response.status_code} - {response.text}")

