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
# ECONOMIA (100 req/dia)
# =========================
POLL_SECONDS = 900  # 15 min (economiza MUITO)

DAILY_LIMIT = 95          # trava antes de 100
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
# CONFIG PRÉ-JOGO
# =========================
PRE_MIN = 5
PRE_MAX = 120
ODD_MIN_HT = 1.30

# Quantos jogos pré-jogo vamos tentar pegar odd por rodada (economia)
MAX_ODDS_CHECK_PER_LOOP = 2

# =========================
# CONFIG AO VIVO
# =========================
LIVE_MINUTE_MIN = 25
MAX_LIVE_STATS_PER_LOOP = 1  # economia

LIVE_SHOTS_MIN = 8
LIVE_SOT_MIN = 2
LIVE_CORNERS_MIN = 3

# =========================
# ALERT LIMIT
# =========================
ALERTS_PER_HOUR = 5

# =========================
# LIGAS IMPORTANTES
# =========================
TARGET_LEAGUES = [
    # INGLATERRA
    "Premier League",
    "Championship",
    "FA Cup",
    "League Cup",

    # FRANÇA
    "Ligue 1",
    "Ligue 2",
    "Coupe de France",

    # ALEMANHA
    "Bundesliga",
    "DFB Pokal",

    # ITÁLIA
    "Serie A",
    "Coppa Italia",

    # BRASIL
    "Serie A (Brazil)",
    "Serie B (Brazil)",
    "Copa do Brasil",

    # ARGENTINA
    "Liga Profesional Argentina",
    "Copa de la Liga Profesional",

    # EUROPA
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",

    # CONMEBOL
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana"
]

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
def api(path: str, params: dict):
    # trava de quota local (pra não passar de 100/dia)
    if not can_call_api():
        return []

    if not API_KEY:
        tg_send("❌ ERRO: API_FOOTBALL_KEY não existe no Railway.")
        return []

    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=20)
    inc_req()

    j = r.json()
    if j.get("errors"):
        tg_send(f"❌ API ERR {path}: {j['errors']}")
        return []

    return j.get("response", []) or []

