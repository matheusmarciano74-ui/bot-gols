import requests
import time
import os
from datetime import datetime

API_KEY = os.getenv("API_KEY")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT = os.getenv("TG_CHAT")

games_checked = 0
games_valid = 0
combos_sent = 0
last_run = "-"


def send(msg):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url,data={"chat_id":TG_CHAT,"text":msg})


def check_commands():

    url=f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"

    try:
        r=requests.get(url).json()
    except:
        return

    for u in r.get("result",[]):

        try:

            chat=u["message"]["chat"]["id"]
            text=u["message"]["text"]

            if str(chat) != str(TG_CHAT):
                continue

            if text == "/status":

                msg=f"""
🤖 BOT ONLINE

Jogos analisados: {games_checked}
Jogos aprovados: {games_valid}
Combos enviados hoje: {combos_sent}
Última análise: {last_run}
"""

                send(msg)

        except:
            pass


def fake_analysis():

    global games_checked
    global games_valid
    global combos_sent
    global last_run

    games_checked += 20
    games_valid += 3

    combos_sent += 1

    last_run=datetime.now().strftime("%H:%M")

    msg=f"""
🔥 COMBO OVER 0.5

Premier League
Arsenal x Brighton

Serie A
Juventus x Lecce

La Liga
Barcelona x Getafe

Odd estimada ~1.30

🔎 Buscar Bet365
"""

    send(msg)


def main():

    send("🤖 BOT OVER 0.5 iniciado")

    while True:

        try:

            fake_analysis()

            check_commands()

        except Exception as e:

            send(f"erro {e}")

        time.sleep(1800)


if __name__ == "__main__":
    main()
