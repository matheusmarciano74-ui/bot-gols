import json
import os
import time
from urllib.parse import quote_plus
import requests

# =========================================================
# CONFIG
# =========================================================

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://v3.football.api-sports.io"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

INTERVALO = 20
STATE_FILE = "state.json"

# =========================================================
# LIGAS
# =========================================================

LIGAS_TOP = {
    ("England", "Premier League"),
    ("Spain", "La Liga"),
    ("Italy", "Serie A"),
    ("Germany", "Bundesliga"),
    ("France", "Ligue 1"),
    ("Brazil", "Serie A"),
}

# =========================================================
# STATE
# =========================================================

def default_state():
    return {
        "modo_operacao": "SNIPER",
        "paused": False,
        "enviados": [],
        "odd_min": 1.20,
        "minuto_min": 10,
        "minuto_max": 35,
        "modo_ligas": "MEDIO",
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        s = default_state()
        save_state(s)
        return s

    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return default_state()


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)


state = load_state()

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(msg):
    try:
        requests.post(
            f"{TELEGRAM_URL}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}
        )
    except:
        pass


last_update = 0


def get_updates():
    global last_update

    try:
        r = requests.get(f"{TELEGRAM_URL}/getUpdates", params={"offset": last_update + 1})
        data = r.json()

        for u in data.get("result", []):
            last_update = u["update_id"]
            yield u

    except:
        return


# =========================================================
# COMANDOS
# =========================================================

def handle_command(text):
    t = text.lower()

    if t == "/modo sniper":
        state["modo_operacao"] = "SNIPER"
        save_state(state)
        send_telegram("🎯 Modo SNIPER ativado")
        return

    if t == "/modo volume":
        state["modo_operacao"] = "VOLUME"
        save_state(state)
        send_telegram("🚀 Modo VOLUME ativado")
        return

    if t.startswith("/odd "):
        try:
            val = float(t.split()[1])
            state["odd_min"] = val
            save_state(state)
            send_telegram(f"💸 Odd mínima agora: {val}")
        except:
            send_telegram("❌ Use /odd 1.50")
        return

    if t.startswith("/min "):
        try:
            val = int(t.split()[1])
            state["minuto_max"] = val
            save_state(state)
            send_telegram(f"⏱ Minuto máximo: {val}")
        except:
            send_telegram("❌ Use /min 30")
        return

    if t.startswith("/ligas "):
        modo = t.split()[1].upper()
        state["modo_ligas"] = modo
        save_state(state)
        send_telegram(f"🌍 Ligas: {modo}")
        return

    if t == "/stop":
        state["paused"] = True
        save_state(state)
        send_telegram("⛔ Bot pausado")
        return

    if t == "/startbot":
        state["paused"] = False
        save_state(state)
        send_telegram("✅ Bot ativo")
        return

    if t == "/status":
        send_telegram(
            f"🤖 STATUS\n"
            f"Modo: {state['modo_operacao']}\n"
            f"Pausado: {state['paused']}\n"
            f"Odd min: {state['odd_min']}\n"
            f"Minuto máx: {state['minuto_max']}\n"
            f"Ligas: {state['modo_ligas']}"
        )


def process_updates():
    for u in get_updates():
        msg = u.get("message", {})
        chat = str(msg.get("chat", {}).get("id"))

        if chat != TELEGRAM_CHAT_ID:
            continue

        text = msg.get("text", "")
        if text:
            handle_command(text)


# =========================================================
# API
# =========================================================

def api_get(path):
    r = requests.get(BASE_URL + path, headers=HEADERS)
    return r.json()


# =========================================================
# ODD
# =========================================================

def get_odd(fixture_id):
    try:
        data = api_get(f"/odds/live?fixture={fixture_id}")

        for item in data.get("response", []):
            for book in item.get("bookmakers", []):
                for bet in book.get("bets", []):
                    for v in bet.get("values", []):
                        label = (v.get("value") or "").lower()

                        if "over" in label and "0.5" in label:
                            odd = float(v.get("odd"))
                            if odd >= state["odd_min"]:
                                return round(odd, 2), book.get("name")

    except:
        pass

    return None, None


# =========================================================
# FILTRO
# =========================================================

def liga_ok(country, league):
    if state["modo_ligas"] == "OPEN":
        return True

    if state["modo_ligas"] == "TOP":
        return (country, league) in LIGAS_TOP

    return True


def jogo_valido(fx):
    minute = fx["fixture"]["status"].get("elapsed") or 0
    goals = (fx["goals"]["home"] or 0) + (fx["goals"]["away"] or 0)
    country = fx["league"]["country"]
    league = fx["league"]["name"]

    if not liga_ok(country, league):
        return False

    if minute < state["minuto_min"] or minute > state["minuto_max"]:
        return False

    if goals > 1:
        return False

    return True


# =========================================================
# BUSCAR JOGOS
# =========================================================

def buscar_jogos():
    data = api_get("/fixtures?live=all")
    resp = data.get("response", [])

    lista = []

    for fx in resp:
        try:
            if not jogo_valido(fx):
                continue

            fid = fx["fixture"]["id"]
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]
            minute = fx["fixture"]["status"].get("elapsed") or 0

            odd, book = get_odd(fid)

            if not odd:
                continue

            lista.append({
                "home": home,
                "away": away,
                "minute": minute,
                "odd": odd,
                "book": book or "-"
            })

        except:
            continue

    print("candidatos:", len(lista))
    return lista


# =========================================================
# ALERTA
# =========================================================

def enviar_alerta(j):
    busca = f"{j['home']} x {j['away']} bet365"
    link = f"https://www.google.com/search?q={quote_plus(busca)}"

    msg = (
        f"🔥 SINAL AO VIVO\n\n"
        f"⚽ {j['home']} x {j['away']}\n"
        f"⏱ {j['minute']}'\n\n"
        f"🎯 Over 0.5 HT\n"
        f"💸 Odd: {j['odd']} ({j['book']})\n\n"
        f"📲 AÇÃO:\n"
        f"1. Abra a bet365\n"
        f"2. Pesquise o jogo\n"
        f"3. Entre em Over 0.5 HT\n\n"
        f"🔎 {link}\n\n"
        f"⚠️ Stake: 2%"
    )

    send_telegram(msg)


# =========================================================
# MAIN
# =========================================================

def main():
    send_telegram("✅ BOT ONLINE")

    while True:
        process_updates()

        if not state["paused"]:
            jogos = buscar_jogos()

            if jogos:
                j = sorted(jogos, key=lambda x: (x["minute"], x["odd"]), reverse=True)[0]

                key = f"{j['home']}-{j['away']}"

                if key not in state["enviados"]:
                    enviar_alerta(j)
                    state["enviados"].append(key)
                    save_state(state)

        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
