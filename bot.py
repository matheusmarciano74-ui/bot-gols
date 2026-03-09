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
TG_CHAT = str(os.getenv("TELEGRAM_CHAT_ID"))

MAX_STAKE = float(os.getenv("MAX_STAKE", "200"))
RECOVER_PCT = float(os.getenv("RECOVER_PCT", "0.70"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
ALERT_BEFORE_MIN = int(os.getenv("ALERT_BEFORE_MIN", "15"))
ODD_MIN = float(os.getenv("ODD_MIN", "1.30"))

API = "https://v3.football.api-sports.io"
ODDS = "https://api.odds-api.io/v3"
BOOK = "Bet365"

# =========================
# COMPETIÇÕES PERMITIDAS
# país + nome da liga
# =========================
ALLOWED_COMPETITIONS = [
    ("England", "Premier League"),
    ("England", "Championship"),
    ("England", "FA Cup"),
    ("England", "EFL Cup"),
    ("England", "League Cup"),

    ("Germany", "Bundesliga"),
    ("Germany", "DFB Pokal"),

    ("Italy", "Serie A"),
    ("Italy", "Coppa Italia"),

    ("Spain", "La Liga"),

    ("France", "Ligue 1"),
    ("France", "Coupe de France"),

    ("Portugal", "Primeira Liga"),
    ("Netherlands", "Eredivisie"),
    ("Belgium", "Belgian Pro League"),
    ("Austria", "Bundesliga"),
    ("Scotland", "Premiership"),
    ("Turkey", "Süper Lig"),

    ("USA", "Major League Soccer"),
    ("Brazil", "Serie A"),
    ("Brazil", "Brasileirao"),
    ("Brazil", "Copa do Brasil"),
    ("Argentina", "Liga Profesional"),
    ("Argentina", "Copa Argentina"),

    ("World", "UEFA Champions League"),
    ("World", "UEFA Europa League"),
    ("World", "UEFA Europa Conference League"),
    ("World", "CONMEBOL Libertadores"),
    ("World", "CONMEBOL Sudamericana"),
]

# =========================
# ESTADO
# =========================
cycle_active = False
waiting_bet = False
pending_game = None

base_stake = 0.0
current_stake = 0.0
loss_acc = 0.0
target_profit = 0.0
attempt = 0

last_games = []
last_error = None

# =========================
# TELEGRAM
# =========================
def tg_send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(
        url,
        data={"chat_id": TG_CHAT, "text": msg},
        timeout=20,
    )


def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"timeout": 5}

    if offset is not None:
        params["offset"] = offset

    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

# =========================
# HELPERS
# =========================
def try_float(v):
    try:
        return float(v)
    except Exception:
        return None


def norm_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
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


def norm_team_name(name: str) -> str:
    return norm_text(name)


def format_search_text(home: str, away: str) -> str:
    return f"{home} x {away}"


def country_league_ok(country: str, league: str) -> bool:
    c = (country or "").strip().lower()
    l = (league or "").strip().lower()

    for allowed_country, allowed_league in ALLOWED_COMPETITIONS:
        if allowed_country.lower() == c and allowed_league.lower() in l:
            return True

    return False


def pregame_competition_ok(ev: dict) -> bool:
    """
    Odds API pode não trazer país/nome exatamente como a API-Football.
    Então tentamos casar por name + slug.
    """
    league = ev.get("league") or {}
    name = str(league.get("name", "")).lower()
    slug = str(league.get("slug", "")).lower()

    text = f"{name} | {slug}"

    allowed = [
        "england premier league",
        "england championship",
        "england fa cup",
        "england efl cup",
        "england league cup",
        "germany bundesliga",
        "germany dfb pokal",
        "italy serie a",
        "italy coppa italia",
        "spain la liga",
        "france ligue 1",
        "france coupe de france",
        "portugal primeira liga",
        "netherlands eredivisie",
        "belgium belgian pro league",
        "austria bundesliga",
        "scotland premiership",
        "turkey super lig",
        "usa major league soccer",
        "brazil serie a",
        "brazil brasileirao",
        "brazil copa do brasil",
        "argentina liga profesional",
        "argentina copa argentina",
        "uefa champions league",
        "uefa europa league",
        "uefa europa conference league",
        "conmebol libertadores",
        "conmebol sudamericana",
    ]

    for item in allowed:
        if item in text:
            return True

    return False

# =========================
# API FOOTBALL
# =========================
def api(path: str, params: dict):
    headers = {"x-apisports-key": API_KEY}
    r = requests.get(API + path, headers=headers, params=params, timeout=25)
    r.raise_for_status()
    j = r.json()

    if j.get("errors"):
        raise RuntimeError(str(j["errors"]))

    return j.get("response", [])

# =========================
# ODDS API
# =========================
def odds_get(path: str, params: dict):
    params = dict(params)
    params["apiKey"] = ODDS_API_KEY
    r = requests.get(ODDS + path, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

# =========================
# ODDS PARSER
# =========================
def extract_match_over05_ft(odds_json: dict):
    """
    Retorna:
      (odd, market_name, line)
    Só aceita:
      - mercado da partida inteira
      - over 0.5
      - não HT
      - não team total
    """
    bookmakers = odds_json.get("bookmakers", {})
    if not bookmakers:
        return None, None, None

    valid_market_keywords = [
        "total goals",
        "goals over/under",
        "over/under",
        "match goals",
        "totals",
    ]

    invalid_market_keywords = [
        "1st half",
        "first half",
        "2nd half",
        "second half",
        "team total",
        "home total",
        "away total",
        "player",
    ]

    for _, markets in bookmakers.items():
        for market in markets or []:
            market_name = str(market.get("name", "")).lower()

            # rejeita mercados claramente errados
            if any(bad in market_name for bad in invalid_market_keywords):
                continue

            # exige algo parecido com mercado de gols da partida
            if not any(ok in market_name for ok in valid_market_keywords):
                continue

            for item in market.get("odds", []) or []:
                if not isinstance(item, dict):
                    continue

                text = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()

                # precisa ser over 0.5
                if "over" not in text or "0.5" not in text:
                    continue

                val = None
                for k in ["odd", "price", "value", "over", "Over"]:
                    if k in item:
                        val = try_float(item[k])
                        if val is not None:
                            break

                if val is None:
                    continue

                return val, market.get("name", "Unknown"), "0.5"

    return None, None, None


def extract_bet365_link(odds_json: dict, odds_event: dict = None):
    """
    Tenta achar link da Bet365 em vários lugares.
    """
    bookmakers = odds_json.get("bookmakers", {})

    if isinstance(bookmakers, dict):
        for _, markets in bookmakers.items():
            if isinstance(markets, dict):
                for key in ["url", "link", "href", "deeplink", "deepLink"]:
                    if markets.get(key):
                        return markets.get(key)

            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue

                    for key in ["url", "link", "href", "deeplink", "deepLink"]:
                        if market.get(key):
                            return market.get(key)

                    for odd_item in market.get("odds", []) or []:
                        if not isinstance(odd_item, dict):
                            continue
                        for key in ["url", "link", "href", "deeplink", "deepLink"]:
                            if odd_item.get(key):
                                return odd_item.get(key)

    if odds_event and isinstance(odds_event, dict):
        for key in ["url", "link", "href", "deeplink", "deepLink"]:
            if odds_event.get(key):
                return odds_event.get(key)

        bm = odds_event.get("bookmakers")
        if isinstance(bm, dict):
            for _, data in bm.items():
                if not isinstance(data, dict):
                    continue
                for key in ["url", "link", "href", "deeplink", "deepLink"]:
                    if data.get(key):
                        return data.get(key)

    return None

# =========================
# POSIÇÕES
# =========================
def get_positions(league_id, season, home, away):
    table = api("/standings", {"league": league_id, "season": season})

    pos_h = None
    pos_a = None

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
# JOGOS PRÉ-JOGO
# =========================
def find_games():
    global last_games

    fixtures = api("/fixtures", {"next": 50})
    now = datetime.now(timezone.utc)
    found = []

    for f in fixtures:
        league = f["league"]["name"]
        country = f["league"].get("country", "")

        if not country_league_ok(country, league):
            continue

        dt = datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00"))
        mins = int((dt - now).total_seconds() / 60)

        if 0 < mins <= ALERT_BEFORE_MIN:
            found.append({
                "fixture_id": f["fixture"]["id"],
                "league": league,
                "country": country,
                "league_id": f["league"]["id"],
                "season": f["league"]["season"],
                "home": f["teams"]["home"]["name"],
                "away": f["teams"]["away"]["name"],
                "mins": mins,
            })

    last_games = found
    return found[0] if found else None

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
# CALCULO PRÓXIMA STAKE
# =========================
def next_stake(odd):
    recover = loss_acc * RECOVER_PCT
    total = recover + target_profit
    return round(total / (odd - 1), 2)

# =========================
# MATCH ODDS EVENT
# =========================
def get_odds_events():
    try:
        return odds_get("/events", {"sport": "football", "bookmaker": BOOK, "limit": 150})
    except Exception:
        return []


def find_matching_event(home, away, league_name, odds_events):
    hn = norm_team_name(home)
    an = norm_team_name(away)
    ln = norm_text(league_name)

    # match exato com liga semelhante
    for ev in odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))
        lg = ev.get("league") or {}
        ev_league = norm_text(str(lg.get("name", "")))

        if hn == oh and an == oa:
            if ev_league and (ln in ev_league or ev_league in ln):
                return ev

    # match flexível com liga semelhante
    for ev in odds_events:
        oh = norm_team_name(str(ev.get("home", "")))
        oa = norm_team_name(str(ev.get("away", "")))
        lg = ev.get("league") or {}
        ev_league = norm_text(str(lg.get("name", "")))

        home_ok = (hn in oh or oh in hn)
        away_ok = (an in oa or oa in an)
        league_ok = (not ev_league) or (ln in ev_league or ev_league in ln)

        if home_ok and away_ok and league_ok:
            return ev

    return None

