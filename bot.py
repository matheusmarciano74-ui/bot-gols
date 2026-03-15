import os
import time
import requests
from datetime import datetime

TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("telegram vars ausentes")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=15)

print("BOT TESTE SUBIU", flush=True)
tg_send("🤖 BOT TESTE SUBIU")

while True:
    try:
        now = datetime.now().strftime("%H:%M:%S")
        print(f"loop vivo {now}", flush=True)
        time.sleep(60)
    except Exception as e:
        print(f"erro loop: {e}", flush=True)
        time.sleep(10)
