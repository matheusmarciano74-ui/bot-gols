import json
import os
import time
import threading
from datetime import datetime
from urllib.parse import quote_plus
import requests

# ================= CONFIG =================

API_KEY = os.getenv("API_FOOTBALL_KEY")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID"))

BASE_URL = "https://v3.football.api-sports.io"
TG_URL = f"https://api.telegram.org/bot{TOKEN}"

HEADERS = {"x-apisports-key": API_KEY}

STATE_FILE = "state.json"

# ================= STATE =================

def default_state():
    return {
        "ativo": True,
        "modo": "SNIPER",
        "ligas": "MEDIO",
        "odd_min": 1.20,
        "minuto_max": 35,
        "historico": [],
        "pendente": None,
        "lucro_dia": 0.0
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
    data = {"chat_id": CHAT_ID, "text": msg}

    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    requests.post(f"{TG_URL}/sendMessage", data=data)

def answer_callback(cid):
    requests.post(f"{TG_URL}/answerCallbackQuery", data={"callback_query_id": cid})

last_update = 0

def updates():
    global last_update
    r = requests.get(f"{TG_URL}/getUpdates", params={"offset": last_update+1})
    data = r.json()

    for u in data.get("result", []):
        last_update = u["update_id"]
        yield u

# ================= PAINEL =================

def painel():
    msg = (
        "🚀 BOT\n\n"
        f"Modo: {state['modo']}\n"
        f"Ativo: {state['ativo']}\n"
        f"Odd: {state['odd_min']}\n"
        f"Min: {state['minuto_max']}\n"
        f"Ligas: {state['ligas']}\n\n"
        f"💰 Lucro hoje: {state['lucro_dia']}"
    )

    buttons = [
        [{"text": "🟢 Start", "callback_data": "start"},
         {"text": "🔴 Stop", "callback_data": "stop"}],
        [{"text": "🎯 Sniper", "callback_data": "sniper"},
         {"text": "🚀 Volume", "callback_data": "volume"}],
        [{"text": "🌍 Ligas", "callback_data": "ligas"}],
        [{"text": "⚙️ Config", "callback_data": "config"}],
        [{"text": "📊 Histórico", "callback_data": "historico"}],
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

def valido(fx):
    m = fx["fixture"]["status"].get("elapsed") or 0
    g = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)

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
        f"{j['min']} min\n"
        f"Odd: {j['odd']}"
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
        send("🟢 ATIVADO")

    elif data == "stop":
        state["ativo"] = False
        send("🔴 PAUSADO")

    elif data == "sniper":
        state["modo"] = "SNIPER"
        send("🎯 SNIPER")

    elif data == "volume":
        state["modo"] = "VOLUME"
        send("🚀 VOLUME")

    elif data == "ligas":
        send("Escolha:", [
            [{"text": "TOP", "callback_data": "liga_top"}],
            [{"text": "MEDIO", "callback_data": "liga_medio"}],
            [{"text": "OPEN", "callback_data": "liga_open"}]
        ])

    elif data == "liga_top":
        state["ligas"] = "TOP"
        send("TOP")

    elif data == "liga_medio":
        state["ligas"] = "MEDIO"
        send("MEDIO")

    elif data == "liga_open":
        state["ligas"] = "OPEN"
        send("OPEN")

    elif data.startswith("bet"):
        _, h, a, o = data.split("|")

        state["pendente"] = {
            "home": h,
            "away": a,
            "odd": float(o),
            "status": "esperando"
        }

        send("Digite valor")

    elif data == "historico":
        send(f"📊 Apostas: {len(state['historico'])}")

    elif data == "status":
        painel()

    save(state)

# ================= TEXTO =================

def texto(msg):
    if msg == "/painel":
        painel()

    elif state["pendente"] and state["pendente"]["status"] == "esperando":
        try:
            v = float(msg)
            odd = state["pendente"]["odd"]

            lucro = round(v * odd - v, 2)

            state["pendente"]["valor"] = v
            state["pendente"]["lucro"] = lucro
            state["pendente"]["status"] = "ativo"

            state["historico"].append(state["pendente"])

            send(f"💸 Lucro possível: {lucro}")

        except:
            send("Valor inválido")

# ================= RESULTADO AUTOMATICO =================

def verificar_resultado():
    if not state["pendente"]:
        return

    j = state["pendente"]

    data = api("/fixtures?live=all")

    for fx in data.get("response", []):
        if fx["teams"]["home"]["name"] == j["home"]:
            gols = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)

            if gols >= 1:
                lucro = j["lucro"]
                state["lucro_dia"] += lucro

                send(f"✅ WIN +{lucro}")
                state["pendente"] = None
                save(state)
                return

# ================= LOOPS =================

def loop_telegram():
    while True:
        for u in updates():

            if "callback_query" in u:
                cb = u["callback_query"]
                answer_callback(cb["id"])
                callbacks(cb["data"])

            if "message" in u:
                if str(u["message"]["chat"]["id"]) != CHAT_ID:
                    continue
                texto(u["message"].get("text",""))

        time.sleep(0.2)

def loop_jogos():
    while True:
        if state["ativo"]:
            jogos = api("/fixtures?live=all").get("response", [])

            for fx in jogos:
                if not valido(fx):
                    continue

                odd = get_odd(fx["fixture"]["id"])
                if not odd:
                    continue

                alerta({
                    "home": fx["teams"]["home"]["name"],
                    "away": fx["teams"]["away"]["name"],
                    "min": fx["fixture"]["status"]["elapsed"],
                    "odd": odd
                })
                break

        verificar_resultado()
        time.sleep(10)

# ================= MAIN =================

def main():
    send("🤖 BOT ONLINE")

    t1 = threading.Thread(target=loop_telegram)
    t2 = threading.Thread(target=loop_jogos)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

if __name__ == "__main__":
    main()
