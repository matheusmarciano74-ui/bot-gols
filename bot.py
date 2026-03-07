import os
import time
import requests
from datetime import datetime, timezone

# =========================
# ENV
# =========================
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://api.odds-api.io/v3"

# =========================
# CONFIG
# =========================
POLL_SECONDS = 300  # 5 min
PRE_MIN = 10        # mínimo de minutos antes do jogo
PRE_MAX = 60        # máximo de minutos antes do jogo

# mercados/odds mínimos
MIN_ODD_HT = 1.30
MIN_ODD_FT_LIVE = 1.35

# limite de alertas por hora
ALERTS_PER_HOUR = 5

# bookmaker principal
BOOKMAKER = "Bet365"

# ligas que você quer
TARGET_LEAGUES = [
    "Premier League",
    "Championship",
    "FA Cup",
    "EFL Cup",
    "Ligue 1",
    "Coupe de France",
    "Bundesliga",
    "DFB Pokal",
    "Serie A",
    "Coppa Italia",
    "Brasileirao",
    "Serie A",
    "Copa do Brasil",
    "Liga Profesional Argentina",
    "Copa Argentina",
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana"
]

# =========================
# STATE
# =========================
alert_times = []
alerted_pre = set()
alerted_live = set()
last_status_msg = 0

# =========================
# TELEGRAM
# =========================
def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars missing")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=15)
    except Exception as e:
        print("Telegram error:", e)

# =========================
# HELPERS
# =========================
def cleanup_alert_times():
    global alert_times
    now = datetime.now()
    alert_times = [t for t in alert_times if (now - t).total_seconds() < 3600]

def can_alert():
    cleanup_alert_times()
    return len(alert_times) < ALERTS_PER_HOUR

def record_alert():
    alert_times.append(datetime.now())

def parse_dt(dt_str: str):
    # Ex.: 2025-10-15T15:00:00Z
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

