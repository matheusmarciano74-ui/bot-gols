import itertools
import json
import os
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests

# =========================================================
# CONFIG
# =========================================================

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_FOOTBALL_URL = "https://v3.football.api-sports.io"
TELEGRAM_BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}
STATE_FILE = "bot_state.json"

INTERVALO_LOOP_SEGUNDOS = 15
MINUTO_MIN = 1
MINUTO_MAX = 25

STAKE_BASE_PCT = 0.15
MAX_LOSS_PCT = 0.25
MAX_TENTATIVAS_DIA = 6

BOOKMAKER_PREFERIDO = "Bet365"

# múltipla
ODD_MIN_MULTIPLA = 1.80
MAX_JOGOS_MULTIPLA = 4   # 2, 3 ou 4
MAX_CANDIDATOS_ANALISE = 8

# =========================================================
# LIGAS
# =========================================================

LIGAS_PERMITIDAS = {
    ("England", "Premier League"),
    ("England", "Championship"),
    ("Spain", "La Liga"),
    ("Spain", "Segunda División"),
    ("Italy", "Serie A"),
    ("Italy", "Serie B"),
    ("Germany", "Bundesliga"),
    ("Germany", "2. Bundesliga"),
    ("France", "Ligue 1"),
    ("France", "Ligue 2"),
    ("Portugal", "Primeira Liga"),
    ("Netherlands", "Eredivisie"),
    ("Belgium", "Jupiler Pro League"),
    ("Turkey", "Süper Lig"),
    ("Brazil", "Serie A"),
    ("Brazil", "Serie B"),
    ("Argentina", "Liga Profesional Argentina"),
}

# =========================================================
# ESTADO
# =========================================================

standings_cache = {}
last_update_id = 0


def hoje_str():
    return datetime.now().strftime("%Y-%m-%d")


def default_state():
    return {
        "day": hoje_str(),
        "banca_inicial": None,
        "stake_base_pct": STAKE_BASE_PCT,
        "max_loss_pct": MAX_LOSS_PCT,
        "ciclo_ativo": False,
        "tentativa": 0,
        "perda_acumulada": 0.0,
        "paused": False,
        "pending_bet": None,
        "sent_keys_today": [],
        "last_limit_alert_day": "",
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        state = default_state()
        save_state(state)
        return state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = default_state()
        save_state(state)
        return state

    for k, v in default_state().items():
        if k not in state:
            state[k] = v

    if state.get("day") != hoje_str():
        banca = state.get("banca_inicial")
        state = default_state()
        state["banca_inicial"] = banca
        save_state(state)

    return state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


state = load_state()

# =========================================================
# UTIL
# =========================================================

def fmt_money(v):
    return f"{float(v):.2f}"


def reset_if_new_day():
    global state
    if state.get("day") != hoje_str():
        banca = state.get("banca_inicial")
        state = default_state()
        state["banca_inicial"] = banca
        save_state(state)


def build_google_link(home, away):
    q = quote_plus(f"Bet365 {home} x {away} over 0.5")
    return f"https://www.google.com/search?q={q}"


def build_bet365_search_link(home, away):
    q = quote_plus(f"{home} x {away} site:bet365.com")
    return f"https://www.google.com/search?q={q}"


def get_banca():
    return float(state["banca_inicial"] or 0.0)


def get_stake_base():
    banca = get_banca()
    if banca <= 0:
        return 0.0
    return round(banca * float(state["stake_base_pct"]), 2)


def get_limite_loss():
    banca = get_banca()
    if banca <= 0:
        return 0.0
    return round(banca * float(state["max_loss_pct"]), 2)


def is_number(text):
    try:
        float(text.replace(",", "."))
        return True
    except Exception:
        return False


def calc_combined_odd(items):
    odd = 1.0
    for item in items:
        odd *= float(item["odd_real"])
    return round(odd, 3)


def lucro_liquido(stake, odd):
    return round((stake * odd) - stake, 2)


def retorno_bruto(stake, odd):
    return round(stake * odd, 2)


def calcular_stake_sugerida(odd_total):
    if odd_total <= 1.0:
        return None

    perda = float(state["perda_acumulada"])
    stake_base = get_stake_base()
    lucro_alvo = round(stake_base * max(odd_total - 1.0, 0), 2)

    stake = (perda + lucro_alvo) / (odd_total - 1.0)
    return round(stake, 2)


def stake_dentro_do_limite(stake):
    limite = get_limite_loss()
    perda = float(state["perda_acumulada"])
    restante = round(limite - perda, 2)
    return stake <= restante, restante


def league_allowed(country, league_name):
    return (country, league_name) in LIGAS_PERMITIDAS


# =========================================================
# TELEGRAM
# =========================================================

def telegram_ok():
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text):
    if not telegram_ok():
        return False

    url = f"{TELEGRAM_BASE_URL}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        return True
    except Exception:
        return False


