import os
import time
import requests
from datetime import datetime, timedelta, timezone

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API_FOOTBALL = "https://v3.football.api-sports.io"
ODDS_API = "https://api.odds-api.io/v3"

CHECK_INTERVAL = 1800  # 30 min
LOOKAHEAD_HOURS = 12
MAX_FIXTURES_SCAN = 120

games_checked = 0
games_valid = 0
combos_sent = 0
last_run = "-"
last_combo_signature = ""
update_offset = None

ALLOWED_COMPETITIONS = [
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

def send(msg: str):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram vars ausentes")
        return

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": TG_CHAT, "text": msg},
            timeout=20,
        )
    except Exception as e:
        print("Erro Telegram:", e)

def football_api(path: str, params: dict):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    r = requests.get(API_FOOTBALL + path, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    j = r.json()

    if j.get("errors"):
        raise RuntimeError(str(j["errors"]))

    return j.get("response", [])

def odds_api(path: str, params: dict):
    params = dict(params)
    params["apiKey"] = ODDS_API_KEY
    r = requests.get(ODDS_API + path, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def parse_float(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default

def norm_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    repl = {
        " fc": "",
        " cf": "",
        " sc": "",
        " ac ": " ",
        "-": " ",
        ".": "",
        ",": "",
        "  ": " ",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    return " ".join(s.split())

def competition_allowed(country: str, league: str) -> bool:
    c = (country or "").strip().lower()
    l = (league or "").strip().lower()

    for allowed_country, allowed_league in ALLOWED_COMPETITIONS:
        if allowed_country.lower() == c and allowed_league.lower() == l:
            return True

    return False

def get_upcoming_matches():
    now = datetime.now(timezone.utc)
    limit_dt = now + timedelta(hours=LOOKAHEAD_HOURS)

    data = football_api("/fixtures", {"next": MAX_FIXTURES_SCAN})
    out = []

    for m in data:
        league_name = m["league"]["name"]
        country = m["league"].get("country", "")

        if not competition_allowed(country, league_name):
            continue

        dt = datetime.fromisoformat(m["fixture"]["date"].replace("Z", "+00:00"))
        if not (now <= dt <= limit_dt):
            continue

        out.append(m)

    return out

def get_team_stats(team_id: int, league_id: int, season: int):
    return football_api(
        "/teams/statistics",
        {"team": team_id, "league": league_id, "season": season},
    )

def score_match(match: dict):
    global games_checked, games_valid
    games_checked += 1

    home_id = match["teams"]["home"]["id"]
    away_id = match["teams"]["away"]["id"]
    league_id = match["league"]["id"]
    season = match["league"]["season"]

    try:
        sh = get_team_stats(home_id, league_id, season)
        sa = get_team_stats(away_id, league_id, season)
    except Exception:
        return False, 0, {}

    home_avg_home = parse_float(sh["goals"]["for"]["average"]["home"])
    away_avg_away = parse_float(sa["goals"]["for"]["average"]["away"])
    home_against_home = parse_float(sh["goals"]["against"]["average"]["home"])
    away_against_away = parse_float(sa["goals"]["against"]["average"]["away"])

    home_played = sh["fixtures"]["played"]["total"] or 0
    away_played = sa["fixtures"]["played"]["total"] or 0

    score = 0

    if home_avg_home >= 1.20:
        score += 2
    if away_avg_away >= 0.90:
        score += 2
    if home_against_home >= 0.70:
        score += 1
    if away_against_away >= 0.70:
        score += 1
    if home_avg_home + away_avg_away >= 2.20:
        score += 2
    if home_played >= 8 and away_played >= 8:
        score += 1

    ok = (
        home_avg_home >= 1.20 and
        away_avg_away >= 0.90 and
        (home_avg_home + away_avg_away) >= 2.20
    )

    if ok:
        games_valid += 1

    details = {
        "home_avg_home": round(home_avg_home, 2),
        "away_avg_away": round(away_avg_away, 2),
        "home_against_home": round(home_against_home, 2),
        "away_against_away": round(away_against_away, 2),
        "internal_score": score,
    }

    return ok, score, details

def get_odds_events():
    try:
        events = odds_api("/events", {"sport": "football", "bookmaker": "Bet365", "limit": 300})
        return events if isinstance(events, list) else []
    except Exception:
        return []

def find_matching_event(home: str, away: str, league_name: str, odds_events: list):
    hn = norm_text(home)
    an = norm_text(away)
    ln = norm_text(league_name)

    for ev in odds_events:
        oh = norm_text(str(ev.get("home", "")))
        oa = norm_text(str(ev.get("away", "")))
        lg = ev.get("league") or {}
        ev_league = norm_text(str(lg.get("name", "")))

        if hn == oh and an == oa:
            if not ev_league or ln in ev_league or ev_league in ln:
                return ev

    for ev in odds_events:
        oh = norm_text(str(ev.get("home", "")))
        oa = norm_text(str(ev.get("away", "")))
        lg = ev.get("league") or {}
        ev_league = norm_text(str(lg.get("name", "")))

        home_ok = (hn in oh or oh in hn)
        away_ok = (an in oa or oa in an)
        league_ok = (not ev_league) or (ln in ev_league or ev_league in ln)

        if home_ok and away_ok and league_ok:
            return ev

    return None

def get_event_odds(event_id):
    try:
        return odds_api("/odds", {"eventId": event_id, "bookmakers": "Bet365"})
    except Exception:
        return None

def extract_over05_ft(odds_json):
    """
    Retorna odd real do mercado Over 0.5 FT da partida inteira.
    """
    if not odds_json:
        return None, None

    bookmakers = odds_json.get("bookmakers", {})
    if not bookmakers:
        return None, None

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

            if any(bad in market_name for bad in invalid_market_keywords):
                continue

            if not any(ok in market_name for ok in valid_market_keywords):
                continue

            for item in market.get("odds", []) or []:
                if not isinstance(item, dict):
                    continue

                txt = " ".join([str(k) + " " + str(v) for k, v in item.items()]).lower()
                if "over" not in txt or "0.5" not in txt:
                    continue

                odd_val = None
                for k in ["odd", "price", "value", "over", "Over"]:
                    if k in item:
                        odd_val = parse_float(item[k], default=0.0)
                        if odd_val > 0:
                            break

                if odd_val and 1.01 <= odd_val <= 1.30:
                    return odd_val, market.get("name", "Over/Under")

    return None, None

def get_bet365_link_from_event(event):
    if not isinstance(event, dict):
        return None

    for key in ["url", "link", "href", "deeplink", "deepLink"]:
        if event.get(key):
            return event.get(key)

    bookmakers = event.get("bookmakers")
    if isinstance(bookmakers, dict):
        for _, data in bookmakers.items():
            if not isinstance(data, dict):
                continue
            for key in ["url", "link", "href", "deeplink", "deepLink"]:
                if data.get(key):
                    return data.get(key)

    return None

def build_combo():
    matches = get_upcoming_matches()
    odds_events = get_odds_events()

    rated = []

    for m in matches:
        try:
            ok, score, details = score_match(m)
            if not ok:
                continue

            home = m["teams"]["home"]["name"]
            away = m["teams"]["away"]["name"]
            league = m["league"]["name"]

            ev = find_matching_event(home, away, league, odds_events)
            if not ev:
                continue

            odds_json = get_event_odds(ev.get("id"))
            odd, market_name = extract_over05_ft(odds_json)
            if not odd:
                continue

            details["odd"] = odd
            details["market_name"] = market_name
            details["link"] = get_bet365_link_from_event(ev)

            rated.append((score, odd, m, details))
        except Exception:
            continue

    # prioriza jogos mais fortes e com odd útil
    rated.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # tenta tripla 1.25~1.35
    best_three = None
    for i in range(len(rated)):
        for j in range(i + 1, len(rated)):
            for k in range(j + 1, len(rated)):
                combo = [rated[i], rated[j], rated[k]]
                product = combo[0][1] * combo[1][1] * combo[2][1]
                if 1.25 <= product <= 1.35:
                    best_three = combo
                    break
            if best_three:
                break
        if best_three:
            break

    if best_three:
        return best_three

    # tenta dupla 1.15~1.28
    best_two = None
    for i in range(len(rated)):
        for j in range(i + 1, len(rated)):
            combo = [rated[i], rated[j]]
            product = combo[0][1] * combo[1][1]
            if 1.15 <= product <= 1.28:
                best_two = combo
                break
        if best_two:
            break

    if best_two:
        return best_two

    return []

def combo_signature(combo):
    ids = [str(item[2]["fixture"]["id"]) for item in combo]
    return "|".join(ids)

def format_combo(combo):
    kind = "TRIPLA" if len(combo) == 3 else "DUPLA"
    total_odd = 1.0
    lines = [f"🔥 {kind} OVER 0.5 FT\n"]

    for _, odd, match, d in combo:
        total_odd *= odd
        league = match["league"]["name"]
        country = match["league"].get("country", "")
        home = match["teams"]["home"]["name"]
        away = match["teams"]["away"]["name"]
        dt = match["fixture"]["date"]

        lines.append(
            f"{country} - {league}\n"
            f"{home} x {away}\n"
            f"{dt}\n"
            f"odd real: {odd}\n"
            f"mercado: {d['market_name']}\n"
            f"média casa: {d['home_avg_home']} | média fora: {d['away_avg_away']}\n"
        )

    lines.append(f"Odd final estimada: {round(total_odd, 3)}\n")

    first_link = combo[0][3].get("link")
    if first_link:
        lines.append(f"🔗 Bet365:\n{first_link}")
    else:
        home = combo[0][2]["teams"]["home"]["name"]
        away = combo[0][2]["teams"]["away"]["name"]
        lines.append(
            f"🔎 Buscar na Bet365:\n"
            f"https://www.google.com/search?q=bet365+{home}+vs+{away}"
        )

    return "\n".join(lines)

def check_commands():
    global update_offset

    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    params = {"timeout": 5}

    if update_offset is not None:
        params["offset"] = update_offset

    try:
        r = requests.get(url, params=params, timeout=20).json()
    except Exception:
        return

    for u in r.get("result", []):
        update_offset = u["update_id"] + 1

        try:
            chat = u["message"]["chat"]["id"]
            text = u["message"]["text"].strip()

            if str(chat) != str(TG_CHAT):
                continue

            if text == "/status":
                msg = (
                    "🤖 BOT ONLINE\n\n"
                    f"Jogos analisados: {games_checked}\n"
                    f"Jogos aprovados: {games_valid}\n"
                    f"Combos enviados hoje: {combos_sent}\n"
                    f"Última análise: {last_run}"
                )
                send(msg)

        except Exception:
            pass

def main():
    global combos_sent, last_run, last_combo_signature

    send("🤖 BOT OVER 0.5 PRÉ-JOGO + ODDS REAIS INICIADO")

    while True:
        try:
            combo = build_combo()
            last_run = datetime.now().strftime("%H:%M")

            if combo:
                sig = combo_signature(combo)

                if sig != last_combo_signature:
                    send(format_combo(combo))
                    combos_sent += 1
                    last_combo_signature = sig
            else:
                print("Sem combo suficiente nesta rodada")

            check_commands()

        except Exception as e:
            send(f"erro {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
