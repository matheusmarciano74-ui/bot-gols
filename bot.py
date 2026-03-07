import os
import time
import requests
from datetime import datetime, date, timedelta

# =========================
# ENV
# =========================
API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"
TZ = "America/Sao_Paulo"

# =========================
# MODO FREE (100 req/dia)
# =========================
POLL_SECONDS = 1800          # 30 min
DAILY_LIMIT = 85             # trava antes de 100
ACTIVE_HOUR_START = 10       # só trabalha entre 10:00
ACTIVE_HOUR_END = 23         # e 23:59

# PRÉ-JOGO
PRE_MIN = 15                 # faltam no mínimo 15 min
PRE_MAX = 90                 # faltam no máximo 90 min
MAX_PREGAME_FIXTURES = 3     # analisa no máximo 3 jogos por rodada
PRE_SCORE_MIN = 75           # score para alertar

# AO VIVO
LIVE_MINUTE_MIN = 30
MAX_LIVE_STATS_PER_LOOP = 1  # no máximo 1 chamada de stats por rodada
LIVE_SHOTS_MIN = 8
LIVE_SOT_MIN = 2
LIVE_CORNERS_MIN = 3

# ALERTAS
ALERTS_PER_HOUR = 5

# =========================
# LIGAS IMPORTANTES
# =========================
TARGET_LEAGUES = [
    "Premier League",
    "Championship",
    "FA Cup",
    "League Cup",
    "Ligue 1",
    "Ligue 2",
    "Coupe de France",
    "Bundesliga",
    "DFB Pokal",
    "Serie A",
    "Coppa Italia",
    "Serie A (Brazil)",
    "Serie B (Brazil)",
    "Copa do Brasil",
    "Liga Profesional Argentina",
    "Copa de la Liga Profesional",
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana"
]

# prioridade simples de liga (ajuda o pré-jogo)
LEAGUE_PRIORITY = {
    "Premier League": 10,
    "UEFA Champions League": 10,
    "UEFA Europa League": 9,
    "UEFA Europa Conference League": 8,
    "Bundesliga": 9,
    "Ligue 1": 8,
    "Serie A": 8,
    "Serie A (Brazil)": 8,
    "Copa do Brasil": 8,
    "Liga Profesional Argentina": 7,
    "CONMEBOL Libertadores": 9,
    "CONMEBOL Sudamericana": 8,
    "FA Cup": 7,
    "Coppa Italia": 7,
    "DFB Pokal": 7,
    "Coupe de France": 7,
}

# =========================
# CONTADOR DE REQUISIÇÕES
# =========================
req_count = 0
req_day = date.today().isoformat()

def reset_counter_if_new_day():
    global req_count, req_day
    today = date.today().isoformat()
    if today != req_day:
        req_day = today
        req_count = 0

def can_call_api():
    reset_counter_if_new_day()
    return req_count < DAILY_LIMIT

def inc_req():
    global req_count
    req_count += 1

# =========================
# TELEGRAM
# =========================
def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars missing.")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=15)
    except Exception as e:
        print("Telegram error:", e)

# =========================
# API
# =========================
last_api_error_msg = None

def api(path: str, params: dict):
    global last_api_error_msg

    if not can_call_api():
        return []

    if not API_KEY:
        tg_send("❌ ERRO: API_FOOTBALL_KEY não encontrada.")
        return []

    headers = {"x-apisports-key": API_KEY}

    try:
        r = requests.get(API + path, headers=headers, params=params, timeout=20)
        inc_req()
        j = r.json()
    except Exception as e:
        print("API request error:", e)
        return []

    if j.get("errors"):
        err = str(j["errors"])
        if err != last_api_error_msg:
            tg_send(f"❌ API ERR {path}: {err}")
            last_api_error_msg = err
        return []

    return j.get("response", []) or []

# =========================
# HORÁRIO
# =========================
def now_local():
    return datetime.now()

def in_active_hours():
    h = now_local().hour
    return ACTIVE_HOUR_START <= h <= ACTIVE_HOUR_END