def get_updates():
    global last_update_id

    if not telegram_ok():
        return []

    url = f"{TELEGRAM_BASE_URL}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 0}

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return []

        updates = data.get("result", [])
        if updates:
            last_update_id = updates[-1]["update_id"]
        return updates
    except Exception:
        return []


def status_text():
    pb = state["pending_bet"]
    pendente = "nenhuma"
    stake_reg = "-"
    odd_reg = "-"

    if pb:
        jogos_txt = " | ".join([f"{j['home']} x {j['away']}" for j in pb["jogos"]])
        pendente = jogos_txt
        if pb.get("stake") is not None:
            stake_reg = fmt_money(pb["stake"])
        odd_reg = str(pb.get("odd_total", "-"))

    return (
        "🤖 BOT ONLINE\n"
        f"Ciclo ativo: {state['ciclo_ativo']}\n"
        f"Tentativa: {state['tentativa']}\n"
        f"Banca: {fmt_money(get_banca())}\n"
        f"Stake base (15%): {fmt_money(get_stake_base())}\n"
        f"Limite perda dia (25%): {fmt_money(get_limite_loss())}\n"
        f"Perda acumulada: {fmt_money(state['perda_acumulada'])}\n"
        f"Múltipla pendente: {pendente}\n"
        f"Odd pendente: {odd_reg}\n"
        f"Stake registrada: {stake_reg}\n"
        f"Pausado: {state['paused']}"
    )