def minutes_to_event(dt_str: str):
    event_dt = parse_dt(dt_str)
    now = datetime.now(timezone.utc)
    diff = event_dt - now
    return int(diff.total_seconds() // 60)

def league_ok(league_name: str):
    name = (league_name or "").lower()
    for item in TARGET_LEAGUES:
        if item.lower() in name:
            return True
    return False

# =========================
# ODDS API
# =========================
def api_get(path: str, params: dict):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY não configurada no Railway")

    params = dict(params)
    params["apiKey"] = ODDS_API_KEY

    url = f"{BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=20)

    if r.status_code == 401:
        raise RuntimeError("API key inválida")
    if r.status_code == 429:
        raise RuntimeError("Limite de requests atingido na Odds-API")
    if r.status_code >= 400:
        raise RuntimeError(f"Erro API {r.status_code}: {r.text[:200]}")

    return r.json()

def get_upcoming_events():
    # docs oficiais mostram /events com sport=football e limit
    return api_get("/events", {
        "sport": "football",
        "limit": 100
    })

def get_live_events():
    # docs oficiais mostram status=live
    return api_get("/events", {
        "sport": "football",
        "status": "live"
    })

def get_event_odds(event_id: int):
    # docs oficiais mostram /odds?eventId=...&bookmakers=...
    return api_get("/odds", {
        "eventId": event_id,
        "bookmakers": BOOKMAKER
    })

# =========================
# PARSER DE MERCADOS
# =========================
def _try_float(v):
    try:
        return float(v)
    except Exception:
        return None

def extract_market_odd(odds_json: dict, target: str):
    """
    target:
      - "HT_OVER_0_5"
      - "FT_OVER_0_5"

    Como os nomes de mercado podem variar por bookmaker,
    este parser tenta achar padrões comuns.
    Se não encontrar, retorna None.
    """
    bookmakers = odds_json.get("bookmakers", {})
    if not bookmakers:
        return None

    for book_name, markets in bookmakers.items():
        for market in markets or []:
            market_name = str(market.get("name", "")).lower()
            odds_list = market.get("odds", []) or []

            # mercados de interesse
            is_ht = any(x in market_name for x in ["1st half", "first half", "1h", "half"])
            is_ft = not is_ht

            wants_ht = (target == "HT_OVER_0_5")
            wants_ft = (target == "FT_OVER_0_5")

            # precisa ser o tipo certo
            if wants_ht and not is_ht:
                continue
            if wants_ft and not is_ft:
                # evita pegar mercado de primeiro tempo quando quer FT
                continue

            # tentar achar referência a over/under 0.5 no nome
            likely_ou = any(x in market_name for x in ["over", "under", "o/u", "ou", "totals", "total"])
            likely_half_point = "0.5" in market_name

            for item in odds_list:
                # caso 1: dict simples com chave "over"
                if isinstance(item, dict):
                    over_val = None

                    # formas mais comuns
                    for key in ["over", "Over", "o", "O"]:
                        if key in item:
                            over_val = _try_float(item[key])
                            if over_val is not None:
                                break

                    # caso 2: dicionário genérico com texto
                    joined = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()

                    if over_val is None:
                        # tenta achar em estruturas tipo {"label":"Over 0.5","odd":"1.35"}
                        if "over" in joined and "0.5" in joined:
                            for k in ["odd", "price", "value"]:
                                if k in item:
                                    over_val = _try_float(item[k])
                                    if over_val is not None:
                                        break

                    # valida contexto
                    if over_val is not None:
                        if likely_ou or ("over" in joined):
                            if likely_half_point or ("0.5" in joined):
                                return over_val

    return None

# =========================
# BOT LOGIC
# =========================
def scan_pregame():
    events = get_upcoming_events()
    sent = 0

    for ev in events:
        if not can_alert():
            break

        event_id = ev.get("id")
        if event_id in alerted_pre:
            continue

        league_name = (ev.get("league") or {}).get("name", "")
        if not league_ok(league_name):
            continue

        status = (ev.get("status") or "").lower()
        if status not in ["pending", "upcoming", "not_started", "scheduled"]:
            # upcoming docs mostram "pending"
            continue

        mins = minutes_to_event(ev.get("date"))
        if mins < PRE_MIN or mins > PRE_MAX:
            continue

        odds_json = get_event_odds(event_id)
        ht_odd = extract_market_odd(odds_json, "HT_OVER_0_5")

        if ht_odd is None:
            continue

        if ht_odd >= MIN_ODD_HT:
            msg = (
                "🚨 ALERTA PRÉ (0.5 HT)\n"
                f"🏆 {league_name}\n"
                f"{ev.get('home')} x {ev.get('away')}\n"
                f"⏳ Faltam {mins} min\n"
                f"🎲 O0.5 HT: {ht_odd:.2f}\n"
                f"📚 Bookmaker: {BOOKMAKER}"
            )
            tg_send(msg)
            alerted_pre.add(event_id)
            record_alert()
            sent += 1

            if sent >= 3:
                # trava extra por rodada
                break

def scan_live():
    events = get_live_events()
    sent = 0

    for ev in events:
        if not can_alert():
            break

        event_id = ev.get("id")
        if event_id in alerted_live:
            continue

        league_name = (ev.get("league") or {}).get("name", "")
        if not league_ok(league_name):
            continue

        odds_json = get_event_odds(event_id)
        ft_odd = extract_market_odd(odds_json, "FT_OVER_0_5")

        if ft_odd is None:
            continue

        if ft_odd >= MIN_ODD_FT_LIVE:
            msg = (
                "⚽ ALERTA AO VIVO (0.5 FT)\n"
                f"🏆 {league_name}\n"
                f"{ev.get('home')} x {ev.get('away')}\n"
                f"🎲 O0.5 FT: {ft_odd:.2f}\n"
                f"📚 Bookmaker: {BOOKMAKER}"
            )
            tg_send(msg)
            alerted_live.add(event_id)
            record_alert()
            sent += 1

            if sent >= 2:
                break

def main():
    global last_status_msg

    tg_send("✅ Bot iniciado na Odds-API.io")

    while True:
        try:
            now_ts = time.time()

            if now_ts - last_status_msg > 1800:
                cleanup_alert_times()
                tg_send(f"✅ BOT ON | alertas(60m): {len(alert_times)}/{ALERTS_PER_HOUR}")
                last_status_msg = now_ts

            scan_pregame()
            scan_live()

            time.sleep(POLL_SECONDS)

        except Exception as e:
            tg_send(f"❌ Erro bot: {e}")
            time.sleep(120)

if __name__ == "__main__":
    main()
