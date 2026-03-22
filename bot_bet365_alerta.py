import itertools
import json
import os
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests

# =========================================================
# CONFIG
# =========================================================

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_FOOTBALL_URL = "https://v3.football.api-sports.io"
TELEGRAM_BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

INTERVALO_LOOP_SEGUNDOS = 20

# 🔥 AJUSTES IMPORTANTES
STAKE_BASE_PCT = 0.03
MAX_LOSS_PCT = 0.10

BOOKMAKER_PREFERIDO = "Bet365"

# ❌ DESLIGADO (CRÍTICO)
PERMITIR_ALERTA_SEM_ODD = False


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"{TELEGRAM_BASE_URL}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        requests.post(url, data=payload, timeout=20)
        return True
    except:
        return False


# =========================================================
# API
# =========================================================

def football_get(path, params=None):
    url = f"{BASE_FOOTBALL_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


# =========================================================
# ODD
# =========================================================

def parse_over05_from_odds_response(data):
    responses = data.get("response", [])

    for item in responses:
        for bookmaker in item.get("bookmakers", []):
            bname = bookmaker.get("name", "")

            for bet in bookmaker.get("bets", []):
                for val in bet.get("values", []):
                    label = (val.get("value") or "").lower()

                    if "over" in label and "0.5" in label:
                        try:
                            odd = float(val.get("odd"))
                            if odd > 1.20:
                                return odd, bname
                        except:
                            continue

    return None, None


def get_live_over05_odd(fixture_id):
    try:
        data = football_get("/odds/live", params={"fixture": fixture_id})
        return parse_over05_from_odds_response(data)
    except:
        return None, None


# =========================================================
# FILTRO
# =========================================================

def fixture_ok(fx):
    minute = fx["fixture"]["status"].get("elapsed") or 0
    goals = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)

    # 🔥 MELHORIAS
    if minute < 10 or minute > 35:
        return False

    if goals > 1:
        return False

    return True


# =========================================================
# BUSCAR JOGOS
# =========================================================

def fetch_live_candidates():
    data = football_get("/fixtures", params={"live": "all"})
    resp = data.get("response", [])

    candidates = []

    for fx in resp:
        try:
            if not fixture_ok(fx):
                continue

            fixture_id = fx["fixture"]["id"]
            minute = fx["fixture"]["status"].get("elapsed") or 0
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]

            odd, book = get_live_over05_odd(fixture_id)

            # 🔥 FILTRO DE ODD
            if not odd:
                continue

            candidates.append({
                "home": home,
                "away": away,
                "minute": minute,
                "odd": round(odd, 2),
                "book": book or "-",
                "link": f"https://www.google.com/search?q={quote_plus(home + ' x ' + away + ' bet365')}"
            })

        except:
            continue

    print(f"[DEBUG] candidatos: {len(candidates)}")
    return candidates


# =========================================================
# ESCOLHER JOGO
# =========================================================

def escolher_jogo(candidates):
    if not candidates:
        return None

    # 🔥 PRIORIDADE: maior minuto + melhor odd
    return sorted(
        candidates,
        key=lambda x: (x["minute"], x["odd"]),
        reverse=True
    )[0]


# =========================================================
# ALERTA
# =========================================================

def enviar_alerta(jogo):
    busca = f"{jogo['home']} x {jogo['away']} bet365"
    link = f"https://www.google.com/search?q={quote_plus(busca)}"

    msg = (
        f"🔥 SINAL AO VIVO\n\n"
        f"⚽ {jogo['home']} x {jogo['away']}\n"
        f"⏱ {jogo['minute']}'\n\n"
        f"🎯 Over 0.5 HT\n"
        f"💸 Odd: {jogo['odd']} ({jogo['book']})\n\n"
        f"📲 AÇÃO RÁPIDA:\n"
        f"1. Abra a bet365\n"
        f"2. Pesquise:\n"
        f"{jogo['home']} x {jogo['away']}\n"
        f"3. Entre em:\n"
        f"Mais de 0.5 gols (1º tempo)\n\n"
        f"🔎 ABRIR JOGO:\n{link}\n\n"
        f"⚠️ Stake: 2% banca"
    )

    send_telegram(msg)

# =========================================================
# LOOP
# =========================================================

def main():
    send_telegram("✅ BOT BET365 ATIVO")

    enviados = set()

    while True:
        try:
            candidates = fetch_live_candidates()
            jogo = escolher_jogo(candidates)

            if jogo:
                key = f"{jogo['home']}-{jogo['away']}"

                if key not in enviados:
                    enviar_alerta(jogo)
                    enviados.add(key)

        except Exception as e:
            print("Erro:", e)

        time.sleep(INTERVALO_LOOP_SEGUNDOS)


if __name__ == "__main__":
    main()
