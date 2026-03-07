import os
import time
import requests
from datetime import datetime, date

# =========================
# ENV
# =========================
API_KEY = os.getenv("API_FOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"
ODDS_BASE_URL = "https://api.odds-api.io/v3"
TZ = "America/Sao_Paulo"

# =========================
# CONFIG
# =========================
POLL_SECONDS = 300          # 5 min
ALERTS_PER_HOUR = 5
DEBUG_SUMMARY_SECONDS = 600 # 10 min

WATCH_MIN = 35
WATCH_MAX = 42

ENTRY_MIN = 43
ENTRY_MAX = 55

MIN_SHOTS = 8
MIN_SOT = 2
MIN_CORNERS = 3
MIN_ODD_FT = 1.30

BOOKMAKER = "Bet365"

TARGET_LEAGUES = [
    "Premier League",
    "Championship",
    "FA Cup",
    "League Cup",
    "EFL Cup",
    "Ligue 1",
    "Coupe de France",
    "Bundesliga",
    "DFB Pokal",
    "Serie A",
    "Coppa Italia",
    "Copa do Brasil",
    "Serie A (Brazil)",
    "Brasileirao",
    "Liga Profesional Argentina",
    "Copa Argentina",
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana"
]

BLOCKED = [
    "Uganda",
    "Rwanda",
    "Singapore",
    "Malta",
    "Algeria",
    "Tunisia",
    "Morocco"
]

# =========================
# STATE
# =========================
alert_times = []
watched_live = set()
alerted_live = set()
last_summary = 0
last_error = None

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

def league_ok(name: str):
    if not name:
        return False

    for b in BLOCKED:
        if b.lower() in name.lower():
            return False

    for t in TARGET_LEAGUES:
        if t.lower() in name.lower():
            return True

    return False

def norm_team_name(name: str) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    replacements = {
        " fc": "",
        " cf": "",
        " sc": "",
        " ac ": " ",
        "  ": " ",
        "-": " ",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    return " ".join(s.split())

# =========================
# API FOOTBALL
# =========================
def api(path: str, params: dict):
    if not API_KEY:
        raise RuntimeError("API_FOOTBALL_KEY não configurada no Railway")

    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=25)

    if r.status_code >= 400:
        raise RuntimeError(f"Erro API {r.status_code}: {r.text[:200]}")

    j = r.json()

    if j.get("errors"):
        raise RuntimeError(str(j["errors"]))

    return j.get("response", []) or []

# =========================
# ODDS API
# =========================
def odds_api_get(path: str, params: dict):
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY não configurada no Railway")

    params = dict(params)
    params["apiKey"] = ODDS_API_KEY

    url = f"{ODDS_BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=20)

    if r.status_code >= 400:
        raise RuntimeError(f"Erro Odds API {r.status_code}: {r.text[:200]}")

    return r.json()

def get_live_odds_events():
    return odds_api_get("/events/live", {
        "sport": "football",
        "bookmaker": BOOKMAKER
    })

def get_event_odds(event_id: int):
    return odds_api_get("/odds", {
        "eventId": event_id,
        "bookmakers": BOOKMAKER
    })

def extract_ft_over05_odd(odds_json: dict):
    bookmakers = odds_json.get("bookmakers", {})
    if not bookmakers:
        return None

    for _, markets in bookmakers.items():
        for market in markets or []:
            market_name = str(market.get("name", "")).lower()

            # ignora mercados de 1º tempo
            if "half" in market_name:
                continue

            odds_list = market.get("odds", []) or []

            for item in odds_list:
                if not isinstance(item, dict):
                    continue

                text = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()

                if "over" in text and "0.5" in text:
                    for k in ["odd", "price", "value", "over", "Over"]:
                        if k in item:
                            try:
                                return float(item[k])
                            except:
                                pass

    return None

# =========================
# LIVE GAMES
# =========================
def get_today_fixtures():
    today = date.today().isoformat()
    return api("/fixtures", {
        "date": today,
        "timezone": TZ
    })

def get_stats(fid: int):
    stats = api("/fixtures/statistics", {"fixture": fid})

    shots = 0
    sot = 0
    corners = 0

    for team in stats:
        for s in team.get("statistics", []) or []:
            if s["type"] == "Total Shots":
                shots += s["value"] or 0
            elif s["type"] == "Shots on Goal":
                sot += s["value"] or 0
            elif s["type"] == "Corner Kicks":
                corners += s["value"] or 0

    return shots, sot, corners

def get_live_candidates():
    fixtures = get_today_fixtures()
    out = []

    for f in fixtures:
        status = f["fixture"]["status"]["short"]
        if status not in ["1H", "HT", "2H"]:
            continue

        league = f["league"]["name"]
        if not league_ok(league):
            continue

        minute = f["fixture"]["status"]["elapsed"] or 0
        home_goals = f["goals"]["home"] or 0
        away_goals = f["goals"]["away"] or 0

        out.append({
            "id": f["fixture"]["id"],
            "league": league,
            "home": f["teams"]["home"]["name"],
            "away": f["teams"]["away"]["name"],
            "minute": minute,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "score": f"{home_goals}-{away_goals}",
        })

    return out

# =========================
# MATCH ODDS EVENT
# =========================
def find_matching_odds_event_id(home_name: str, away_name: str, live_odds_events: list):
    home_norm = norm_team_name(home_name)
    away_norm = norm_team_name(away_name)

    for ev in live_odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))

        if home_norm == oh and away_norm == oa:
            return ev.get("id")

    # fallback mais flexível
    for ev in live_odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))

        if home_norm in oh or oh in home_norm:
            if away_norm in oa or oa in away_norm:
                return ev.get("id")

    return None

