# ============================================
# BOT APOSTAS PRE-JOGO
# Versão: v1.4
# DUPLA OVER 0.5
# ============================================

import os
import time
import requests
from datetime import datetime

API_KEY = os.getenv("API_FOOTBALL_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

ODD_MIN_DUPLA = 1.80
INTERVALO = 300

headers = {
    "x-apisports-key": API_KEY
}

# ============================================

def enviar_telegram(msg):

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    }

    try:
        requests.post(url, data=data, timeout=20)
    except:
        pass


# ============================================

def buscar_jogos():

    url = "https://v3.football.api-sports.io/fixtures?live=all"

    r = requests.get(url, headers=headers)

    data = r.json()

    jogos = []

    for jogo in data["response"]:

        casa = jogo["teams"]["home"]["name"]
        fora = jogo["teams"]["away"]["name"]

        gols = jogo["goals"]["home"] + jogo["goals"]["away"]

        minuto = jogo["fixture"]["status"]["elapsed"]

        jogos.append({
            "casa": casa,
            "fora": fora,
            "gols": gols,
            "minuto": minuto
        })

    return jogos


# ============================================

def gerar_dupla(jogos):

    candidatos = []

    for j in jogos:

        if j["gols"] == 0 and j["minuto"] <= 25:

            candidatos.append(j)

    if len(candidatos) < 2:
        return None

    j1 = candidatos[0]
    j2 = candidatos[1]

    return j1, j2


# ============================================

def rodar():

    enviar_telegram("🤖 BOT APOSTAS INICIADO v1.4")

    while True:

        try:

            jogos = buscar_jogos()

            dupla = gerar_dupla(jogos)

            if dupla:

                j1, j2 = dupla

                msg = f"""
🔥 DUPLA OVER 0.5

{j1['casa']} x {j1['fora']}
Minuto: {j1['minuto']}

{j2['casa']} x {j2['fora']}
Minuto: {j2['minuto']}

Mercado: Over 0.5
"""

                enviar_telegram(msg)

            time.sleep(INTERVALO)

        except Exception as e:

            enviar_telegram(f"Erro bot: {e}")

            time.sleep(60)


# ============================================

if __name__ == "__main__":
    rodar()
