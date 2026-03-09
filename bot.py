import os
import time
import requests
from datetime import datetime, timezone

# =========================
# ENV
# =========================
API_KEY = os.getenv("API_FOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

MAX_STAKE = float(os.getenv("MAX_STAKE", "200"))
RECOVER_PCT = float(os.getenv("RECOVER_PCT", "0.70"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
ALERT_BEFORE_MIN = int(os.getenv("ALERT_BEFORE_MIN", "15"))
ODD_MIN = float(os.getenv("ODD_MIN", "1.30"))

API = "https://v3.football.api-sports.io"
ODDS = "https://api.odds-api.io/v3"
BOOK = "Bet365"

# =========================
# LIGAS BOAS
# =========================
GOOD_LEAGUES = [
    "Premier League","Championship","FA Cup","EFL Cup",
    "Bundesliga","DFB Pokal",
    "Serie A","Coppa Italia",
    "La Liga",
    "Ligue 1","Coupe de France",
    "Primeira Liga",
    "Eredivisie",
    "Belgian Pro League",
    "Austrian Bundesliga",
    "Scottish Premiership",
    "Süper Lig",
    "Major League Soccer",
    "Campeonato Brasileiro Série A","Brasileirao","Copa do Brasil",
    "Liga Profesional","Copa Argentina",
    "UEFA Champions League","UEFA Europa League","UEFA Europa Conference League",
    "CONMEBOL Libertadores","CONMEBOL Sudamericana"
]

# =========================
# ESTADO DO CICLO
# =========================
cycle_active = False
base_stake = None
current_stake = None
target_profit = None
loss_acc = 0.0
attempt = 0
pending_game = None

# =========================
# TELEGRAM
# =========================
def tg_send(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT, "text": msg})

def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset
    r = requests.get(url, params=params)
    return r.json()

# =========================
# HELPERS
# =========================
def league_ok(name):
    if not name:
        return False
    n = name.lower()
    return any(g.lower() in n for g in GOOD_LEAGUES)

def api(path, params):
    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params)
    return r.json().get("response", [])

def odds_get(path, params):
    params = dict(params)
    params["apiKey"] = ODDS_API_KEY
    r = requests.get(ODDS + path, params=params)
    return r.json()

def try_float(v):
    try:
        return float(v)
    except:
        return None

def extract_over05_ft(odds_json):
    bms = odds_json.get("bookmakers", {})
    for _, markets in bms.items():
        for m in markets or []:
            name = str(m.get("name","")).lower()
            if "half" in name:
                continue
            for item in m.get("odds",[]) or []:
                txt = " ".join([str(k)+" "+str(v) for k,v in item.items()]).lower()
                if "over" in txt and "0.5" in txt:
                    for k in ["odd","price","value","over","Over"]:
                        if k in item:
                            v = try_float(item[k])
                            if v:
                                return v
    return None

def extract_bet365_link(odds_json):
    bms = odds_json.get("bookmakers", {})
    for _, markets in bms.items():
        if isinstance(markets, dict):
            if markets.get("url"):
                return markets["url"]
        if isinstance(markets, list):
            for m in markets:
                if isinstance(m, dict) and m.get("url"):
                    return m["url"]
    return None

# =========================
# POSIÇÃO NA TABELA
# =========================
def get_positions(league_id, season, home, away):
    table = api("/standings", {"league":league_id, "season":season})
    pos_h = pos_a = None

    if table:
        standings = table[0]["league"]["standings"]

        for group in standings:
            for row in group:
                team = row["team"]["name"]

                if team == home:
                    pos_h = row["rank"]

                if team == away:
                    pos_a = row["rank"]

    return pos_h, pos_a

# =========================
# BUSCA JOGOS
# =========================
def find_games():

    fixtures = api("/fixtures", {"next":50})

    now = datetime.now(timezone.utc)

    for f in fixtures:

        league = f["league"]["name"]

        if not league_ok(league):
            continue

        dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))

        mins = int((dt-now).total_seconds()/60)

        if 0 < mins <= ALERT_BEFORE_MIN:

            return {
                "fixture_id": f["fixture"]["id"],
                "league": league,
                "league_id": f["league"]["id"],
                "season": f["league"]["season"],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "date": dt
            }

    return None