def handle_command(text):
    global state
    raw = text.strip()
    t = raw.lower()

    if t in ("/status", "status", "/ping", "ping"):
        send_telegram(status_text())
        return

    if t == "/debug":
        debug_resumo()
        return

    if t.startswith("/banca "):
        valor_txt = raw.split(" ", 1)[1].strip().replace(",", ".")
        if not is_number(valor_txt):
            send_telegram("❌ Use assim: /banca 1000")
            return

        banca = round(float(valor_txt), 2)
        state["banca_inicial"] = banca
        state["tentativa"] = 0
        state["perda_acumulada"] = 0.0
        state["paused"] = False
        state["ciclo_ativo"] = False
        state["pending_bet"] = None
        save_state(state)

        send_telegram(
            "✅ Banca registrada\n"
            f"Banca: {fmt_money(banca)}\n"
            f"Stake base (15%): {fmt_money(get_stake_base())}\n"
            f"Limite perda dia (25%): {fmt_money(get_limite_loss())}"
        )
        return

    if t == "/resetday":
        banca = state.get("banca_inicial")
        state = default_state()
        state["banca_inicial"] = banca
        save_state(state)
        send_telegram("✅ Dia resetado.")
        return

    if t == "/skip":
        if state["pending_bet"]:
            state["pending_bet"] = None
            save_state(state)
            send_telegram("⏭ Múltipla pendente removida.")
        else:
            send_telegram("ℹ️ Não há múltipla pendente.")
        return

    if t == "/win":
        if state["pending_bet"] is None:
            send_telegram("ℹ️ Não há múltipla pendente para WIN.")
            return

        state["ciclo_ativo"] = False
        state["tentativa"] = 0
        state["perda_acumulada"] = 0.0
        state["paused"] = False
        state["pending_bet"] = None
        save_state(state)
        send_telegram("✅ WIN registrado\nCiclo zerado.")
        return

    if t == "/loss":
        if state["pending_bet"] is None:
            send_telegram("ℹ️ Não há múltipla pendente para LOSS.")
            return

        stake = float(state["pending_bet"].get("stake") or 0.0)

        state["ciclo_ativo"] = True
        state["tentativa"] += 1
        state["perda_acumulada"] = round(state["perda_acumulada"] + stake, 2)
        state["pending_bet"] = None

        if (
            state["tentativa"] >= MAX_TENTATIVAS_DIA
            or state["perda_acumulada"] >= get_limite_loss()
        ):
            state["paused"] = True

        save_state(state)

        msg = (
            "❌ LOSS registrado\n"
            f"Perda acumulada: {fmt_money(state['perda_acumulada'])}\n"
            f"Tentativa: {state['tentativa']}\n"
        )

        if state["paused"]:
            msg += "🚫 Ciclo pausado por limite diário. Use /resetday."
        else:
            msg += "➡️ Bot aguardando nova múltipla."

        send_telegram(msg)
        return

    if is_number(raw):
        if state["pending_bet"] is None:
            send_telegram("ℹ️ Não há múltipla pendente para registrar aposta.")
            return

        stake = round(float(raw.replace(",", ".")), 2)
        odd = float(state["pending_bet"]["odd_total"])
        bruto = retorno_bruto(stake, odd)
        lucro = lucro_liquido(stake, odd)

        state["pending_bet"]["stake"] = stake
        state["pending_bet"]["retorno_bruto"] = bruto
        state["pending_bet"]["lucro_alvo"] = lucro
        save_state(state)

        send_telegram(
            "✅ Aposta registrada\n"
            f"Qtd. jogos: {len(state['pending_bet']['jogos'])}\n"
            f"Aposta: {fmt_money(stake)}\n"
            f"Odd total: {odd}\n"
            f"Retorno bruto: {fmt_money(bruto)}\n"
            f"Lucro líquido: {fmt_money(lucro)}\n\n"
            "Depois mande:\n/win  ou  /loss"
        )
        return


def process_updates():
    updates = get_updates()
    for upd in updates:
        msg = upd.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        if chat != TELEGRAM_CHAT_ID:
            continue

        text = (msg.get("text") or "").strip()
        if not text:
            continue

        handle_command(text)


# =========================================================
# API FOOTBALL
# =========================================================

def football_get(path, params=None):
    url = f"{BASE_FOOTBALL_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def get_standings_map(league_id, season):
    key = f"{league_id}_{season}"

    if key in standings_cache:
        return standings_cache[key]

    data = football_get("/standings", params={
        "league": league_id,
        "season": season,
    })

    resp = data.get("response", [])
    if not resp:
        standings_cache[key] = None
        return None

    standings_groups = resp[0].get("league", {}).get("standings", [])
    if not standings_groups:
        standings_cache[key] = None
        return None

    table = standings_groups[0]
    teams_count = len(table)
    top_half_limit = teams_count / 2.0

    mapping = {}
    for row in table:
        team_id = row["team"]["id"]
        rank = row["rank"]

        played = row.get("all", {}).get("played", 0) or 0
        gf = row.get("all", {}).get("goals", {}).get("for", 0) or 0
        ga = row.get("all", {}).get("goals", {}).get("against", 0) or 0

        gf_avg = (gf / played) if played > 0 else 0.0
        ga_avg = (ga / played) if played > 0 else 0.0
        total_avg = ((gf + ga) / played) if played > 0 else 0.0

        mapping[team_id] = {
            "rank": rank,
            "teams_count": teams_count,
            "is_top_half": rank <= top_half_limit,
            "played": played,
            "gf_avg": round(gf_avg, 2),
            "ga_avg": round(ga_avg, 2),
            "total_avg": round(total_avg, 2),
        }

    standings_cache[key] = mapping
    return mapping