# =========================
# MAIN LOGIC
# =========================
def scan_live():
    global last_summary

    games = get_live_candidates()

    total = len(games)
    zero_zero = 0
    watch_zone = 0
    entry_zone = 0
    stats_checked = 0
    odds_checked = 0
    odds_found = 0
    alerts_obs = 0
    alerts_entry = 0

    try:
        live_odds_events = get_live_odds_events()
    except Exception:
        live_odds_events = []

    for g in games:
        minute = g["minute"]
        fid = g["id"]
        score = g["score"]

        if score == "0-0":
            zero_zero += 1

        # OBSERVAÇÃO
        if WATCH_MIN <= minute <= WATCH_MAX and score == "0-0":
            watch_zone += 1

            if fid not in watched_live:
                msg = (
                    "👀 OBSERVAÇÃO AO VIVO\n"
                    f"🏆 {g['league']}\n"
                    f"{g['home']} x {g['away']}\n"
                    f"⏱ Minuto: {minute}'\n"
                    f"📊 Placar: {score}\n"
                    "📌 Se seguir 0-0 e com pressão, pode virar entrada no intervalo"
                )
                tg_send(msg)
                watched_live.add(fid)
                alerts_obs += 1

        # ENTRADA
        if not can_alert():
            break

        if fid in alerted_live:
            continue

        if not (ENTRY_MIN <= minute <= ENTRY_MAX):
            continue

        entry_zone += 1

        if score != "0-0":
            continue

        shots, sot, corners = get_stats(fid)
        stats_checked += 1

        if shots < MIN_SHOTS or sot < MIN_SOT or corners < MIN_CORNERS:
            continue

        matched_event_id = find_matching_odds_event_id(g["home"], g["away"], live_odds_events)
        if not matched_event_id:
            continue

        odds_checked += 1
        odds_json = get_event_odds(matched_event_id)
        odd_ft = extract_ft_over05_odd(odds_json)

        if odd_ft is None:
            continue

        odds_found += 1

        if odd_ft >= MIN_ODD_FT:
            msg = (
                "⚽ ENTRADA AO VIVO (0.5 FT)\n"
                f"🏆 {g['league']}\n"
                f"{g['home']} x {g['away']}\n"
                f"⏱ Minuto: {minute}'\n"
                f"📊 Placar: {score}\n"
                f"📈 Chutes: {shots}\n"
                f"🎯 No alvo: {sot}\n"
                f"🚩 Escanteios: {corners}\n"
                f"🎲 Odd O0.5 FT: {odd_ft:.2f}\n"
                "✅ Jogo com pressão + odd mínima 1.30"
            )
            tg_send(msg)
            alerted_live.add(fid)
            record_alert()
            alerts_entry += 1

    now_ts = time.time()
    if now_ts - last_summary > DEBUG_SUMMARY_SECONDS:
        tg_send(
            "📊 RESUMO LIVE V3\n"
            f"jogos ao vivo: {total}\n"
            f"0-0: {zero_zero}\n"
            f"faixa observação 35-42: {watch_zone}\n"
            f"faixa entrada 43-55: {entry_zone}\n"
            f"stats consultadas: {stats_checked}\n"
            f"odds consultadas: {odds_checked}\n"
            f"odds encontradas: {odds_found}\n"
            f"obs enviadas: {alerts_obs}\n"
            f"entradas enviadas: {alerts_entry}"
        )
        last_summary = now_ts

# =========================
# MAIN
# =========================
def main():
    global last_error

    tg_send("✅ Bot V3 iniciado (pressão + odd mínima 1.30)")

    while True:
        try:
            scan_live()
            time.sleep(POLL_SECONDS)

        except Exception as e:
            msg = f"❌ Erro bot: {e}"
            if msg != last_error:
                tg_send(msg)
                last_error = msg
            time.sleep(120)

if __name__ == "__main__":
    main()