# =========================
# RESULTADO
# =========================
def check_result(fid):

    data = api("/fixtures", {"id": fid})

    if not data:
        return None

    f = data[0]

    status = f["fixture"]["status"]["short"]

    if status != "FT":
        return None

    g1 = f["goals"]["home"] or 0
    g2 = f["goals"]["away"] or 0

    return g1 + g2

# =========================
# CÁLCULO APOSTA
# =========================
def next_stake(odd):

    recover = loss_acc * RECOVER_PCT

    total = recover + target_profit

    return round(total / (odd-1), 2)

# =========================
# LOOP
# =========================
def main():

    global cycle_active
    global base_stake
    global current_stake
    global target_profit
    global loss_acc
    global attempt
    global pending_game

    tg_send("🤖 BOT iniciado")

    offset = None

    while True:

        # =====================
        # TELEGRAM INPUT
        # =====================
        updates = tg_get_updates(offset)

        if updates.get("result"):

            for u in updates["result"]:

                offset = u["update_id"] + 1

                msg = u.get("message",{})

                txt = msg.get("text","").strip()

                if pending_game and txt.replace(".","",1).isdigit():

                    stake = float(txt)

                    if not cycle_active:

                        base_stake = stake
                        target_profit = stake * (pending_game["odd"] - 1)

                        loss_acc = 0
                        attempt = 1
                        cycle_active = True

                    current_stake = stake

                    tg_send(
                        f"✅ Aposta registrada\n"
                        f"{pending_game['home']} x {pending_game['away']}\n"
                        f"Aposta: {stake}\n"
                        f"Lucro alvo: {round(target_profit,2)}"
                    )

        # =====================
        # PROCURAR JOGO
        # =====================
        if not pending_game:

            g = find_games()

            if g:

                odds = odds_get("/odds", {"eventId": g["fixture_id"], "bookmakers": BOOK})

                odd = extract_over05_ft(odds)

                if odd and odd >= ODD_MIN:

                    link = extract_bet365_link(odds)

                    pos_h,pos_a = get_positions(
                        g["league_id"],
                        g["season"],
                        g["home"],
                        g["away"]
                    )

                    pending_game = g
                    pending_game["odd"] = odd

                    msg = (
                        f"🚨 JOGO ENCONTRADO\n"
                        f"{g['league']}\n"
                        f"{g['home']} ({pos_h}) x {g['away']} ({pos_a})\n"
                        f"Odd O0.5: {odd}\n"
                    )

                    if cycle_active:

                        suggested = next_stake(odd)

                        msg += (
                            f"\n📊 Ciclo em andamento\n"
                            f"Perda acumulada: {round(loss_acc,2)}\n"
                            f"Sugestão aposta: {suggested}\n"
                        )

                    if link:
                        msg += f"\n🔗 Bet365: {link}"

                    msg += "\n\nDigite o valor da aposta"

                    tg_send(msg)

        # =====================
        # RESULTADO
        # =====================
        if pending_game and cycle_active:

            res = check_result(pending_game["fixture_id"])

            if res is not None:

                if res > 0:

                    profit = current_stake * (pending_game["odd"] - 1)

                    tg_send(
                        f"✅ GREEN\n"
                        f"Lucro: {round(profit,2)}\n"
                        f"Ciclo reiniciado"
                    )

                    cycle_active = False
                    loss_acc = 0
                    attempt = 0
                    pending_game = None

                else:

                    loss_acc += current_stake
                    attempt += 1

                    tg_send(
                        f"❌ RED\n"
                        f"Perda acumulada: {round(loss_acc,2)}\n"
                        f"Tentativa: {attempt}"
                    )

                    pending_game = None

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
