import os
import time
import requests
from datetime import datetime, date, timedelta

API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"

# Agora vamos rodar mais frequente pra avisar antes do jogo
POLL_SECONDS = 120  # 2 min

# PRÉ-JOGO: janela pra alertar
PRE_MIN = 5
PRE_MAX = 120

ODD_MIN_HT = 1.30
ALERTS_PER_HOUR = 5

# LIGAS IMPORTANTES
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

def tg_send(msg):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars missing")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TG_CHAT, "text": msg})
    except Exception as e:
        print("Telegram error:", e)

def api(path, params):
    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=25)
    j = r.json()
    return j.get("response", [])

def now_brt():
    # API-Sports consegue devolver datas já no timezone pedido.
    # Aqui só usamos "agora" do servidor mesmo; serve.
    return datetime.now()

def mins_to_kickoff(iso_dt):
    # iso_dt vem tipo "2026-03-05T21:00:00-03:00"
    try:
        ko = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except Exception:
        # fallback simples
        ko = datetime.strptime(iso_dt[:19], "%Y-%m-%dT%H:%M:%S")
    diff = ko - now_brt()
    return int(diff.total_seconds() // 60)

# ----------------------------
# PRÉ-JOGO
# ----------------------------

def get_today_fixtures():
    today = date.today().isoformat()
    return api("/fixtures", {"date": today, "timezone": "America/Sao_Paulo"})

def get_last_fixtures(team_id, last=10):
    return api("/fixtures", {"team": team_id, "last": last, "timezone": "America/Sao_Paulo"})

def calc_ht_stats(last_fixtures):
    total = 0
    ht_with_goal = 0
    ht_goals_sum = 0

    for fx in last_fixtures:
        ht = fx.get("score", {}).get("halftime", {}) or {}
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
        "rate": (ht_with_goal / total) * 100.0,   # %
        "avg": ht_goals_sum / total               # média
    }

def score_pregame(a, b, mins):
    # a,b = {"rate":.., "avg":..}
    s = 0

    # A) Tendência HT (0-60)
    def pts(t):
        p = 0
        if t["rate"] >= 70: p += 15
        if t["rate"] >= 80: p += 10
        if t["avg"] >= 1.10: p += 5
        return p  # max 30

    s += pts(a)
    s += pts(b)

    # B) “Encaixe” (0-25) simples
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

def get_odd_over05_ht(fixture_id):
    # Parser “tolerante”: tenta achar qualquer mercado do 1º tempo com "Over 0.5"
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
                            odd = float(v.get("odd"))
                            return odd
                        except:
                            pass
    return None

def get_pregame_candidates():
    fixtures = get_today_fixtures()
    out = []

    for f in fixtures:
        status = f["fixture"]["status"]["short"]  # NS, 1H, etc.
        if status != "NS":
            continue

        league = f["league"]["name"]
        if league not in TARGET_LEAGUES:
            continue

        mins = mins_to_kickoff(f["fixture"]["date"])
        if mins < PRE_MIN or mins > PRE_MAX:
            continue

        out.append(f)

    return out

# ----------------------------
# AO VIVO (SEU BOT ORIGINAL)
# ----------------------------

def get_live_games():
    today = date.today().isoformat()
    data = api("/fixtures", {"date": today, "timezone": "America/Sao_Paulo"})

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

def get_stats(fid):
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

# ----------------------------
# LIMITES / LOGS
# ----------------------------

def cleanup_alert_times(alert_times):
    now = datetime.now()
    one_hour_ago = now - timedelta(hours=1)
    return [t for t in alert_times if t >= one_hour_ago]