# =========================
# COMANDOS
# =========================
def cmd_status():
    if pending_game:
        tg_send(
            f"🤖 BOT ONLINE\n"
            f"Ciclo ativo: {cycle_active}\n"
            f"Aguardando aposta: {waiting_bet}\n"
            f"Tentativa: {attempt}\n"
            f"Perda acumulada: {round(loss_acc, 2)}\n"
            f"Jogo pendente: {pending_game['home']} x {pending_game['away']}"
        )
    else:
        tg_send(
            f"🤖 BOT ONLINE\n"
            f"Ciclo ativo: {cycle_active}\n"
            f"Aguardando aposta: {waiting_bet}\n"
            f"Tentativa: {attempt}\n"
            f"Perda acumulada: {round(loss_acc, 2)}\n"
            f"Jogo pendente: nenhum"
        )


def cmd_jogos():
    if not last_games:
        tg_send("Nenhum jogo encontrado na janela.")
        return

    msg = "📋 Jogos encontrados:\n\n"

    for g in last_games[:10]:
        msg += (
            f"{g['country']} - {g['league']}\n"
            f"{g['home']} x {g['away']}\n"
            f"Começa em {g['mins']} min\n\n"
        )

    tg_send(msg)


def cmd_scan():
    fixtures = api("/fixtures", {"next": 50})

    tg_send(
        f"🔎 SCAN\n"
        f"fixtures lidos: {len(fixtures)}\n"
        f"jogos válidos: {len(last_games)}"
    )