def parse_over05_from_odds_response(data):
    responses = data.get("response", [])
    found = []

    for item in responses:
        bookmakers = item.get("bookmakers", [])
        for bookmaker in bookmakers:
            bname = bookmaker.get("name", "")
            bets = bookmaker.get("bets", [])

            for bet in bets:
                bet_name = (bet.get("name") or "").lower()

                if (
                    "over" not in bet_name
                    and "under" not in bet_name
                    and "goal" not in bet_name
                    and "total" not in bet_name
                ):
                    continue

                values = bet.get("values", [])
                for val in values:
                    label = (val.get("value") or "").lower().replace(" ", "")
                    odd = val.get("odd")

                    if "over" in label and "0.5" in label:
                        try:
                            odd_f = float(str(odd).replace(",", "."))
                            found.append((bname, odd_f))
                        except Exception:
                            continue

    if not found:
        return None, None

    for bname, odd in found:
        if bname.strip().lower() == BOOKMAKER_PREFERIDO.lower():
            return odd, bname

    return found[0][1], found[0][0]


def get_live_over05_odd(fixture_id):
    try:
        data = football_get("/odds/live", params={"fixture": fixture_id})
        odd, book = parse_over05_from_odds_response(data)
        return odd, book
    except Exception:
        return None, None


def fixture_ok(fx):
    fixture = fx["fixture"]
    league = fx["league"]
    teams = fx["teams"]
    goals = fx["goals"]

    minute = fixture["status"].get("elapsed") or 0
    country = league.get("country") or ""
    league_name = league.get("name") or ""
    league_id = league.get("id")
    season = league.get("season")

    if not league_allowed(country, league_name):
        return False

    if not (MINUTO_MIN <= minute <= MINUTO_MAX):
        return False

    total_goals = (goals.get("home") or 0) + (goals.get("away") or 0)
    if total_goals != 0:
        return False

    standings = get_standings_map(league_id, season)
    if not standings:
        return False

    home_id = teams["home"]["id"]
    away_id = teams["away"]["id"]

    home_row = standings.get(home_id)
    away_row = standings.get(away_id)

    if not home_row or not away_row:
        return False

    # pelo menos 1 time acima da metade
    if not (home_row["is_top_half"] or away_row["is_top_half"]):
        return False

    # pelo menos 1 ofensivo
    if not (home_row["gf_avg"] >= 1.40 or away_row["gf_avg"] >= 1.40):
        return False

    # média total do confronto
    media_total_confronto = round(
        (home_row["total_avg"] + away_row["total_avg"]) / 2.0, 2
    )
    if media_total_confronto < 2.40:
        return False

    return True


def fetch_live_candidates():
    data = football_get("/fixtures", params={"live": "all"})
    resp = data.get("response", [])
    candidates = []

    for fx in resp:
        try:
            if not fixture_ok(fx):
                continue

            fixture_id = fx["fixture"]["id"]
            minute = fx["fixture"]["status"].get("elapsed") or 0
            home = fx["teams"]["home"]["name"]
            away = fx["teams"]["away"]["name"]
            country = fx["league"]["country"]
            league_name = fx["league"]["name"]

            odd_real, bookmaker = get_live_over05_odd(fixture_id)
            if odd_real is None or odd_real <= 1.01:
                continue

            candidates.append({
                "fixture_id": fixture_id,
                "home": home,
                "away": away,
                "minute": minute,
                "country": country,
                "league_name": league_name,
                "odd_real": round(float(odd_real), 3),
                "bookmaker": bookmaker or "-",
                "google_link": build_google_link(home, away),
                "bet365_search_link": build_bet365_search_link(home, away),
            })
        except Exception:
            continue

    return candidates


