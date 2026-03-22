import time
import json
import math
import sqlite3
import requests
import os
from datetime import datetime, timedelta, timezone

# =========================================================
# CONFIG
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL_SECONDS = 60
MAX_SIGNALS_PER_DAY = 12
MIN_MINUTE_TO_SCAN = 12
MAX_MINUTE_TO_SCAN = 80

# Faixas de odd aceitas
ODD_RANGE_OVER05_HT = (1.30, 1.65)
ODD_RANGE_OVER15_FT = (1.45, 1.95)
ODD_RANGE_OVER25_FT = (1.90, 2.80)

# Evita repetir alerta do mesmo jogo/mercado por muito tempo
SIGNAL_COOLDOWN_MINUTES = 45

# Mercados habilitados
ENABLE_OVER05_HT = True
ENABLE_OVER15_FT = True
ENABLE_OVER25_FT = True

# Controle básico
REQUIRE_MIN_DANGEROUS_ATTACKS = True
ALLOW_LOW_LEAGUES = False

# Ligas permitidas (exemplo; adapte)
ALLOWED_LEAGUES = {
    "Brazil Serie A",
    "Brazil Serie B",
    "England Premier League",
    "Spain La Liga",
    "Italy Serie A",
    "Germany Bundesliga",
    "France Ligue 1",
    "Portugal Primeira Liga",
    "Netherlands Eredivisie",
    "Argentina Liga Profesional"
}

DB_FILE = "bet_alerts.db"


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        print(f"[ERRO TELEGRAM] {e}")


