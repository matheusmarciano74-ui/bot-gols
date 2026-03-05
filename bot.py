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
TIMEZONE = "America/Sao_Paulo"

# =========================
# CONFIG (ajuste aqui)
# =========================
POLL_SECONDS = 600  # 10 min (economiza quota)
PRE_MIN = 5
PRE_MAX = 120

# MODO PRÉ-JOGO:
# - False (recomendado no plano FREE): avisa "jogo bom pré" sem olhar odds (economiza MUITO)
# - True  (melhor no plano PAGO): só avisa se achar odd >= ODD_MIN_HT
USE_ODDS = False
ODD_MIN_HT = 1.30

# Limite de alertas pré-jogo por hora
ALERTS_PER_HOUR = 5

# Quantos jogos pré-jogo avaliar por rodada (pra economizar chamadas)
MAX_PREGAME_EVAL = 12

# Ao vivo: quantos jogos checar stats por rodada
MAX_LIVE_EVAL = 3

# Thresholds ao vivo (seu bot original)
LIVE_MINUTE_MIN = 25
LIVE_SHOTS_MIN = 8
LIVE_SOT_MIN = 2
LIVE_CORNERS_MIN = 3

# HT score mínimo pra alertar pré-jogo
PRE_SCORE_MIN = 75
PRE_CANDIDATE_MIN = 70

# =========================
# LIGAS IMPORTANTES (NOMES)
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
# HELPERS
# =========================
def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars missing (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID).")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=20)
    except Exception as e:
        print("Telegram send error:", e)

def api(path: str, params: dict):
    if not API_KEY:
        tg_send("❌ ERRO: API_FOOTBALL_KEY não está definida no Railway.")
        return []

    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=25)
    j = r.json()

    # Loga erro real (muito útil no plano free)
    if j.get("errors"):
        tg_send(f"❌ API ERR {path}: {j['errors']}")
        return []

    return j.get("response", []) or []

def now_local():
    return datetime.now()

def mins_to_kickoff(iso_dt: str) -> int:
    # Ex: "2026-03-05T19:00:00-03:00"
    try:
        ko = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except Exception:
        # fallback
        ko = datetime.strptime(iso_dt[:19], "%Y-%m-%dT%H:%M:%S")

    diff = ko - now_local()
    return int(diff.total_seconds() // 60)

# =========================
# PRE-GAME (FREE SAFE)
# =========================
def get_ns_fixtures_today_and_tomorrow():
    # Plano free: sem "next"
    today = date.today()
    tomorrow = today + timedelta(days=1)

    f1 = api("/fixtures", {"date": today.isoformat(), "timezone": TIMEZONE, "status": "NS"})
    f2 = api("/fixtures", {"date": tomorrow.isoformat(), "timezone": TIMEZONE, "status": "NS"})
    return (f1 or []) + (f2 or [])

def get_last_fixtures(team_id: int, last: int = 10):
    return api("/fixtures", {"team": team_id, "last": last, "timezone": TIMEZONE})

def calc_ht_stats(last_fixtures: list) -> dict:
    total = 0
    ht_with_goal = 0
    ht_goals_sum = 0

    for fx in last_fixtures:
        ht = (fx.get("score", {}) or {}).get("halftime", {}) or {}
        home_ht = ht.get("home") or 0
        away_ht = ht.get("away") or 0
        g = (home_ht or 0) + (away_ht or 0)

        total += 1
        ht_goals_sum += g
        if g >= 1:
            ht_with_goal += 1

    if total == 0:
        return {"rate": 0.0, "avg": 0.0}

    return {
        "rate": (ht_with_goal / total) * 100.0,  # %
        "avg": ht_goals_sum / total              # média
    }

def score_pregame(a: dict, b: dict, mins: int) -> int:
    s = 0

    def pts(t):
        p = 0
        if t["rate"] >= 70: p += 15
        if t["rate"] >= 80: p += 10
        if t["avg"] >= 1.10: p += 5
        return p  # max 30

    # A) tendência HT (0-60)
    s += pts(a)
    s += pts(b)

    # B) encaixe simples (0-25)
    if a["avg"] >= 1.10 and b["avg"] >= 1.10:
        s += 12
    if a["rate"] >= 75 and b["rate"] >= 75:
        s += 13

    # C) timing (0-15)
    if 25 <= mins <= 45:
        s += 15
    elif 15 <= mins <= 24:
        s += 8
    elif 46 <= mins <= 55:
        s += 5

    return min(100, int(round(s)))

def get_odd_over05_ht(fixture_id: int):
    # Só use se USE_ODDS=True (plano pago recomendado)
    items = api("/odds", {"fixture": fixture_id})
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

def build_pregame_list():
    fixtures = get_ns_fixtures_today_and_tomorrow()

    pre = []
    for f in fixtures:
        league = f.get("league", {}).get("name", "")
        if league not in TARGET_LEAGUES:
            continue

        mins = mins_to_kickoff(f["fixture"]["date"])
        if mins < PRE_MIN or mins > PRE_MAX:
            continue

        pre.append((mins, f))

    # primeiro os que começam mais cedo (pra você ter antecedência)
    pre.sort(key=lambda x: x[0])
    return pre

# =========================
# LIVE (seu bot original)
# =========================
def get_live_games():
    today = date.today().isoformat()
    data = api("/fixtures", {"date": today, "timezone": TIMEZONE})

    live = []
    for f in data:
        status = f["fixture"]["status"]["short"]
        if status not in ["1H", "2H", "HT"]:
            continue

        league = f["league"]["name"]
        if league not in TARGET_LEAGUES:
            continue

        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]

        ghome = f["goals"]["home"] or 0
        gaway = f["goals"]["away"] or 0

        minute = f["fixture"]["status"]["elapsed"] or 0

        live.append({
            "id": f["fixture"]["id"],
            "league": league,
            "home": home,
            "away": away,
            "minute": minute,
            "score": f"{ghome}-{gaway}",
            "status": status
        })

    return live

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
# RATE LIMIT (alerts/hour)
# =========================
def cleanup_alert_times(alert_times):
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    return [t for t in alert_times if t >= one_hour_ago]

