# bot_gols_ht_ft.py
# Python 3.10+
# pip install requests

import csv
import time
import os
import requests
from datetime import datetime

# =========================
# CONFIG (edite aqui)
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "COLE_SUA_KEY_AQUI")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "COLE_SEU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "COLE_SEU_CHAT_ID_AQUI")

# Competições-alvo (vamos filtrar por país/league name na resposta)
TARGET = [
    "Brazil", "England", "Italy",
]
# E nomes (porque “Copa do Brasil”, “UEFA Champions League”, “Europa League”)
TARGET_LEAGUE_KEYWORDS = [
    "Serie A", "Premier League", "Copa do Brasil",
    "UEFA Champions League", "UEFA Europa League", "Brasileiro"
]

# Sua estratégia / stakes (odd base 1.30)
ODD_BASE = 1.30
UNIT = 15.0

# Modelo: sair positivo na 2ª e zerar na 3ª (e 4ª zera se perder 3)
# Op1 = U
# Op2 = 4U
# Op3 = (Op1+Op2)/(odd-1)
# Op4 = (Op1+Op2+Op3)/(odd-1)
def stake_for_op(op: int) -> float:
    if op == 1:
        return round(UNIT, 2)
    if op == 2:
        return round(UNIT * 4, 2)
    op1 = stake_for_op(1)
    op2 = stake_for_op(2)
    if op == 3:
        return round((op1 + op2) / (ODD_BASE - 1), 2)
    if op == 4:
        op3 = stake_for_op(3)
        return round((op1 + op2 + op3) / (ODD_BASE - 1), 2)
    return 0.0

# Filtros de “pressão” (ajuste)
MIN_TRIGGER = 25
SHOTS_MIN = 8
SOT_MIN = 2
CORNERS_MIN = 3

# Intervalo do loop (segundos)
POLL_SECONDS = 70  # ~1x por minuto, pra respeitar rate limit

# Controle: evitar spam
SENT_ALERTS = set()   # (fixture_id, kind) kind: "HT" or "FT"
MAX_GAMES_TO_CHECK_STATS = 8  # economiza requisições

CSV_PATH = "jogos.csv"

API_BASE = "https://v3.football.api-sports.io"

def tg_send(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=15)
    except Exception:
        pass