def choose_best_multiple(candidates):
    if not candidates:
        return None

    sent_today = set(state["sent_keys_today"])

    ordered = sorted(
        candidates,
        key=lambda x: (-x["odd_real"], -x["minute"], x["fixture_id"])
    )[:MAX_CANDIDATOS_ANALISE]

    max_n = min(MAX_JOGOS_MULTIPLA, len(ordered))
    min_n = 2

    melhor = None

    for n in range(min_n, max_n + 1):
        for combo in itertools.combinations(ordered, n):
            ids = tuple(sorted([c["fixture_id"] for c in combo]))
            if str(ids) in sent_today:
                continue

            odd_total = calc_combined_odd(combo)

            if odd_total < ODD_MIN_MULTIPLA:
                continue

            media_min = round(sum(c["minute"] for c in combo) / len(combo), 2)

            atual = {
                "jogos": list(combo),
                "odd_total": odd_total,
                "key": str(ids),
                "qtd": len(combo),
                "media_min": media_min,
            }

            if melhor is None:
                melhor = atual
            else:
                if atual["qtd"] < melhor["qtd"]:
                    melhor = atual
                elif atual["qtd"] == melhor["qtd"]:
                    if atual["odd_total"] > melhor["odd_total"]:
                        melhor = atual
                    elif atual["odd_total"] == melhor["odd_total"] and atual["media_min"] > melhor["media_min"]:
                        melhor = atual

        if melhor is not None:
            break

    return melhor


def debug_resumo():
    try:
        data = football_get("/fixtures", params={"live": "all"})
        resp = data.get("response", [])

        total_live = len(resp)
        ligas_ok = 0
        pre_ok = 0
        top_half_ok = 0
        ofensivo_ok = 0
        odds_ok = 0

        exemplos = []

        for fx in resp:
            try:
                fixture = fx["fixture"]
                league = fx["league"]
                teams = fx["teams"]
                goals = fx["goals"]

                minute = fixture["status"].get("elapsed") or 0
                country = league.get("country") or ""
                league_name = league.get("name") or ""
                league_id = league.get("id")
                season = league.get("season")

                if league_allowed(country, league_name):
                    ligas_ok += 1
                else:
                    continue

                total_goals = (goals.get("home") or 0) + (goals.get("away") or 0)
                if MINUTO_MIN <= minute <= MINUTO_MAX and total_goals == 0:
                    pre_ok += 1
                else:
                    continue

                standings = get_standings_map(league_id, season)
                if not standings:
                    continue

                home_id = teams["home"]["id"]
                away_id = teams["away"]["id"]

                home_row = standings.get(home_id)
                away_row = standings.get(away_id)

                if not home_row or not away_row:
                    continue

                if home_row["is_top_half"] or away_row["is_top_half"]:
                    top_half_ok += 1
                else:
                    continue

                pelo_menos_um_ofensivo = (
                    home_row["gf_avg"] >= 1.40 or
                    away_row["gf_avg"] >= 1.40
                )
                media_total_confronto = round(
                    (home_row["total_avg"] + away_row["total_avg"]) / 2.0, 2
                )

                if pelo_menos_um_ofensivo and media_total_confronto >= 2.40:
                    ofensivo_ok += 1
                else:
                    continue

                odd_real, bookmaker = get_live_over05_odd(fixture["id"])
                if odd_real and odd_real > 1.01:
                    odds_ok += 1
                    exemplos.append(
                        f"{teams['home']['name']} x {teams['away']['name']} | "
                        f"{league_name} | min {minute} | "
                        f"odd {odd_real} | "
                        f"GFavg {home_row['gf_avg']}/{away_row['gf_avg']} | "
                        f"TotAvg {media_total_confronto} | "
                        f"{bookmaker}"
                    )
            except Exception:
                continue

        msg = (
            "📊 DEBUG FILTRO\n"
            f"Jogos ao vivo: {total_live}\n"
            f"Ligas válidas: {ligas_ok}\n"
            f"0x0 até 25': {pre_ok}\n"
            f"1 time top-half: {top_half_ok}\n"
            f"Perfil ofensivo: {ofensivo_ok}\n"
            f"Com odd live O0.5: {odds_ok}\n"
        )

        if exemplos:
            msg += "\nExemplos:\n" + "\n".join(exemplos[:5])

        send_telegram(msg)
    except Exception as e:
        send_telegram(f"Erro debug: {e}")