def main():
    tg_send("✅ Bot gols iniciado (pré-jogo + ao vivo)")

    alerted_live = set()
    alerted_pre = set()

    alert_times = []  # timestamps dos alertas (rolling 60min)
    last_heartbeat = 0

    while True:
        try:
            # HEARTBEAT a cada 5 min
            if time.time() - last_heartbeat > 300:
                alert_times = cleanup_alert_times(alert_times)
                tg_send(f"✅ BOT ON | alertas(60m): {len(alert_times)}/{ALERTS_PER_HOUR} | varrendo pré-jogo + ao vivo")
                last_heartbeat = time.time()

            # -----------------
            # PRÉ-JOGO SCAN
            # -----------------
            pre = get_pregame_candidates()
            tg_send(f"🔎 SCAN PRÉ | jogos na janela (T-{PRE_MAX}→T-{PRE_MIN}): {len(pre)}")

            scored = []
            for f in pre:
                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                home = f["teams"]["home"]
                away = f["teams"]["away"]
                home_id = home["id"]
                away_id = away["id"]

                mins = mins_to_kickoff(f["fixture"]["date"])

                odd_ht = get_odd_over05_ht(fid)
                if odd_ht is None or odd_ht < ODD_MIN_HT:
                    continue

                # últimos 10 jogos de cada time
                home_last = get_last_fixtures(home_id, 10)
                away_last = get_last_fixtures(away_id, 10)
                hs = calc_ht_stats(home_last)
                as_ = calc_ht_stats(away_last)

                sc = score_pregame(hs, as_, mins)

                # só entra forte
                if sc >= 75:
                    scored.append((sc, mins, odd_ht, f, hs, as_))

            # ordena por score e limita por hora (5)
            scored.sort(key=lambda x: (-x[0], -x[2]))

            for sc, mins, odd_ht, f, hs, as_ in scored:
                # limite 5 por hora
                alert_times = cleanup_alert_times(alert_times)
                if len(alert_times) >= ALERTS_PER_HOUR:
                    break

                fid = f["fixture"]["id"]
                if fid in alerted_pre:
                    continue

                league = f["league"]["name"]
                home = f["teams"]["home"]["name"]
                away = f["teams"]["away"]["name"]
                ko = f["fixture"]["date"]

                msg = (
                    "🚨 ALERTA PRÉ (0.5 HT)\n"
                    f"🏆 {league}\n"
                    f"{home} x {away}\n"
                    f"⏳ Faltam {mins} min\n"
                    f"🎲 Odd O0.5 HT: {odd_ht:.2f} (mín {ODD_MIN_HT})\n"
                    f"📈 Score: {sc}/100\n"
                    f"📌 HT%: {hs['rate']:.0f}% vs {as_['rate']:.0f}% | HT avg: {hs['avg']:.2f} vs {as_['avg']:.2f}\n"
                    "✅ Ação: entrar no Over 0.5 HT antes do jogo começar"
                )
                tg_send(msg)

                alerted_pre.add(fid)
                alert_times.append(datetime.now())

            # -----------------
            # AO VIVO (seu fluxo original)
            # -----------------
            games = get_live_games()
            candidates = []
            for g in games:
                if g["score"] != "0-0":
                    continue
                if g["minute"] < 25:
                    continue
                candidates.append(g)

            print("LIVE:", len(games), "cand:", len(candidates))

            for g in candidates[:3]:
                fid = g["id"]
                if fid in alerted_live:
                    continue

                shots, sot, corners = get_stats(fid)

                if shots >= 8 and sot >= 2 and corners >= 3:
                    msg = (
                        "⚽ POSSÍVEL GOL (AO VIVO)\n\n"
                        f"🏆 {g['league']}\n"
                        f"{g['home']} x {g['away']}\n\n"
                        f"⏱ {g['minute']}' | 0-0\n\n"
                        f"📊 Chutes {shots}\n"
                        f"🎯 No alvo {sot}\n"
                        f"🚩 Escanteios {corners}\n\n"
                        "➡ (se sua estratégia for 0.5 FT no intervalo/2ºT, aqui é o sinal de jogo quente)"
                    )
                    tg_send(msg)
                    alerted_live.add(fid)

            time.sleep(POLL_SECONDS)

        except Exception as e:
            print(e)
            tg_send(f"❌ Erro bot: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
