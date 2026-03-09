import os
import time
import requests
from datetime import datetime, timedelta, timezone

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
# LIGAS BOAS (nome contém)
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
target_profit = None
loss_acc = 0.0
attempt = 0
pending_game = None  # guarda jogo escolhido para apostar

# =========================
# TELEGRAM
# =========================
def tg_send(msg):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars missing")
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=15)

def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=15)
    return r.json()

# =========================
# HELPERS
# =========================
def league_ok(name):
    if not name: return False
    n = name.lower()
    return any(g.lower() in n for g in GOOD_LEAGUES)

def api(path, params):
    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API+path, headers=headers, params=params, timeout=25)
    r.raise_for_status()
    return r.json().get("response", [])

def odds_get(path, params):
    params = dict(params)
    params["apiKey"] = ODDS_API_KEY
    r = requests.get(ODDS+path, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def try_float(v):
    try: return float(v)
    except: return None

def extract_over05_ft(odds_json):
    bms = odds_json.get("bookmakers", {})
    for _, markets in bms.items():
        for m in markets or []:
            name = str(m.get("name","")).lower()
            if "half" in name:  # ignora HT
                continue
            for item in m.get("odds",[]) or []:
                txt = " ".join([str(k)+" "+str(v) for k,v in item.items()]).lower()
                if "over" in txt and "0.5" in txt:
                    for k in ["odd","price","value","over","Over"]:
                        if k in item:
                            v = try_float(item[k])
                            if v: return v
    return None

def extract_bet365_link(odds_json):
    bms = odds_json.get("bookmakers", {})
    for _, markets in bms.items():
        if isinstance(markets, dict):
            for k in ["url","link","href"]:
                if markets.get(k): return markets.get(k)
        if isinstance(markets, list):
            for m in markets:
                if not isinstance(m, dict): continue
                for k in ["url","link","href"]:
                    if m.get(k): return m.get(k)
    return None

# =========================
# POSIÇÃO NA TABELA
# =========================
def get_positions(league_id, season, home, away):
    table = api("/standings", {"league":league_id, "season":season})
    pos_h = pos_a = None
    if table and table[0].get("league",{}).get("standings"):
        for group in table[0]["league"]["standings"]:
            for row in group:
                team = row["team"]["name"]
                if team == home: pos_h = row["rank"]
                if team == away: pos_a = row["rank"]
    return pos_h, pos_a

# =========================
# BUSCA JOGOS PRÉ-JOGO
# =========================
def find_games():
    fixtures = api("/fixtures", {"next":50})
    out = []
    now = datetime.now(timezone.utc)
    for f in fixtures:
        league = f["league"]["name"]
        if not league_ok(league): continue

        dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z","+00:00"))
        mins = int((dt-now).total_seconds()/60)

        if 0 < mins <= ALERT_BEFORE_MIN:
            out.append({
                "fixture_id": f["fixture"]["id"],
                "league": league,
                "league_id": f["league"]["id"],
                "season": f["league"]["season"],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "date": dt
            })
    return out

# =========================
# RESULTADO DO JOGO
# =========================
def check_result(fid):
    data = api("/fixtures", {"id": fid})
    if not data: return None
    f = data[0]
    status = f["fixture"]["status"]["short"]
    if status != "FT":
        return None
    g1 = f["goals"]["home"] or 0
    g2 = f["goals"]["away"] or 0
    return g1+g2

# =========================
# CÁLCULO DA PRÓXIMA APOSTA
# =========================
def next_stake(odd):
    global loss_acc, target_profit
    recover = loss_acc * RECOVER_PCT
    total = recover + target_profit
    return round(total/(odd-1),2)

# =========================
# LOOP PRINCIPAL
# =========================
def main():
    global cycle_active, base_stake, target_profit, loss_acc, attempt, pending_game

    tg_send("🤖 BOT V6 iniciado")

    offset = None

    while True:

        # =====================
        # ler respostas Telegram
        # =====================
        updates = tg_get_updates(offset)
        if updates.get("result"):
            for u in updates["result"]:
                offset = u["update_id"] + 1
                msg = u.get("message",{})
                txt = msg.get("text","").strip()

                if pending_game and txt.replace(".","",1).isdigit():
                    stake = float(txt)
                    base_stake = stake
                    target_profit = stake*(pending_game["odd"]-1)
                    cycle_active = True
                    loss_acc = 0
                    attempt = 1

                    tg_send(
                        f"✅ Aposta registrada\n"
                        f"{pending_game['home']} x {pending_game['away']}\n"
                        f"Aposta: {stake}\n"
                        f"Lucro alvo: {round(target_profit,2)}"
                    )

        # =====================
        # procurar jogos
        # =====================
        if not pending_game:
            games = find_games()
            for g in games:
                try:
                    odds = odds_get("/odds", {"eventId": g["fixture_id"], "bookmakers": BOOK})
                except:
                    continue

                odd = extract_over05_ft(odds)
                if not odd or odd < ODD_MIN:
                    continue

                link = extract_bet365_link(odds)
                pos_h,pos_a = get_positions(g["league_id"], g["season"], g["home"], g["away"])

                pending_game = g
                pending_game["odd"] = odd

                msg = (
                    f"🚨 JOGO ENCONTRADO\n"
                    f"{g['league']}\n"
                    f"{g['home']} ({pos_h}) x {g['away']} ({pos_a})\n"
                    f"Odd O0.5: {odd}\n"
                )

                if link:
                    msg += f"🔗 Bet365: {link}\n"
                else:
                    msg += f"🔎 Buscar Bet365: {g['home']} x {g['away']}\n"

                msg += "\nDigite o valor da aposta."

                tg_send(msg)
                break

        # =====================
        # verificar resultado
        # =====================
        if pending_game and cycle_active:
            res = check_result(pending_game["fixture_id"])
            if res is not None:
                if res > 0:
                    tg_send("✅ GREEN — ciclo reiniciado")
                    cycle_active = False
                    pending_game = None
                    loss_acc = 0
                    attempt = 0
                else:
                    loss_acc += base_stake if attempt==1 else last_stake
                    tg_send(f"❌ RED\nPerda acumulada: {round(loss_acc,2)}")
                    attempt += 1
                    pending_game = None

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