# =========================================================
# ALERTA
# =========================================================

def can_send_new_alert():
    if state["paused"]:
        return False
    if state["pending_bet"] is not None:
        return False
    if state["tentativa"] >= MAX_TENTATIVAS_DIA:
        return False
    if state["perda_acumulada"] >= get_limite_loss() > 0:
        return False
    if get_banca() <= 0:
        return False
    return True


def maybe_pause_and_alert():
    if not state["paused"]:
        return
    if state["last_limit_alert_day"] == hoje_str():
        return

    send_telegram(
        "🚫 Ciclo pausado por limite diário.\n"
        f"Tentativas: {state['tentativa']}\n"
        f"Perda acumulada: {fmt_money(state['perda_acumulada'])}\n"
        f"Limite do dia: {fmt_money(get_limite_loss())}\n"
        "Use /resetday para reabrir."
    )
    state["last_limit_alert_day"] = hoje_str()
    save_state(state)


def send_new_bet_alert(mult):
    odd_total = float(mult["odd_total"])
    stake_sugerida = calcular_stake_sugerida(odd_total)
    if stake_sugerida is None:
        return False

    ok_limite, restante = stake_dentro_do_limite(stake_sugerida)

    if not ok_limite:
        state["paused"] = True
        save_state(state)
        send_telegram(
            "🚫 Entrada bloqueada por limite de martingale do dia.\n"
            f"Stake necessária: {fmt_money(stake_sugerida)}\n"
            f"Restante do limite diário: {fmt_money(restante)}\n"
            "Use /status ou /resetday."
        )
        return False

    linhas = []
    for idx, j in enumerate(mult["jogos"], start=1):
        linhas.append(
            f"{idx}) {j['home']} x {j['away']}\n"
            f"   {j['league_name']} - {j['country']}\n"
            f"   Min: {j['minute']} | Odd O0.5: {j['odd_real']} | {j['bookmaker']}\n"
            f"   Bet365: {j['bet365_search_link']}"
        )

    msg = (
        f"🚨 MÚLTIPLA ENCONTRADA ({mult['qtd']} jogos)\n"
        f"Odd total: {odd_total}\n"
        f"Stake sugerida: {fmt_money(stake_sugerida)}\n"
        f"Stake base (15%): {fmt_money(get_stake_base())}\n"
        f"Limite perda dia (25%): {fmt_money(get_limite_loss())}\n"
        f"Perda acumulada: {fmt_money(state['perda_acumulada'])}\n\n"
        + "\n\n".join(linhas) +
        "\n\nDigite o valor da aposta para registrar."
    )

    ok = send_telegram(msg)
    if not ok:
        return False

    state["pending_bet"] = {
        "type": "multipla",
        "qtd": mult["qtd"],
        "odd_total": odd_total,
        "stake_sugerida": stake_sugerida,
        "jogos": mult["jogos"],
        "stake": None,
        "retorno_bruto": None,
        "lucro_alvo": None,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "key": mult["key"],
    }

    keys = list(state["sent_keys_today"])
    keys.append(mult["key"])
    state["sent_keys_today"] = keys[-200:]
    state["ciclo_ativo"] = True
    save_state(state)
    return True


# =========================================================
# LOOP
# =========================================================

def loop_principal():
    while True:
        reset_if_new_day()

        try:
            process_updates()
        except Exception:
            pass

        try:
            maybe_pause_and_alert()
        except Exception:
            pass

        try:
            if can_send_new_alert():
                candidates = fetch_live_candidates()
                best = choose_best_multiple(candidates)
                if best:
                    send_new_bet_alert(best)
        except Exception:
            pass

        time.sleep(INTERVALO_LOOP_SEGUNDOS)


if __name__ == "__main__":
    loop_principal()
