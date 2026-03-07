import os
import time
import requests
from datetime import datetime, date, timezone

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
POLL_SECONDS = 120
SUMMARY_SECONDS = 600
ALERTS_PER_HOUR = 8

PRE_MIN = 10
PRE_MAX = 60
MIN_ODD_HT = 1.30

WATCH_MIN = 35
WATCH_MAX = 42

ENTRY_MIN = 43
ENTRY_MAX = 70

MIN_ODD_FT = 1.30
MAX_ODD_FT = 2.60

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
    "Morocco",
    "India",
    "Indonesia"
]

LEAGUE_PRIORITY = {
    "Premier League": 12,
    "Bundesliga": 12,
    "Serie A": 11,
    "UEFA Champions League": 12,
    "UEFA Europa League": 10,
    "UEFA Europa Conference League": 9,
    "Ligue 1": 10,
    "Copa do Brasil": 10,
    "Serie A (Brazil)": 10,
    "Brasileirao": 10,
    "CONMEBOL Libertadores": 11,
    "CONMEBOL Sudamericana": 9,
    "Liga Profesional Argentina": 8,
    "Copa Argentina": 7,
    "FA Cup": 8,
    "EFL Cup": 7,
    "Coppa Italia": 8,
    "DFB Pokal": 8,
    "Coupe de France": 8,
    "Championship": 7,
    "League Cup": 7
}

# =========================
# STATE
# =========================
alert_times = []
watched_live = set()
alerted_live = set()
alerted_pre = set()
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

    low = name.lower()

    for b in BLOCKED:
        if b.lower() in low:
            return False

    for t in TARGET_LEAGUES:
        if t.lower() in low:
            return True

    return False

def league_score(name: str):
    if not name:
        return 0
    for k, v in LEAGUE_PRIORITY.items():
        if k.lower() in name.lower():
            return v
    return 5

def norm_team_name(name: str) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    replacements = {
        " fc": "",
        " cf": "",
        " sc": "",
        " ac ": " ",
        "-": " ",
        ".": "",
        ",": "",
        "  ": " ",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    return " ".join(s.split())

def parse_dt(dt_str: str):
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))

def minutes_to_event(dt_str: str):
    event_dt = parse_dt(dt_str)
    now = datetime.now(timezone.utc)
    diff = event_dt - now
    return int(diff.total_seconds() // 60)

# =========================
# API FOOTBALL
# =========================
def api(path: str, params: dict):
    if not API_KEY:
        raise RuntimeError("API_FOOTBALL_KEY não configurada no Railway")

    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=25)

    if r.status_code >= 400:
        raise RuntimeError(f"Erro API-Football {r.status_code}: {r.text[:200]}")

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

def get_upcoming_odds_events():
    return odds_api_get("/events", {
        "sport": "football",
        "bookmaker": BOOKMAKER,
        "limit": 150
    })

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

# =========================
# ODDS PARSER
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

                text = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()

                if "over" in text and "0.5" in text:
                    for k in ["odd", "price", "value", "over", "Over"]:
                        if k in item:
                            val = _try_float(item[k])
                            if val is not None:
                                return val
    return None

# =========================
# API FOOTBALL DATA
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
    dangerous = 0
    possession_home = 0
    possession_away = 0

    for idx, team in enumerate(stats):
        for s in team.get("statistics", []) or []:
            t = s.get("type")
            v = s.get("value")

            if t == "Total Shots":
                shots += v or 0
            elif t == "Shots on Goal":
                sot += v or 0
            elif t == "Corner Kicks":
                corners += v or 0
            elif t == "Dangerous Attacks":
                dangerous += v or 0
            elif t == "Ball Possession":
                try:
                    val = int(str(v).replace("%", "").strip())
                    if idx == 0:
                        possession_home = val
                    else:
                        possession_away = val
                except:
                    pass

    max_possession = max(possession_home, possession_away)
    return shots, sot, corners, dangerous, max_possession

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
# MATCH ODDS <-> FIXTURE
# =========================
def find_matching_odds_event_id(home_name: str, away_name: str, odds_events: list):
    home_norm = norm_team_name(home_name)
    away_norm = norm_team_name(away_name)

    for ev in odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))
        if home_norm == oh and away_norm == oa:
            return ev.get("id")

    for ev in odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))

        if (home_norm in oh or oh in home_norm) and (away_norm in oa or oa in away_norm):
            return ev.get("id")

    return None

# =========================
# PRESSURE SCORE
# =========================
def pressure_score(league: str, minute: int, shots: int, sot: int, corners: int, dangerous: int, max_possession: int):
    score = 0

    # chutes
    if shots >= 8:
        score += 10
    if shots >= 10:
        score += 8
    if shots >= 12:
        score += 6

    # no alvo
    if sot >= 2:
        score += 12
    if sot >= 3:
        score += 8
    if sot >= 4:
        score += 5

    # escanteios
    if corners >= 3:
        score += 8
    if corners >= 5:
        score += 5

    # ataques perigosos
    if dangerous >= 30:
        score += 10
    if dangerous >= 40:
        score += 10
    if dangerous >= 55:
        score += 5

    # posse
    if max_possession >= 55:
        score += 4
    if max_possession >= 60:
        score += 4

    # minuto mais maduro
    if 43 <= minute <= 55:
        score += 8
    elif 56 <= minute <= 70:
        score += 5

    # peso da liga
    score += league_score(league)

    return min(score, 100)

