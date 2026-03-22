import json
import os
import time
from urllib.parse import quote_plus
import requests

# ================= CONFIG =================

API_KEY = os.getenv("API_FOOTBALL_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://v3.football.api-sports.io"
TG_URL = f"https://api.telegram.org/bot{TOKEN}"

HEADERS = {"x-apisports-key": API_KEY}

STATE_FILE = "state.json"
INTERVALO = 20

# ================= LIGAS =================

LIGAS_TOP = {
    ("England", "Premier League"),
    ("Spain", "La Liga"),
    ("Italy", "Serie A"),
    ("Germany", "Bundesliga"),
    ("France", "Ligue 1"),
    ("Brazil", "Serie A"),
}

# ================= STATE =================

def default_state():
    return {
        "modo": "SNIPER",
        "ativo": True,
        "odd_min": 1.20,
        "minuto_max": 35,
        "ligas": "MEDIO",
        "historico": [],
        "pendente": None
    }

def load():
    if not os.path.exists(STATE_FILE):
        s = default_state()
        save(s)
        return s
    return json.load(open(STATE_FILE))

def save(s):
    json.dump(s, open(STATE_FILE, "w"))

state = load()

# ================= TELEGRAM =================

def send(msg, buttons=None):
    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }

    if buttons:
        data["reply_markup"] = json.dumps({
            "inline_keyboard": buttons
        })

    requests.post(f"{TG_URL}/sendMessage", data=data)

last_update = 0

def updates():
    global last_update
    r = requests.get(f"{TG_URL}/getUpdates", params={"offset": last_update+1})
    data = r.json()

    for u in data.get("result", []):
        last_update = u["update_id"]
        yield u

# ================= MENU =================

def painel():
    msg = (
        "🚀 MENU DO BOT\n\n"
        f"Modo: {state['modo']}\n"
        f"Ativo: {state['ativo']}\n"
        f"Odd min: {state['odd_min']}\n"
        f"Minuto máx: {state['minuto_max']}\n"
        f"Ligas: {state['ligas']}"
    )

    buttons = [
        [{"text": "📊 Painel", "callback_data": "status"}],
        [{"text": "🟢 Ativar", "callback_data": "start"},
         {"text": "🔴 Pausar", "callback_data": "stop"}],
        [{"text": "🎯 Sniper", "callback_data": "sniper"},
         {"text": "🚀 Volume", "callback_data": "volume"}],
        [{"text": "🌍 Ligas", "callback_data": "ligas"}],
        [{"text": "⚙️ Config", "callback_data": "config"}],
        [{"text": "💰 Histórico", "callback_data": "historico"}],
    ]

    send(msg, buttons)

# ================= API =================

def api(path):
    return requests.get(BASE_URL+path, headers=HEADERS).json()

# ================= ODD =================

def get_odd(fid):
    try:
        data = api(f"/odds/live?fixture={fid}")

        for item in data.get("response", []):
            for b in item.get("bookmakers", []):
                for bet in b.get("bets", []):
                    for v in bet.get("values", []):
                        if "over" in (v.get("value") or "").lower():
                            odd = float(v["odd"])
                            if odd >= state["odd_min"]:
                                return odd
    except:
        pass
    return None

# ================= FILTRO =================

def liga_ok(country, league):
    if state["ligas"] == "OPEN":
        return True
    if state["ligas"] == "TOP":
        return (country, league) in LIGAS_TOP
    return True

def valido(fx):
    m = fx["fixture"]["status"].get("elapsed") or 0
    g = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)
    country = fx["league"]["country"]
    league = fx["league"]["name"]

    if not liga_ok(country, league):
        return False

    if m < 10 or m > state["minuto_max"]:
        return False

    if g > 1:
        return False

    return True

# ================= ALERTA =================