def mins_to_kickoff(iso_dt: str) -> int:
    try:
        ko = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
        now = datetime.now(ko.tzinfo)
    except Exception:
        ko = datetime.strptime(iso_dt[:19], "%Y-%m-%dT%H:%M:%S")
        now = datetime.now()

    diff = ko - now
    return int(diff.total_seconds() // 60)

# =========================
# FIXTURES
# =========================
def get_fixtures_today():
    today = date.today().isoformat()
    return api("/fixtures", {"date": today, "timezone": TZ})

# =========================
# HISTÓRICO DOS TIMES
# cache diário em memória
# =========================
team_cache = {}  # team_id -> {"rate": x, "avg": y}

def get_last_fixtures(team_id: int, last: int = 10):
    return api("/fixtures", {"team": team_id, "last": last, "timezone": TZ})

def calc_ht_stats(last_fixtures: list) -> dict:
    total = 0
    ht_with_goal = 0
    ht_goals_sum = 0

    for fx in last_fixtures:
        ht = (fx.get("score", {}) or {}).get("halftime", {}) or {}
        home_ht = ht.get("home") or 0
        away_ht = ht.get("away") or 0
        g = home_ht + away_ht

        total += 1
        ht_goals_sum += g
        if g >= 1:
            ht_with_goal += 1

    if total == 0:
        return {"rate": 0.0, "avg": 0.0}

    return {
        "rate": (ht_with_goal / total) * 100.0,
        "avg": ht_goals_sum / total
    }

def get_team_ht_stats(team_id: int):
    if team_id in team_cache:
        return team_cache[team_id]

    last_fx = get_last_fixtures(team_id, 10)
    stats = calc_ht_stats(last_fx)
    team_cache[team_id] = stats
    return stats

# =========================
# SCORE PRÉ-JOGO
# =========================
def score_pregame(home_stats: dict, away_stats: dict, mins: int, league_name: str) -> int:
    s = 0

    def pts(t):
        p = 0
        if t["rate"] >= 70:
            p += 15
        if t["rate"] >= 80:
            p += 10
        if t["avg"] >= 1.10:
            p += 5
        return p

    # tendência HT
    s += pts(home_stats)
    s += pts(away_stats)

    # encaixe simples
    if home_stats["avg"] >= 1.10 and away_stats["avg"] >= 1.10:
        s += 12
    if home_stats["rate"] >= 75 and away_stats["rate"] >= 75:
        s += 13

    # timing
    if 25 <= mins <= 45:
        s += 15
    elif 15 <= mins <= 24:
        s += 8
    elif 46 <= mins <= 60:
        s += 5

    # prioridade da liga
    s += LEAGUE_PRIORITY.get(league_name, 5)

    return min(100, int(round(s)))

# =========================
# AO VIVO
# =========================
def get_stats(fid: int):
    stats = api("/fixtures/statistics", {"fixture": fid})

    shots = 0
    sot = 0
    corners = 0

    for t in stats:
        for s in t.get("statistics", []) or []:
            if s["type"] == "Total Shots":
                shots += s["value"] or 0
            if s["type"] == "Shots on Goal":
                sot += s["value"] or 0
            if s["type"] == "Corner Kicks":
                corners += s["value"] or 0

    return shots, sot, corners

# =========================
# ALERTAS POR HORA
# =========================
alert_times = []

def cleanup_alert_times():
    global alert_times
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    alert_times = [t for t in alert_times if t >= one_hour_ago]

def can_alert_now():
    cleanup_alert_times()
    return len(alert_times) < ALERTS_PER_HOUR

def record_alert():
    alert_times.append(datetime.now())

# =========================
# MAIN
# =========================
def main():
    tg_send("✅ Bot gols iniciado (FREE 100 req/dia otimizado)")

    alerted_pre = set()
    alerted_live = set()

    last_heartbeat = 0
    last_sleep_log = 0
    last_scan_log = 0

    while True:
        try:
            reset_counter_if_new_day()

            # fora do horário útil = não chama API
            if not in_active_hours():
                if time.time() - last_sleep_log > 7200:
                    tg_send("🌙 Fora do horário útil | bot em modo descanso")
                    last_sleep_log = time.time()
                time.sleep(POLL_SECONDS)
                continue

            # heartbeat a cada 2h
            if time.time() - last_heartbeat > 7200:
                cleanup_alert_times()
                tg_send(f"✅ BOT ON | req hoje: {req_count}/{DAILY_LIMIT} | alertas(60m): {len(alert_times)}/{ALERTS_PER_HOUR}")
                last_heartbeat = time.time()

            # se bateu o limite local, para
            if not can_call_api():
                tg_send(f"⛔ LIMITE LOCAL atingido | req hoje: {req_count}/{DAILY_LIMIT}")
                time.sleep(POLL_SECONDS)
                continue

            fixtures = get_fixtures_today()

            # log do scan a cada 2h
            if time.time() - last_scan_log > 7200:
                tg_send(f"🔎 SCAN | fixtures recebidos: {len(fixtures)} | req hoje: {req_count}/{DAILY_LIMIT}")
                last_scan_log = time.time()

            # -------------------------
            # PRÉ-JOGO
            # -------------------------
            pre_list = []

            for f in fixtures:
                status = f["fixture"]["status"]["short"]
                if status != "NS":
                    continue

                league = f["league"]["name"]
                if league not in TARGET_LEAGUES:
                    continue

                mins = mins_to_kickoff(f["fixture"]["date"])
                if mins < PRE_MIN or mins > PRE_MAX:
                    continue

                pre_list.append((mins, f))

            # ordena por proximidade + prioridade
            pre_list.sort(key=lambda x: (x[0], -LEAGUE_PRIORITY.get(x[1]["league"]["name"], 5)))

            # analisa no máximo 3 jogos por rodada
            for mins, f in pre_list[:MAX_PREGAME_FIXTURES]:
                if not can_alert_now():
                    break

                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                home_id = f["teams"]["home"]["id"]
                away_id = f["teams"]["away"]["id"]

                home_stats = get_team_ht_stats(home_id)
                away_stats = get_team_ht_stats(away_id)

                score = score_pregame(home_stats, away_stats, mins, f["league"]["name"])

                if score >= PRE_SCORE_MIN:
                    league = f["league"]["name"]
                    home = f["teams"]["home"]["name"]
                    away = f["teams"]["away"]["name"]

                    msg = (
                        "🚨 ALERTA PRÉ (0.5 HT)\n"
                        f"🏆 {league}\n"
                        f"{home} x {away}\n"
                        f"⏳ Faltam {mins} min\n"
                        f"📈 Score: {score}/100\n"
                        f"📌 HT%: {home_stats['rate']:.0f}% vs {away_stats['rate']:.0f}%\n"
                        f"📊 HT avg: {home_stats['avg']:.2f} vs {away_stats['avg']:.2f}\n"
                        "✅ Ação: olhar Over 0.5 HT no app antes do jogo começar"
                    )
                    tg_send(msg)
                    alerted_pre.add(fid)
                    record_alert()

            # -------------------------
            # AO VIVO
            # -------------------------
            live_candidates = []

            for f in fixtures:
                status = f["fixture"]["status"]["short"]
                if status not in ["1H", "2H", "HT"]:
                    continue

                league = f["league"]["name"]
                if league not in TARGET_LEAGUES:
                    continue

                minute = f["fixture"]["status"]["elapsed"] or 0
                ghome = f["goals"]["home"] or 0
                gaway = f["goals"]["away"] or 0

                if (ghome + gaway) != 0:
                    continue
                if minute < LIVE_MINUTE_MIN:
                    continue

                live_candidates.append({
                    "id": f["fixture"]["id"],
                    "league": league,
                    "home": f["teams"]["home"]["name"],
                    "away": f["teams"]["away"]["name"],
                    "minute": minute
                })

            checked_live = 0
            for g in live_candidates:
                if checked_live >= MAX_LIVE_STATS_PER_LOOP:
                    break

                if not can_alert_now():
                    break

                fid = g["id"]
                if fid in alerted_live:
                    continue

                shots, sot, corners = get_stats(fid)
                checked_live += 1

                if shots >= LIVE_SHOTS_MIN and sot >= LIVE_SOT_MIN and corners >= LIVE_CORNERS_MIN:
                    msg = (
                        "⚽ POSSÍVEL GOL (AO VIVO)\n"
                        f"🏆 {g['league']}\n"
                        f"{g['home']} x {g['away']}\n"
                        f"⏱ {g['minute']}' | 0-0\n"
                        f"📊 Chutes {shots}\n"
                        f"🎯 No alvo {sot}\n"
                        f"🚩 Escanteios {corners}\n"
                        "➡ Sinal de jogo quente pra sua recuperação ao vivo"
                    )
                    tg_send(msg)
                    alerted_live.add(fid)
                    record_alert()

            time.sleep(POLL_SECONDS)

        except Exception as e:
            tg_send(f"❌ Erro bot: {e}")
            time.sleep(300)

if __name__ == "__main__":
    main()