# =========================================================
# BANCO
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sent_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            market TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            UNIQUE(match_id, market)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_counter (
            date_key TEXT PRIMARY KEY,
            total_signals INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def get_today_key():
    return datetime.now().strftime("%Y-%m-%d")


def get_daily_signal_count():
    today = get_today_key()
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT total_signals FROM daily_counter WHERE date_key = ?", (today,))
    row = cur.fetchone()
    conn.close()

    return row[0] if row else 0


def increment_daily_signal_count():
    today = get_today_key()
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT total_signals FROM daily_counter WHERE date_key = ?", (today,))
    row = cur.fetchone()

    if row:
        cur.execute(
            "UPDATE daily_counter SET total_signals = total_signals + 1 WHERE date_key = ?",
            (today,)
        )
    else:
        cur.execute(
            "INSERT INTO daily_counter (date_key, total_signals) VALUES (?, ?)",
            (today, 1)
        )

    conn.commit()
    conn.close()


def was_signal_sent_recently(match_id: str, market: str, cooldown_minutes: int) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        "SELECT sent_at FROM sent_signals WHERE match_id = ? AND market = ?",
        (match_id, market)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return False

    sent_at = datetime.fromisoformat(row[0])
    return datetime.now() - sent_at < timedelta(minutes=cooldown_minutes)


def register_signal(match_id: str, market: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    now_iso = datetime.now().isoformat()
    cur.execute("""
        INSERT INTO sent_signals (match_id, market, sent_at)
        VALUES (?, ?, ?)
        ON CONFLICT(match_id, market)
        DO UPDATE SET sent_at = excluded.sent_at
    """, (match_id, market, now_iso))

    conn.commit()
    conn.close()


# =========================================================
# UTIL
# =========================================================

def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except:
        return default


def safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(float(v))
    except:
        return default


def percent(a, b):
    if b == 0:
        return 0
    return (a / b) * 100


def normalize_league_name(league_name: str) -> str:
    return (league_name or "").strip()


def is_allowed_league(league_name: str) -> bool:
    if ALLOW_LOW_LEAGUES:
        return True
    return normalize_league_name(league_name) in ALLOWED_LEAGUES


def format_match_header(m):
    return f"{m['home']} x {m['away']}"

def current_score_str(m):
    return f"{m['home_goals']}x{m['away_goals']}"


# =========================================================
# PROVEDOR DE DADOS
# =========================================================
# AQUI VOCÊ PLUGA SUA FONTE ATUAL
#
# O BOT ESPERA UMA LISTA DE JOGOS NESSE FORMATO:
#
# [
#   {
#       "match_id": "12345",
#       "league": "Brazil Serie A",
#       "minute": 28,
#       "period": "1H",   # "1H" ou "2H"
#       "home": "Flamengo",
#       "away": "Bahia",
#       "home_goals": 0,
#       "away_goals": 0,
#       "shots_total": 8,
#       "shots_on_target": 3,
#       "dangerous_attacks": 52,
#       "attacks": 78,
#       "corners": 4,
#       "red_cards": 0,
#       "odds": {
#           "over_0_5_ht": 1.47,
#           "over_1_5_ft": 1.61,
#           "over_2_5_ft": 2.20
#       }
#   }
# ]
#
# Se no seu bot antigo você já puxa isso de uma API,
# é só adaptar a função fetch_live_matches().
# =========================================================

def fetch_live_matches():
    """
    ADAPTE ESTA FUNÇÃO PARA SUA FONTE DE DADOS REAL.

    Retorne uma lista de dicts no formato esperado acima.
    """
    # -------------------------------------------------
    # EXEMPLO FALSO / MOCK SÓ PRA ESTRUTURA FUNCIONAR
    # REMOVA ISSO E COLE SUA LÓGICA REAL AQUI
    # -------------------------------------------------
    mock_data = [
        {
            "match_id": "1001",
            "league": "Brazil Serie A",
            "minute": 27,
            "period": "1H",
            "home": "Flamengo",
            "away": "Bahia",
            "home_goals": 0,
            "away_goals": 0,
            "shots_total": 9,
            "shots_on_target": 3,
            "dangerous_attacks": 49,
            "attacks": 76,
            "corners": 5,
            "red_cards": 0,
            "odds": {
                "over_0_5_ht": 1.44,
                "over_1_5_ft": 1.60,
                "over_2_5_ft": 2.18
            }
        },
        {
            "match_id": "1002",
            "league": "England Premier League",
            "minute": 63,
            "period": "2H",
            "home": "Arsenal",
            "away": "Brighton",
            "home_goals": 1,
            "away_goals": 0,
            "shots_total": 15,
            "shots_on_target": 6,
            "dangerous_attacks": 68,
            "attacks": 101,
            "corners": 7,
            "red_cards": 0,
            "odds": {
                "over_0_5_ht": None,
                "over_1_5_ft": 1.52,
                "over_2_5_ft": 2.05
            }
        }
    ]
    return mock_data


# =========================================================
# VALIDAÇÃO DE ODD
# =========================================================

def is_valid_odd(odd, odd_range):
    if odd is None:
        return False
    odd = safe_float(odd, 0)
    return odd_range[0] <= odd <= odd_range[1]


# =========================================================
# FILTROS DE QUALIDADE
# =========================================================

def basic_match_filters(match):
    minute = safe_int(match.get("minute"))
    red_cards = safe_int(match.get("red_cards"))
    league = match.get("league", "")

    if minute < MIN_MINUTE_TO_SCAN or minute > MAX_MINUTE_TO_SCAN:
        return False

    if not is_allowed_league(league):
        return False

    if red_cards > 0:
        return False

    return True


def pressure_score(match):
    """
    Score simples para medir pressão ofensiva.
    """
    shots_total = safe_int(match.get("shots_total"))
    shots_on_target = safe_int(match.get("shots_on_target"))
    dangerous_attacks = safe_int(match.get("dangerous_attacks"))
    attacks = safe_int(match.get("attacks"))
    corners = safe_int(match.get("corners"))

    score = 0
    score += shots_total * 1.2
    score += shots_on_target * 2.0
    score += dangerous_attacks * 0.15
    score += attacks * 0.04
    score += corners * 1.1

    return round(score, 2)


def qualifies_over05_ht(match):
    """
    Over 0.5 HT:
    Melhor entre 18' e 38', placar 0x0,
    com pressão ofensiva real.
    """
    if not ENABLE_OVER05_HT:
        return False, "Mercado desligado"

    minute = safe_int(match.get("minute"))
    period = match.get("period")
    home_goals = safe_int(match.get("home_goals"))
    away_goals = safe_int(match.get("away_goals"))
    shots_total = safe_int(match.get("shots_total"))
    shots_on_target = safe_int(match.get("shots_on_target"))
    dangerous_attacks = safe_int(match.get("dangerous_attacks"))
    odd = match.get("odds", {}).get("over_0_5_ht")

    if period != "1H":
        return False, "Não está no 1º tempo"

    if minute < 18 or minute > 38:
        return False, "Minuto fora da faixa"

    if home_goals + away_goals != 0:
        return False, "Jogo não está 0x0"

    if shots_total < 7:
        return False, "Poucas finalizações"

    if shots_on_target < 2:
        return False, "Poucos chutes no gol"

    if REQUIRE_MIN_DANGEROUS_ATTACKS and dangerous_attacks < 28:
        return False, "Poucos ataques perigosos"

    if not is_valid_odd(odd, ODD_RANGE_OVER05_HT):
        return False, "Odd fora da faixa"

    if pressure_score(match) < 18:
        return False, "Pressão baixa"

    return True, "OK"


def qualifies_over15_ft(match):
    """
    Over 1.5 FT:
    Bom quando:
    - 0x0 entre 25' e 65' com forte pressão
    - ou 1x0 / 0x1 entre 35' e 75'
    """
    if not ENABLE_OVER15_FT:
        return False, "Mercado desligado"

    minute = safe_int(match.get("minute"))
    home_goals = safe_int(match.get("home_goals"))
    away_goals = safe_int(match.get("away_goals"))
    total_goals = home_goals + away_goals
    shots_total = safe_int(match.get("shots_total"))
    shots_on_target = safe_int(match.get("shots_on_target"))
    dangerous_attacks = safe_int(match.get("dangerous_attacks"))
    odd = match.get("odds", {}).get("over_1_5_ft")

    if total_goals == 0:
        if minute < 25 or minute > 65:
            return False, "0x0 fora da faixa"
        if shots_total < 10:
            return False, "Poucas finalizações no 0x0"
        if shots_on_target < 3:
            return False, "Poucos chutes no gol no 0x0"
        if REQUIRE_MIN_DANGEROUS_ATTACKS and dangerous_attacks < 35:
            return False, "Ataques perigosos baixos no 0x0"
        if pressure_score(match) < 24:
            return False, "Pressão insuficiente no 0x0"

    elif total_goals == 1:
        if minute < 35 or minute > 75:
            return False, "1 gol fora da faixa"
        if shots_total < 8:
            return False, "Poucas finalizações com 1 gol"
        if shots_on_target < 3:
            return False, "Poucos chutes no gol com 1 gol"
        if REQUIRE_MIN_DANGEROUS_ATTACKS and dangerous_attacks < 26:
            return False, "Ataques perigosos baixos com 1 gol"
        if pressure_score(match) < 20:
            return False, "Pressão insuficiente com 1 gol"
    else:
        return False, "Jogo já tem 2 ou mais gols"

    if not is_valid_odd(odd, ODD_RANGE_OVER15_FT):
        return False, "Odd fora da faixa"

    return True, "OK"


def qualifies_over25_ft(match):
    """
    Over 2.5 FT:
    Usado só em jogos mais vivos.
    """
    if not ENABLE_OVER25_FT:
        return False, "Mercado desligado"

    minute = safe_int(match.get("minute"))
    home_goals = safe_int(match.get("home_goals"))
    away_goals = safe_int(match.get("away_goals"))
    total_goals = home_goals + away_goals
    shots_total = safe_int(match.get("shots_total"))
    shots_on_target = safe_int(match.get("shots_on_target"))
    dangerous_attacks = safe_int(match.get("dangerous_attacks"))
    odd = match.get("odds", {}).get("over_2_5_ft")

    if total_goals == 1:
        if minute < 35 or minute > 68:
            return False, "1 gol fora da faixa"
        if shots_total < 11:
            return False, "Poucas finalizações"
        if shots_on_target < 4:
            return False, "Poucos chutes no gol"
        if REQUIRE_MIN_DANGEROUS_ATTACKS and dangerous_attacks < 40:
            return False, "Poucos ataques perigosos"
        if pressure_score(match) < 27:
            return False, "Pressão insuficiente"

    elif total_goals == 2:
        if minute < 40 or minute > 72:
            return False, "2 gols fora da faixa"
        if shots_total < 10:
            return False, "Poucas finalizações com 2 gols"
        if shots_on_target < 4:
            return False, "Poucos chutes no gol com 2 gols"
        if REQUIRE_MIN_DANGEROUS_ATTACKS and dangerous_attacks < 32:
            return False, "Poucos ataques perigosos com 2 gols"
        if pressure_score(match) < 22:
            return False, "Pressão insuficiente com 2 gols"
    else:
        return False, "Jogo não está no cenário ideal"

    if not is_valid_odd(odd, ODD_RANGE_OVER25_FT):
        return False, "Odd fora da faixa"

    return True, "OK"


# =========================================================
# MENSAGEM
# =========================================================

def build_signal_message(match, market_name, odd, rationale):
    score = pressure_score(match)
    msg = (
        f"🔥 <b>SINAL AO VIVO</b>\n\n"
        f"🏆 <b>Liga:</b> {match['league']}\n"
        f"⚽ <b>Jogo:</b> {format_match_header(match)}\n"
        f"⏱ <b>Minuto:</b> {match['minute']}' ({match['period']})\n"
        f"📊 <b>Placar:</b> {current_score_str(match)}\n\n"
        f"🎯 <b>Mercado:</b> {market_name}\n"
        f"💸 <b>Odd validada:</b> {odd:.2f}\n\n"
        f"📈 <b>Stats:</b>\n"
        f"• Finalizações: {match['shots_total']}\n"
        f"• No gol: {match['shots_on_target']}\n"
        f"• Ataques perigosos: {match['dangerous_attacks']}\n"
        f"• Escanteios: {match['corners']}\n"
        f"• Score de pressão: {score}\n\n"
        f"🧠 <b>Leitura:</b> {rationale}\n"
        f"⚠️ Gestão: stake fixa e sem gale."
    )
    return msg


# =========================================================
# MOTOR
# =========================================================

def analyze_and_send():
    daily_count = get_daily_signal_count()
    if daily_count >= MAX_SIGNALS_PER_DAY:
        print("[INFO] Limite diário já atingido.")
        return

    try:
        matches = fetch_live_matches()
    except Exception as e:
        print(f"[ERRO FETCH] {e}")
        return

    if not matches:
        print("[INFO] Nenhum jogo ao vivo retornado.")
        return

    candidates = []

    for match in matches:
        if not basic_match_filters(match):
            continue

        match_id = str(match.get("match_id"))

        # OVER 0.5 HT
        ok, reason = qualifies_over05_ht(match)
        if ok and not was_signal_sent_recently(match_id, "OVER_0_5_HT", SIGNAL_COOLDOWN_MINUTES):
            odd = safe_float(match["odds"].get("over_0_5_ht"))
            candidates.append({
                "priority": pressure_score(match) + 4,
                "market_key": "OVER_0_5_HT",
                "market_name": "Mais de 0.5 gol no 1º tempo",
                "odd": odd,
                "match": match,
                "reason": "0x0 com pressão ofensiva e odd dentro da faixa."
            })

        # OVER 1.5 FT
        ok, reason = qualifies_over15_ft(match)
        if ok and not was_signal_sent_recently(match_id, "OVER_1_5_FT", SIGNAL_COOLDOWN_MINUTES):
            odd = safe_float(match["odds"].get("over_1_5_ft"))
            candidates.append({
                "priority": pressure_score(match) + 2,
                "market_key": "OVER_1_5_FT",
                "market_name": "Mais de 1.5 gols no jogo",
                "odd": odd,
                "match": match,
                "reason": "Cenário de jogo com boa chance de sair mais gol."
            })

        # OVER 2.5 FT
        ok, reason = qualifies_over25_ft(match)
        if ok and not was_signal_sent_recently(match_id, "OVER_2_5_FT", SIGNAL_COOLDOWN_MINUTES):
            odd = safe_float(match["odds"].get("over_2_5_ft"))
            candidates.append({
                "priority": pressure_score(match),
                "market_key": "OVER_2_5_FT",
                "market_name": "Mais de 2.5 gols no jogo",
                "odd": odd,
                "match": match,
                "reason": "Jogo vivo, com volume e odd de valor."
            })

    if not candidates:
        print("[INFO] Nenhum candidato qualificado.")
        return

    # Ordena pelo melhor score
    candidates.sort(key=lambda x: x["priority"], reverse=True)

    # Envia no máximo 2 por varredura
    sent_now = 0
    for candidate in candidates:
        if get_daily_signal_count() >= MAX_SIGNALS_PER_DAY:
            break

        match = candidate["match"]
        match_id = str(match["match_id"])

        # Validação final de odd antes do envio
        market_key = candidate["market_key"]
        odd = candidate["odd"]

        if odd <= 1.0:
            continue

        message = build_signal_message(
            match=match,
            market_name=candidate["market_name"],
            odd=odd,
            rationale=candidate["reason"]
        )

        send_telegram_message(message)
        register_signal(match_id, market_key)
        increment_daily_signal_count()

        sent_now += 1
        print(f"[ENVIADO] {candidate['market_name']} | {format_match_header(match)} | odd {odd:.2f}")

        if sent_now >= 2:
            break


# =========================================================
# LOOP
# =========================================================

def main():
    init_db()
    send_telegram_message("✅ Bot de alertas iniciado com sucesso.")
    print("[BOT] Iniciado.")

    while True:
        try:
            analyze_and_send()
        except Exception as e:
            print(f"[ERRO LOOP] {e}")

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
