import os
import time
import requests
from datetime import datetime, timezone

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://api.odds-api.io/v3"

# =========================
# CONFIG
# =========================
POLL_SECONDS = 300  # 5 min
BOOKMAKER = "Bet365"

# pré-jogo
PRE_MIN = 10
PRE_MAX = 60
MIN_ODD_HT = 1.30

# ao vivo (intervalo / começo 2T)
LIVE_MIN_MINUTE = 43
LIVE_MAX_MINUTE = 55
MIN_ODD_FT_LIVE = 1.30
MAX_ODD_FT_LIVE = 2.40

ALERTS_PER_HOUR = 5

# ligas aceitas
ALLOWED_LEAGUES = [
    "english premier league",
    "premier league (england)",
    "uefa champions league",
    "uefa europa league",
    "uefa europa conference league",
    "germany bundesliga",
    "france ligue 1",
    "italy serie a",
    "coppa italia",
    "fa cup",
    "efl cup",
    "coupe de france",
    "dfb pokal",
    "copa do brasil",
    "conmebol libertadores",
    "conmebol sudamericana",
    "liga profesional argentina",
    "copa argentina",
    "brazil serie a",
    "serie a (brazil)",
    "brasileirao"
]

BLOCKED_WORDS = [
    "uganda",
    "rwanda",
    "singapore",
    "malta",
    "algeria",
    "tunisia",
    "morocco"
]

# =========================
# STATE
# =========================
alert_times = []
alerted_pre = set()
alerted_live = set()
last_status_msg = 0
last_error_msg = None

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
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

def minutes_to_event(dt_str: str):
    event_dt = parse_dt(dt_str)
    now = datetime.now(timezone.utc)
    diff = event_dt - now
    return int(diff.total_seconds() // 60)

def league_ok(league_name: str):
    if not league_name:
        return False

    name = league_name.lower().strip()

    for bad in BLOCKED_WORDS:
        if bad in name:
            return False

    for item in ALLOWED_LEAGUES:
        if item in name:
            return True

    return False

# -------------------------
# leitura defensiva de minuto / placar
# porque o formato pode variar por competição/bookmaker/feed
# -------------------------
def get_event_minute(ev: dict):
    candidates = [
        ev.get("minute"),
        ev.get("elapsed"),
        (ev.get("time") or {}).get("minute") if isinstance(ev.get("time"), dict) else None,
        (ev.get("score") or {}).get("minute") if isinstance(ev.get("score"), dict) else None,
        (ev.get("live") or {}).get("minute") if isinstance(ev.get("live"), dict) else None,
    ]
    for c in candidates:
        try:
            if c is not None:
                return int(c)
        except:
            pass
    return None

def get_event_score(ev: dict):
    # tenta vários formatos possíveis
    home = None
    away = None

    score = ev.get("score")
    if isinstance(score, dict):
        for hk in ["home", "home_score", "homeScore"]:
            if hk in score:
                home = score.get(hk)
                break
        for ak in ["away", "away_score", "awayScore"]:
            if ak in score:
                away = score.get(ak)
                break

    if home is None:
        home = ev.get("home_score", ev.get("homeScore"))
    if away is None:
        away = ev.get("away_score", ev.get("awayScore"))

    try:
        home = int(home)
        away = int(away)
        return home, away
    except:
        return None, None

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
    # bookmaker no /events ajuda a economizar e filtrar melhor
    return api_get("/events", {
        "sport": "football",
        "bookmaker": BOOKMAKER,
        "limit": 100
    })

def get_live_events():
    return api_get("/events/live", {
        "sport": "football",
        "bookmaker": BOOKMAKER
    })

def get_event_odds(event_id: int):
    return api_get("/odds", {
        "eventId": event_id,
        "bookmakers": BOOKMAKER
    })

# =========================
# PARSER DE MERCADO
# =========================
def _try_float(v):
    try:
        return float(v)
    except:
        return None

def extract_market_odd(odds_json: dict, target: str):
    bookmakers = odds_json.get("bookmakers", {})
    if not bookmakers:
        return None

    for _, markets in bookmakers.items():
        for market in markets or []:
            market_name = str(market.get("name", "")).lower()
            odds_list = market.get("odds", []) or []

            is_ht = any(x in market_name for x in ["1st half", "first half", "1h", "half"])
            wants_ht = target == "HT_OVER_0_5"
            wants_ft = target == "FT_OVER_0_5"

            if wants_ht and not is_ht:
                continue
            if wants_ft and is_ht:
                continue

            for item in odds_list:
                if not isinstance(item, dict):
                    continue

                joined = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()

                if "over" in joined and "0.5" in joined:
                    for k in ["odd", "price", "value", "over", "Over"]:
                        if k in item:
                            val = _try_float(item[k])
                            if val is not None:
                                return val
    return None

# =========================
# SCAN PRÉ-JOGO
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

        status = str(ev.get("status") or "").lower()
        if status not in ["pending", "upcoming", "not_started", "scheduled"]:
            continue

        event_date = ev.get("date")
        if not event_date:
            continue

        mins = minutes_to_event(event_date)
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
                f"📚 Bookmaker: {BOOKMAKER}\n"
                "📌 Estratégia: entrada pré-jogo"
            )
            tg_send(msg)
            alerted_pre.add(event_id)
            record_alert()
            sent += 1

            if sent >= 3:
                break

# =========================
# SCAN AO VIVO
# =========================
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

        minute = get_event_minute(ev)
        if minute is None:
            continue

        if minute < LIVE_MIN_MINUTE or minute > LIVE_MAX_MINUTE:
            continue

        home_score, away_score = get_event_score(ev)
        if home_score is None or away_score is None:
            continue

        # sua estratégia: entrar no 45' se tiver 0-0
        if home_score != 0 or away_score != 0:
            continue

        odds_json = get_event_odds(event_id)
        ft_odd = extract_market_odd(odds_json, "FT_OVER_0_5")

        if ft_odd is None:
            continue

        if MIN_ODD_FT_LIVE <= ft_odd <= MAX_ODD_FT_LIVE:
            msg = (
                "⚽ ALERTA AO VIVO (0.5 FT)\n"
                f"🏆 {league_name}\n"
                f"{ev.get('home')} x {ev.get('away')}\n"
                f"⏱ Minuto: {minute}'\n"
                f"📊 Placar: {home_score}-{away_score}\n"
                f"🎲 O0.5 FT: {ft_odd:.2f}\n"
                f"📚 Bookmaker: {BOOKMAKER}\n"
                "📌 Estratégia: entrada no 45'/intervalo"
            )
            tg_send(msg)
            alerted_live.add(event_id)
            record_alert()
            sent += 1

            if sent >= 2:
                break

# =========================
# MAIN
# =========================
def main():
    global last_status_msg, last_error_msg

    tg_send("✅ Bot iniciado na Odds-API.io (pré + 45')")

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
            msg = f"❌ Erro bot: {e}"
            if msg != last_error_msg:
                tg_send(msg)
                last_error_msg = msg
            time.sleep(120)

if __name__ == "__main__":
    main()