def cmd_debug():
    fixtures = api("/fixtures", {"next": 20})

    msg = "🧪 DEBUG JOGOS\n\n"

    for f in fixtures[:10]:
        country = f["league"].get("country", "")
        league = f["league"]["name"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        dt = f["fixture"]["date"]

        msg += (
            f"{country} - {league}\n"
            f"{home} x {away}\n"
            f"{dt}\n\n"
        )

    tg_send(msg)


def cmd_reset():
    global cycle_active, waiting_bet, base_stake, current_stake, target_profit, loss_acc, attempt, pending_game

    cycle_active = False
    waiting_bet = False
    base_stake = 0.0
    current_stake = 0.0
    target_profit = 0.0
    loss_acc = 0.0
    attempt = 0
    pending_game = None

    tg_send("🔄 Ciclo resetado")

# =========================
# LOOP
# =========================
def main():
    global cycle_active, waiting_bet, base_stake, current_stake, target_profit, loss_acc, attempt, pending_game, last_error

    tg_send("🤖 BOT V8 iniciado")

    offset = None

    while True:
        try:
            if not pending_game:
                find_games()

            updates = tg_get_updates(offset)

            if updates.get("result"):
                for u in updates["result"]:
                    offset = u["update_id"] + 1

                    msg = u.get("message", {})
                    txt = msg.get("text", "").strip()

                    if txt == "/status":
                        cmd_status()
                        continue

                    if txt == "/jogos":
                        cmd_jogos()
                        continue

                    if txt == "/scan":
                        cmd_scan()
                        continue

                    if txt == "/debug":
                        cmd_debug()
                        continue

                    if txt == "/reset":
                        cmd_reset()
                        continue

                    if pending_game and waiting_bet and txt.replace(".", "", 1).isdigit():
                        stake = float(txt)

                        if stake > MAX_STAKE:
                            tg_send(f"⚠️ Aposta acima do limite. Máximo permitido: {MAX_STAKE}")
                            continue

                        if not cycle_active:
                            base_stake = stake
                            target_profit = stake * (pending_game["odd"] - 1)
                            loss_acc = 0.0
                            attempt = 1
                            cycle_active = True

                        current_stake = stake
                        waiting_bet = False

                        tg_send(
                            f"✅ Aposta registrada\n"
                            f"{pending_game['home']} x {pending_game['away']}\n"
                            f"Aposta: {stake}\n"
                            f"Lucro alvo: {round(target_profit, 2)}"
                        )

            if not pending_game:
                g = find_games()

                if g:
                    odds_events = get_odds_events()
                    matched_event = find_matching_event(g["home"], g["away"], g["league"], odds_events)

                    if not matched_event:
                        continue

                    try:
                        odds_json = odds_get("/odds", {"eventId": matched_event.get("id"), "bookmakers": BOOK})
                    except Exception:
                        continue

                    odd, market_name, line = extract_match_over05_ft(odds_json)

                    if odd is None or odd < ODD_MIN:
                        continue

                    bet365_link = extract_bet365_link(odds_json, matched_event)
                    pos_h, pos_a = get_positions(
                        g["league_id"],
                        g["season"],
                        g["home"],
                        g["away"]
                    )

                    pending_game = g
                    pending_game["odd"] = odd
                    pending_game["market_name"] = market_name
                    pending_game["line"] = line
                    waiting_bet = True

                    msg = (
                        f"🚨 JOGO ENCONTRADO\n"
                        f"{g['country']} - {g['league']}\n"
                        f"{g['home']} ({pos_h}) x {g['away']} ({pos_a})\n"
                        f"🎲 Odd O0.5 FT: {odd}\n"
                        f"📌 Mercado: {market_name}\n"
                        f"📏 Linha: {line}\n"
                    )

                    if cycle_active:
                        suggested = next_stake(odd)

                        if suggested > MAX_STAKE:
                            tg_send(
                                f"⚠️ Próxima stake sugerida ({suggested}) ultrapassa o limite de {MAX_STAKE}.\n"
                                f"Ciclo pausado."
                            )
                            pending_game = None
                            waiting_bet = False
                            continue
                        else:
                            msg += (
                                f"\n📊 Ciclo ativo\n"
                                f"Perda acumulada: {round(loss_acc, 2)}\n"
                                f"Sugestão aposta: {suggested}\n"
                            )

                    if bet365_link:
                        msg += f"\n🔗 Bet365: {bet365_link}"
                    else:
                        msg += (
                            f"\n🔗 Link Bet365 indisponível na API"
                            f"\n🔎 Buscar na Bet365: {format_search_text(g['home'], g['away'])}"
                        )

                    msg += "\n\nDigite o valor da aposta"
                    tg_send(msg)

            if pending_game and cycle_active and not waiting_bet and current_stake > 0:
                res = check_result(pending_game["fixture_id"])

                if res is not None:
                    if res > 0:
                        profit = current_stake * (pending_game["odd"] - 1)

                        tg_send(
                            f"✅ GREEN\n"
                            f"Lucro: {round(profit, 2)}\n"
                            f"Ciclo reiniciado"
                        )

                        cycle_active = False
                        waiting_bet = False
                        base_stake = 0.0
                        current_stake = 0.0
                        target_profit = 0.0
                        loss_acc = 0.0
                        attempt = 0
                        pending_game = None

                    else:
                        loss_acc += current_stake
                        attempt += 1

                        tg_send(
                            f"❌ RED\n"
                            f"Perda acumulada: {round(loss_acc, 2)}\n"
                            f"Tentativa: {attempt}"
                        )

                        current_stake = 0.0
                        pending_game = None
                        waiting_bet = False

            time.sleep(POLL_SECONDS)

        except Exception as e:
            msg = f"Erro bot: {e}"
            if msg != last_error:
                tg_send(msg)
                last_error = msg
            time.sleep(30)


if __name__ == "__main__":
    main()