def api_get(path: str, params: dict):
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    r = requests.get(f"{API_BASE}{path}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def is_target_league(country: str, league_name: str) -> bool:
    if country in TARGET:
        for kw in TARGET_LEAGUE_KEYWORDS:
            if kw.lower() in league_name.lower():
                return True
    # Champions/Europa às vezes vem com country = "World"
    for kw in TARGET_LEAGUE_KEYWORDS:
        if kw.lower() in league_name.lower():
            return True
    return False

def parse_score(fx) -> tuple[int,int]:
    # api-football: goals.home/goals.away (inteiros ou None)
    h = fx["goals"]["home"] or 0
    a = fx["goals"]["away"] or 0
    return int(h), int(a)

def minute_of(fx) -> int:
    # fixture.status.elapsed
    m = fx["fixture"]["status"].get("elapsed")
    return int(m) if m is not None else 0

def status_short(fx) -> str:
    return fx["fixture"]["status"].get("short", "")

def fetch_live_fixtures():
    # 1 request
    data = api_get("/fixtures", {"live": "all"})
    return data.get("response", [])

def fetch_stats(fixture_id: int):
    # 1 request
    data = api_get("/fixtures/statistics", {"fixture": fixture_id})
    return data.get("response", [])

def get_stat(stats, team_index, stat_name):
    # stats format: [ {team:..., statistics:[{type,value},...]} , ...]
    if not stats or team_index >= len(stats):
        return 0
    items = stats[team_index].get("statistics", [])
    for it in items:
        if it.get("type", "").lower() == stat_name.lower():
            v = it.get("value")
            if v is None:
                return 0
            # pode vir "12" ou 12 ou "55%"
            if isinstance(v, str):
                v = v.replace("%", "").strip()
            try:
                return int(float(v))
            except Exception:
                return 0
    return 0

def main():
    tg_send("✅ Bot Gols HT/FT iniciado (modo FREE). Vou alertar jogos 0-0 com pressão para Over 0,5 HT e, no intervalo 0-0, Over 0,5 FT.")

    while True:
        try:
            live = fetch_live_fixtures()

            # Filtra só as ligas que você quer
            candidates = []
            for fx in live:
                league = fx.get("league", {})
                country = league.get("country", "") or ""
                league_name = league.get("name", "") or ""
                if not is_target_league(country, league_name):
                    continue

                m = minute_of(fx)
                sh = status_short(fx)  # "1H", "HT", "2H" etc.
                h, a = parse_score(fx)
                if h == 0 and a == 0:
                    candidates.append((fx, m, sh, country, league_name))

            # Prioriza perto do intervalo / gatilho
            candidates.sort(key=lambda x: x[1], reverse=True)

            # Vamos checar stats de poucos jogos (economia de rate limit)
            to_check = candidates[:MAX_GAMES_TO_CHECK_STATS]

            rows_for_csv = []
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for fx, m, sh, country, league_name in to_check:
                fixture_id = fx["fixture"]["id"]
                home = fx["teams"]["home"]["name"]
                away = fx["teams"]["away"]["name"]

                # Puxa stats
                stats = fetch_stats(fixture_id)

                # Soma de chutes/no alvo/escanteios (dos dois times)
                shots = get_stat(stats, 0, "Total Shots") + get_stat(stats, 1, "Total Shots")
                sot   = get_stat(stats, 0, "Shots on Goal") + get_stat(stats, 1, "Shots on Goal")
                corners = get_stat(stats, 0, "Corner Kicks") + get_stat(stats, 1, "Corner Kicks")

                # (Opcional) cartões vermelhos: aqui pegamos de "Red Cards" se vier
                red = get_stat(stats, 0, "Red Cards") + get_stat(stats, 1, "Red Cards")
                red_str = "SIM" if red > 0 else "NÃO"

                jogo = f"{home} x {away}"
                placar = "0-0"
                ht_flag = "SIM" if sh == "HT" else "NÃO"

                # Decide operação e ação
                action = "AGUARDAR"
                op = 1

                passes = (m >= MIN_TRIGGER and shots >= SHOTS_MIN and sot >= SOT_MIN and corners >= CORNERS_MIN and red == 0)

                # ALERTA HT
                if sh == "1H" and passes:
                    action = "APOSTAR O0,5 HT"
                    op = 1
                    key = (fixture_id, "HT")
                    if key not in SENT_ALERTS:
                        stake = stake_for_op(1)
                        tg_send(
                            f"🟢 {action}\n"
                            f"🏆 {league_name}\n"
                            f"⚽ {jogo}\n"
                            f"⏱ {m}' | {placar}\n"
                            f"📊 Chutes {shots} | No alvo {sot} | Cantos {corners} | Vermelho {red_str}\n"
                            f"💰 Operação {op} | Stake R${stake:.2f}\n"
                            f"📌 Entrada: Over 0,5 1º Tempo"
                        )
                        SENT_ALERTS.add(key)

                # ALERTA FT no intervalo 0-0
                if sh == "HT":
                    action = "APOSTAR O0,5 FT"
                    op = 2  # você pode ajustar manualmente se estiver em op3/op4
                    key = (fixture_id, "FT")
                    if key not in SENT_ALERTS:
                        stake = stake_for_op(op)
                        tg_send(
                            f"🟡 {action}\n"
                            f"🏆 {league_name}\n"
                            f"⚽ {jogo}\n"
                            f"⏱ HT | {placar}\n"
                            f"📊 Chutes {shots} | No alvo {sot} | Cantos {corners} | Vermelho {red_str}\n"
                            f"💰 Operação {op} | Stake R${stake:.2f}\n"
                            f"📌 Entrada: Over 0,5 Jogo (FT)"
                        )
                        SENT_ALERTS.add(key)

                # CSV (odds você preenche no painel ou deixa vazio)
                rows_for_csv.append([
                    jogo, m, placar, ht_flag, red_str, shots, sot, corners, "", "", "", "", now_str
                ])

                # Respeita rate limit (dá uma respirada leve entre stats)
                time.sleep(3)

            # Escreve CSV pro Excel (IMPORT)
            with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["jogo","min","placar","ht","vermelho","chutes","no_alvo","escanteios","ataq_perig","odd_ht","odd_ft","obs","atualizado_em"])
                w.writerows(rows_for_csv)

        except Exception as e:
            tg_send(f"⚠️ Erro no bot: {e}")

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