def mins_to_kickoff(iso_dt: str) -> int:
    try:
        ko = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except Exception:
        ko = datetime.strptime(iso_dt[:19], "%Y-%m-%dT%H:%M:%S")
    diff = ko - datetime.now()
    return int(diff.total_seconds() // 60)

# =========================
# FIXTURES (1 ou 2 calls por rodada)
# =========================
def get_fixtures_today():
    today = date.today().isoformat()
    # SEM status aqui: uma chamada só já pega NS + live (economia)
    return api("/fixtures", {"date": today, "timezone": TZ})

def get_fixtures_tomorrow():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    return api("/fixtures", {"date": tomorrow, "timezone": TZ})

# =========================
# ODDS (chama pouco e só 1x por jogo)
# =========================
def get_odd_over05_ht(fixture_id: int):
    items = api("/odds", {"fixture": fixture_id})
    if not items:
        return None

    # parser tolerante
    for it in items:
        for book in it.get("bookmakers", []) or []:
            for bet in book.get("bets", []) or []:
                bet_name = (bet.get("name") or "").lower()
                is_ht = ("1st" in bet_name) or ("first" in bet_name) or ("half" in bet_name)
                if not is_ht:
                    continue
                for v in bet.get("values", []) or []:
                    val = (v.get("value") or "").lower()
                    if ("over" in val) and ("0.5" in val):
                        try:
                            return float(v.get("odd"))
                        except:
                            pass
    return None

# =========================
# AO VIVO STATS
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
# LIMIT ALERTA POR HORA
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
    tg_send("✅ Bot gols iniciado (modo ECONÔMICO 100 req/dia)")

    alerted_pre = set()       # jogos já alertados no pré
    checked_odds = set()      # jogos que já consultou odds (pra não gastar 2x)
    alerted_live = set()      # jogos já alertados ao vivo

    last_heartbeat = 0
    last_scan_log = 0

    while True:
        try:
            reset_counter_if_new_day()

            # Heartbeat a cada 30 min (pra não spammar)
            if time.time() - last_heartbeat > 1800:
                tg_send(f"✅ BOT ON | req hoje: {req_count}/{DAILY_LIMIT} | alertas(60m): {len(alert_times)}/{ALERTS_PER_HOUR}")
                last_heartbeat = time.time()

            # Se estourou nossa trava local, para de chamar API e só avisa
            if not can_call_api():
                tg_send(f"⛔ LIMITE LOCAL atingido | req hoje: {req_count}/{DAILY_LIMIT} | aguardando virar o dia")
                time.sleep(1800)
                continue

            fixtures = get_fixtures_today()

            # Se não vier nada e já for tarde, tenta amanhã (1 call extra)
            if (not fixtures) and datetime.now().hour >= 18:
                fixtures = get_fixtures_tomorrow()

            # Log scan a cada 45 min
            if time.time() - last_scan_log > 2700:
                tg_send(f"🔎 SCAN | fixtures recebidos: {len(fixtures)} | req hoje: {req_count}/{DAILY_LIMIT}")
                last_scan_log = time.time()

            # -----------------------
            # PRÉ-JOGO: pegar jogos NS na janela
            # -----------------------
            pre_list = []
            for f in fixtures:
                status = f["fixture"]["status"]["short"]  # NS, 1H, 2H...
                if status != "NS":
                    continue

                league = f["league"]["name"]
                if league not in TARGET_LEAGUES:
                    continue

                mins = mins_to_kickoff(f["fixture"]["date"])
                if mins < PRE_MIN or mins > PRE_MAX:
                    continue

                pre_list.append((mins, f))

            # ordena por quem começa primeiro (antecedência)
            pre_list.sort(key=lambda x: x[0])

            # Checar odds só em poucos jogos por rodada (economia)
            odds_checked_now = 0
            for mins, f in pre_list:
                if odds_checked_now >= MAX_ODDS_CHECK_PER_LOOP:
                    break

                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                # odds só 1x por jogo
                if fid in checked_odds:
                    continue

                # se não dá pra alertar por hora, nem gasta odds
                if not can_alert_now():
                    break

                odd = get_odd_over05_ht(fid)
                checked_odds.add(fid)
                odds_checked_now += 1

                # se API/Plano não retornar odds, só pula
                if odd is None:
                    continue

                if odd >= ODD_MIN_HT:
                    league = f["league"]["name"]
                    home = f["teams"]["home"]["name"]
                    away = f["teams"]["away"]["name"]

                    tg_send(
                        "🚨 ALERTA PRÉ (0.5 HT)\n"
                        f"🏆 {league}\n"
                        f"{home} x {away}\n"
                        f"⏳ Faltam {mins} min\n"
                        f"🎲 Odd O0.5 HT: {odd:.2f} (mín {ODD_MIN_HT})\n"
                        "✅ Ação: entrar no Over 0.5 HT antes do jogo começar"
                    )
                    alerted_pre.add(fid)
                    record_alert()

            # -----------------------
            # AO VIVO: encontrar 0-0 depois dos 25'
            # -----------------------
            live_candidates = []
            for f in fixtures:
                status = f["fixture"]["status"]["short"]
                if status not in ["1H", "2H", "HT"]:
                    continue

                league = f["league"]["name"]
                if league not in TARGET_LEAGUES:
                    continue

                ghome = f["goals"]["home"] or 0
                gaway = f["goals"]["away"] or 0
                minute = f["fixture"]["status"]["elapsed"] or 0

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

            # checar stats em no máximo 1 jogo por rodada (economia)
            checked_live = 0
            for g in live_candidates:
                if checked_live >= MAX_LIVE_STATS_PER_LOOP:
                    break

                fid = g["id"]
                if fid in alerted_live:
                    continue

                shots, sot, corners = get_stats(fid)
                checked_live += 1

                if shots >= LIVE_SHOTS_MIN and sot >= LIVE_SOT_MIN and corners >= LIVE_CORNERS_MIN:
                    tg_send(
                        "⚽ POSSÍVEL GOL (AO VIVO)\n\n"
                        f"🏆 {g['league']}\n"
                        f"{g['home']} x {g['away']}\n"
                        f"⏱ {g['minute']}' | 0-0\n\n"
                        f"📊 Chutes {shots}\n"
                        f"🎯 No alvo {sot}\n"
                        f"🚩 Escanteios {corners}\n\n"
                        "➡ Sinal de jogo quente (bom pra sua entrada 0.5 no intervalo/2ºT)"
                    )
                    alerted_live.add(fid)

            time.sleep(POLL_SECONDS)

        except Exception as e:
            tg_send(f"❌ Erro bot: {e}")
            time.sleep(300)

if __name__ == "__main__":
    main()
