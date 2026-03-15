import os
import time
import requests
from datetime import datetime

API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"

POLL_SECONDS = 180
HEARTBEAT_SECONDS = 1800
MAX_ALERTS_PER_HOUR = 5

# ===== PARÂMETROS DA ESTRATÉGIA =====
HT_MIN_MINUTE = 18
HT_MAX_MINUTE = 37

FT_MIN_MINUTE = 22
FT_MAX_MINUTE = 65

# HT
HT_MIN_SHOTS = 7
HT_MIN_SOT = 2
HT_MIN_CORNERS = 2
HT_MIN_DANGER = 20

# FT
FT_MIN_SHOTS = 8
FT_MIN_SOT = 2
FT_MIN_CORNERS = 3
FT_MIN_DANGER = 22

# ===== LIGAS BOAS =====
TARGET_LEAGUES = [
    ("England", "Premier League"),
    ("Germany", "Bundesliga"),
    ("Italy", "Serie A"),
    ("Spain", "La Liga"),
    ("France", "Ligue 1"),
    ("Brazil", "Serie A"),
    ("Brazil", "Copa do Brasil"),
    ("USA", "Major League Soccer"),
    ("Netherlands", "Eredivisie"),
    ("Belgium", "First Division A"),
    ("Austria", "Bundesliga"),
    ("Switzerland", "Super League"),
    ("Denmark", "Superliga"),
    ("Norway", "Eliteserien"),
    ("Sweden", "Allsvenskan"),
    ("Turkey", "Süper Lig"),
    ("Argentina", "Liga Profesional"),
]

session = requests.Session()

alerted = set()
alerts_hour = []
last_update_id = None
start_time = datetime.now()
last_ok_loop = "-"
last_error = "-"
last_heartbeat = 0

last_total_live = 0
last_valid_league = 0
last_valid_table = 0
last_ht_window = 0
last_ft_window = 0
last_failed_stats = 0
last_alerts_sent = 0
loops = 0

# cache de tabela para reduzir requests
standings_cache = {}
STANDINGS_CACHE_TTL = 1800  # 30 min


def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Variáveis do Telegram ausentes")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    session.post(
        url,
        data={"chat_id": TG_CHAT, "text": msg},
        timeout=15,
    )


def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"timeout": 5}
    if offset is not None:
        params["offset"] = offset

    r = session.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def api(path: str, params: dict):
    headers = {"x-apisports-key": API_KEY}
    r = session.get(API + path, headers=headers, params=params, timeout=25)
    r.raise_for_status()
    j = r.json()

    if j.get("errors"):
        raise RuntimeError(str(j["errors"]))

    return j.get("response", [])


def league_ok(country: str, league: str) -> bool:
    for c, l in TARGET_LEAGUES:
        if country == c and league == l:
            return True
    return False


def bet365_link(home: str, away: str) -> str:
    return f"https://www.google.com/search?q=bet365+{home}+vs+{away}"


def can_alert() -> bool:
    now = time.time()
    alerts_hour[:] = [t for t in alerts_hour if now - t < 3600]
    return len(alerts_hour) < MAX_ALERTS_PER_HOUR


def get_standings_cached(league_id: int, season: int):
    key = (league_id, season)
    now = time.time()

    cached = standings_cache.get(key)
    if cached and now - cached["ts"] < STANDINGS_CACHE_TTL:
        return cached["data"]

    data = api("/standings", {"league": league_id, "season": season})
    standings_cache[key] = {"ts": now, "data": data}
    return data


def check_table(league_id: int, season: int, home: str, away: str):
    data = get_standings_cached(league_id, season)

    if not data:
        return None, None

    try:
        standings_groups = data[0]["league"]["standings"]
    except Exception:
        return None, None

    pos_h = None
    pos_a = None

    for group in standings_groups:
        for row in group:
            name = row["team"]["name"]

            if name == home:
                pos_h = row["rank"]

            if name == away:
                pos_a = row["rank"]

    return pos_h, pos_a


def table_ok(pos_h, pos_a) -> bool:
    """
    Regra equilibrada:
    - bloqueia ambos muito ruins
    - bloqueia confronto equilibrado na parte de baixo
    - permite favorito forte x time ruim
    """
    if pos_h is None or pos_a is None:
        return False

    if pos_h >= 15 and pos_a >= 15:
        return False

    if pos_h >= 13 and pos_a >= 13 and abs(pos_h - pos_a) <= 3:
        return False

    return True


def get_stats(fid: int):
    stats = api("/fixtures/statistics", {"fixture": fid})

    shots = 0
    sot = 0
    corners = 0
    danger = 0

    for team in stats:
        for s in team.get("statistics", []):
            stype = s.get("type")
            sval = s.get("value") or 0

            if stype == "Total Shots":
                shots += sval
            elif stype == "Shots on Goal":
                sot += sval
            elif stype == "Corner Kicks":
                corners += sval
            elif stype == "Dangerous Attacks":
                danger += sval

    return shots, sot, corners, danger


def ht_rule(minute, score, shots, sot, corners, danger) -> bool:
    if not (HT_MIN_MINUTE <= minute <= HT_MAX_MINUTE):
        return False

    if score != "0-0":
        return False

    if shots >= HT_MIN_SHOTS and sot >= HT_MIN_SOT and corners >= HT_MIN_CORNERS and danger >= HT_MIN_DANGER:
        return True

    if shots >= 10 and sot >= 3 and danger >= 18:
        return True

    return False


def ft_rule(minute, score, shots, sot, corners, danger) -> bool:
    if not (FT_MIN_MINUTE <= minute <= FT_MAX_MINUTE):
        return False

    if score not in ["0-0", "1-0", "0-1"]:
        return False

    if shots >= FT_MIN_SHOTS and sot >= FT_MIN_SOT and corners >= FT_MIN_CORNERS and danger >= FT_MIN_DANGER:
        return True

    if shots >= 11 and sot >= 3 and danger >= 20:
        return True

    return False