def classify_score(score: int):
    if score >= 55:
        return "FORTE"
    if score >= 42:
        return "MODERADA"
    return "FRACA"

# =========================
# PRE-GAME HT
# =========================
def scan_pregame():
    sent = 0
    checked_odds = 0

    try:
        events = get_upcoming_odds_events()
    except Exception:
        return {"pre_checked": 0, "pre_sent": 0}

    for ev in events:
        if not can_alert():
            break

        event_id = ev.get("id")
        if event_id in alerted_pre:
            continue

        league_name = str((ev.get("league") or {}).get("name", ""))
        if not league_ok(league_name):
            continue

        event_date = ev.get("date")
        if not event_date:
            continue

        mins = minutes_to_event(event_date)
        if mins < PRE_MIN or mins > PRE_MAX:
            continue

        odds_json = get_event_odds(event_id)
        checked_odds += 1
        ht_odd = extract_market_odd(odds_json, "HT_OVER_0_5")

        if ht_odd is None:
            continue

        if ht_odd >= MIN_ODD_HT:
            msg = (
                "🚨 ALERTA PRÉ (0.5 HT)\n"
                f"🏆 {league_name}\n"
                f"{ev.get('home')} x {ev.get('away')}\n"
                f"⏳ Faltam {mins} min\n"
                f"🎲 Odd O0.5 HT: {ht_odd:.2f}\n"
                f"📚 Bookmaker: {BOOKMAKER}\n"
                "✅ Entrada pré-jogo com odd mínima 1.30"
            )
            tg_send(msg)
            alerted_pre.add(event_id)
            record_alert()
            sent += 1

            if sent >= 3:
                break

    return {"pre_checked": checked_odds, "pre_sent": sent}

# =========================
# LIVE LOGIC
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
                    "📌 Se seguir 0-0 e com pressão, pode virar entrada"
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

        shots, sot, corners, dangerous, max_possession = get_stats(fid)
        stats_checked += 1

        score_pressure = pressure_score(
            g["league"], minute, shots, sot, corners, dangerous, max_possession
        )
        level = classify_score(score_pressure)

        if score_pressure < 42:
            continue

        matched_event_id = find_matching_odds_event_id(g["home"], g["away"], live_odds_events)
        if not matched_event_id:
            continue

        odds_checked += 1
        odds_json = get_event_odds(matched_event_id)
        odd_ft = extract_market_odd(odds_json, "FT_OVER_0_5")

        if odd_ft is None:
            continue

        odds_found += 1

        if MIN_ODD_FT <= odd_ft <= MAX_ODD_FT:
            icon = "⚽" if level == "FORTE" else "⚠️"
            msg = (
                f"{icon} ENTRADA {level} (0.5 FT)\n"
                f"🏆 {g['league']}\n"
                f"{g['home']} x {g['away']}\n"
                f"⏱ Minuto: {minute}'\n"
                f"📊 Placar: {score}\n"
                f"📈 Chutes: {shots}\n"
                f"🎯 No alvo: {sot}\n"
                f"🚩 Escanteios: {corners}\n"
                f"🔥 Ataques perigosos: {dangerous}\n"
                f"📌 Posse máx: {max_possession}%\n"
                f"🧠 Score pressão: {score_pressure}\n"
                f"🎲 Odd O0.5 FT: {odd_ft:.2f}\n"
                "✅ Pressão + odd mínima 1.30"
            )
            tg_send(msg)
            alerted_live.add(fid)
            record_alert()
            alerts_entry += 1

    now_ts = time.time()
    if now_ts - last_summary > SUMMARY_SECONDS:
        pre_info = scan_pregame()

        tg_send(
            "📊 RESUMO V5\n"
            f"ao vivo: {total}\n"
            f"0-0: {zero_zero}\n"
            f"faixa observação 35-42: {watch_zone}\n"
            f"faixa entrada 43-70: {entry_zone}\n"
            f"stats consultadas: {stats_checked}\n"
            f"odds consultadas live: {odds_checked}\n"
            f"odds encontradas live: {odds_found}\n"
            f"obs enviadas: {alerts_obs}\n"
            f"entradas live enviadas: {alerts_entry}\n"
            f"odds pré consultadas: {pre_info['pre_checked']}\n"
            f"entradas pré enviadas: {pre_info['pre_sent']}"
        )
        last_summary = now_ts

# =========================
# MAIN
# =========================
def main():
    global last_error

    tg_send("✅ Bot V5 iniciado (score de pressão + odd 1.30)")

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