# =========================
# MAIN
# =========================
def main():
    tg_send("✅ Bot gols iniciado (pré-jogo + ao vivo)")

    alerted_pre = set()   # fixture_id
    alerted_live = set()  # fixture_id

    alert_times = []
    last_heartbeat = 0
    last_scan_log = 0

    # cache por rodada pra não chamar last fixtures repetido
    team_cache = {}  # team_id -> {"rate":.., "avg":..}

    while True:
        try:
            # HEARTBEAT a cada 10 min
            if time.time() - last_heartbeat > 600:
                alert_times = cleanup_alert_times(alert_times)
                tg_send(
                    f"✅ BOT ON | alertas(60m): {len(alert_times)}/{ALERTS_PER_HOUR} | "
                    f"pré-jogo: {'ODDS' if USE_ODDS else 'SEM ODDS'} | ao vivo ativo"
                )
                last_heartbeat = time.time()

            # ==========
            # PRÉ-JOGO
            # ==========
            pre = build_pregame_list()

            # log do scan a cada 20 min (pra não encher)
            if time.time() - last_scan_log > 1200:
                tg_send(f"🔎 SCAN PRÉ | janela (T-{PRE_MAX}→T-{PRE_MIN}): {len(pre)} jogos")
                last_scan_log = time.time()

            scored = []
            team_cache.clear()

            # limita quantos jogos vamos avaliar por rodada (economiza quota)
            for mins, f in pre[:MAX_PREGAME_EVAL]:
                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                home = f["teams"]["home"]
                away = f["teams"]["away"]
                home_id = home["id"]
                away_id = away["id"]

                # (opcional) odds - só se plano pago e você quiser
                odd_ht = None
                if USE_ODDS:
                    odd_ht = get_odd_over05_ht(fid)
                    if odd_ht is None or odd_ht < ODD_MIN_HT:
                        continue

                # stats time A
                if home_id not in team_cache:
                    home_last = get_last_fixtures(home_id, 10)
                    team_cache[home_id] = calc_ht_stats(home_last)

                # stats time B
                if away_id not in team_cache:
                    away_last = get_last_fixtures(away_id, 10)
                    team_cache[away_id] = calc_ht_stats(away_last)

                hs = team_cache[home_id]
                as_ = team_cache[away_id]

                sc = score_pregame(hs, as_, mins)

                # candidato
                if sc >= PRE_CANDIDATE_MIN:
                    scored.append((sc, mins, odd_ht, f, hs, as_))

            # melhores primeiro
            scored.sort(key=lambda x: (-x[0], x[1]))

            # manda até 5 alertas/h
            for sc, mins, odd_ht, f, hs, as_ in scored:
                if sc < PRE_SCORE_MIN:
                    continue

                alert_times = cleanup_alert_times(alert_times)
                if len(alert_times) >= ALERTS_PER_HOUR:
                    break

                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                league = f["league"]["name"]
                home_name = f["teams"]["home"]["name"]
                away_name = f["teams"]["away"]["name"]

                msg = (
                    "🚨 ALERTA PRÉ (0.5 HT)\n"
                    f"🏆 {league}\n"
                    f"{home_name} x {away_name}\n"
                    f"⏳ Faltam {mins} min\n"
                    f"📈 Score: {sc}/100\n"
                    f"📌 HT%: {hs['rate']:.0f}% vs {as_['rate']:.0f}% | HT avg: {hs['avg']:.2f} vs {as_['avg']:.2f}\n"
                )
                if USE_ODDS:
                    msg += f"🎲 Odd O0.5 HT: {odd_ht:.2f} (mín {ODD_MIN_HT})\n"

                msg += "✅ Ação: procurar Over 0.5 HT antes do jogo começar"
                tg_send(msg)

                alerted_pre.add(fid)
                alert_times.append(datetime.now())

            # ==========
            # AO VIVO
            # ==========
            games = get_live_games()

            candidates = []
            for g in games:
                if g["score"] != "0-0":
                    continue
                if g["minute"] < LIVE_MINUTE_MIN:
                    continue
                candidates.append(g)

            # checa só alguns por rodada (economiza quota)
            for g in candidates[:MAX_LIVE_EVAL]:
                fid = g["id"]
                if fid in alerted_live:
                    continue

                shots, sot, corners = get_stats(fid)

                if shots >= LIVE_SHOTS_MIN and sot >= LIVE_SOT_MIN and corners >= LIVE_CORNERS_MIN:
                    msg = (
                        "⚽ POSSÍVEL GOL (AO VIVO)\n\n"
                        f"🏆 {g['league']}\n"
                        f"{g['home']} x {g['away']}\n\n"
                        f"⏱ {g['minute']}' | 0-0\n\n"
                        f"📊 Chutes {shots}\n"
                        f"🎯 No alvo {sot}\n"
                        f"🚩 Escanteios {corners}\n\n"
                        "➡ Sinal de jogo quente (bom pra sua entrada 0.5 no intervalo/2ºT)"
                    )
                    tg_send(msg)
                    alerted_live.add(fid)

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print("ERR:", e)
            tg_send(f"❌ Erro bot: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