def alerta(j):
    link = f"https://www.google.com/search?q={quote_plus(j['home']+' x '+j['away'])}"

    msg = (
        f"🔥 SINAL\n\n"
        f"{j['home']} x {j['away']}\n"
        f"{j['min']} min\n\n"
        f"Over 0.5 HT\n"
        f"Odd: {j['odd']}\n\n"
        f"{link}"
    )

    buttons = [
        [{"text": "✅ Apostei", "callback_data": f"bet|{j['home']}|{j['away']}|{j['odd']}"}],
        [{"text": "❌ Ignorar", "callback_data": "skip"}]
    ]

    send(msg, buttons)

# ================= CALLBACK =================

def callbacks(data):
    if data == "start":
        state["ativo"] = True
    elif data == "stop":
        state["ativo"] = False
    elif data == "sniper":
        state["modo"] = "SNIPER"
    elif data == "volume":
        state["modo"] = "VOLUME"

    elif data == "ligas":
        send("🌍 Escolha:", [
            [{"text": "TOP", "callback_data": "liga_top"}],
            [{"text": "MEDIO", "callback_data": "liga_medio"}],
            [{"text": "OPEN", "callback_data": "liga_open"}],
        ])
        return

    elif data == "liga_top":
        state["ligas"] = "TOP"
    elif data == "liga_medio":
        state["ligas"] = "MEDIO"
    elif data == "liga_open":
        state["ligas"] = "OPEN"

    elif data == "config":
        send("⚙️ Config:", [
            [{"text": "Odd 1.20", "callback_data": "odd_120"},
             {"text": "Odd 1.40", "callback_data": "odd_140"}],
            [{"text": "Min 30", "callback_data": "min_30"},
             {"text": "Min 45", "callback_data": "min_45"}],
        ])
        return

    elif data == "odd_120":
        state["odd_min"] = 1.20
    elif data == "odd_140":
        state["odd_min"] = 1.40

    elif data == "min_30":
        state["minuto_max"] = 30
    elif data == "min_45":
        state["minuto_max"] = 45

    elif data.startswith("bet"):
        _, h, a, o = data.split("|")
        state["pendente"] = {"home": h, "away": a, "odd": float(o), "status": "wait"}
        send("💰 Digite valor")

    elif data == "historico":
        send(f"📊 Total apostas: {len(state['historico'])}")

    save(state)

# ================= TEXTO =================

def texto(msg):
    if msg == "/painel":
        painel()

    elif state.get("pendente") and state["pendente"]["status"] == "wait":
        try:
            v = float(msg)
            odd = state["pendente"]["odd"]

            lucro = round(v * odd - v, 2)

            state["pendente"]["valor"] = v
            state["pendente"]["lucro"] = lucro
            state["historico"].append(state["pendente"])
            state["pendente"] = None

            save(state)

            send(f"💸 Lucro: {lucro}")
        except:
            send("Valor inválido")

# ================= LOOP =================

import threading

def loop_telegram():
    while True:
        for u in updates():
            if "callback_query" in u:
                cb = u["callback_query"]

                answer_callback(cb["id"])  # 🔥 ESSENCIAL

                callbacks(cb["data"])

            if "message" in u:
                if str(u["message"]["chat"]["id"]) != str(CHAT_ID):
                    continue
                texto(u["message"].get("text",""))

        time.sleep(1)  # ⚡ resposta rápida


def loop_jogos():
    while True:
        if state["ativo"]:
            jogos = api("/fixtures?live=all").get("response", [])

            for fx in jogos:
                if not valido(fx):
                    continue

                fid = fx["fixture"]["id"]
                odd = get_odd(fid)

                if not odd:
                    continue

                alerta({
                    "home": fx["teams"]["home"]["name"],
                    "away": fx["teams"]["away"]["name"],
                    "min": fx["fixture"]["status"]["elapsed"],
                    "odd": odd
                })

                break

        time.sleep(INTERVALO)  # pode manter 20s aqui


def main():
    send("🤖 BOT INICIADO")

    t1 = threading.Thread(target=loop_telegram)
    t2 = threading.Thread(target=loop_jogos)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

if __name__ == "__main__":
    main()
