# bot.py
# Bot Gols HT/FT (modo FREE) - API-Football (API-Sports) + Telegram
#
# ✅ Ajustado para evitar /fixtures?live=all (pode dar 403 no free)
# ✅ Usa /fixtures?date=YYYY-MM-DD e filtra jogos LIVE (1H/HT/2H)
# ✅ Economiza requests (plano FREE ~100/dia) com:
#    - polling mais lento
#    - checar estatísticas só de poucos jogos candidatos
#    - cache de stats por alguns minutos
#
# Comandos no Telegram:
#   status  -> mostra resumo do que está sendo escaneado
#   agora   -> força checagem imediata
#
# Variáveis de ambiente (Railway -> Variables):
#   API_FOOTBALL_KEY   = sua chave da API-Football
#   TELEGRAM_TOKEN     = token do bot do Telegram
#   TELEGRAM_CHAT_ID   = id numérico do seu chat
#
# requirements.txt:
#   requests

import os
import time
import csv
import requests
from datetime import datetime, date

# =========================
# ENV
# =========================
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://v3.football.api-sports.io"
CSV_PATH = "jogos.csv"

# =========================
# CONFIG (AJUSTE AQUI)
# =========================

# Ligas-alvo (filtro por palavras no nome da liga)
TARGET_LEAGUE_KEYWORDS = [
    "Brasileiro", "Serie A", "Premier League",
    "UEFA Champions League", "UEFA Europa League",
    "Copa do Brasil",
]

# Seu filtro de “pressão” (ajustável)
MIN_TRIGGER = int(os.getenv("MIN_TRIGGER", "25"))  # minuto mínimo no 1º tempo
SHOTS_MIN   = int(os.getenv("SHOTS_MIN", "8"))
SOT_MIN     = int(os.getenv("SOT_MIN", "2"))
CORNERS_MIN = int(os.getenv("CORNERS_MIN", "3"))
AVOID_RED   = os.getenv("AVOID_RED", "1").strip() != "0"

# Controle: não operar em mais de 3 jogos ao mesmo tempo
MAX_ACTIVE_GAMES = int(os.getenv("MAX_ACTIVE_GAMES", "3"))

# Polling do loop principal (p/ plano FREE 100/dia):
# - 15 min = 96 chamadas/dia só de fixtures (OK)
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "900"))  # padrão 15 min

# Quantos jogos no máximo vamos buscar stats por ciclo (cada stats = 1 request)
MAX_STATS_CHECK_PER_CYCLE = int(os.getenv("MAX_STATS_CHECK_PER_CYCLE", "2"))  # padrão 2

# Cache de estatísticas (para não consultar toda hora o mesmo jogo)
STATS_CACHE_TTL_SECONDS = int(os.getenv("STATS_CACHE_TTL_SECONDS", "480"))  # 8 min

ALERT_HT = os.getenv("ALERT_HT", "1").strip() != "0"
ALERT_FT_AT_HT = os.getenv("ALERT_FT_AT_HT", "1").strip() != "0"

# =========================
# STAKE (opcional)
# =========================
ODD_BASE = float(os.getenv("ODD_BASE", "1.30"))
UNIT = float(os.getenv("UNIT", "15"))

def stake_for_op(op: int) -> float:
    """
    Modelo simples:
      Op1 = 1U
      Op2 = 4U
      Op3/Op4 = zera prejuízo acumulado usando odd_base
    """
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

# =========================
# TELEGRAM
# =========================
def tg_send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram não configurado (faltando TELEGRAM_TOKEN/CHAT_ID).", flush=True)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=20)
        if r.status_code != 200:
            print("Telegram HTTP", r.status_code, r.text[:250], flush=True)
    except Exception as e:
        print("Telegram error:", e, flush=True)

def tg_get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 5}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def normalize_cmd(text: str) -> str:
    return (text or "").strip().lower()

def handle_commands(state):
    """
    state: dict com chaves:
      - last_update_id
      - last_summary
      - force_run
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        offset = (state.get("last_update_id", 0) + 1) if state.get("last_update_id") is not None else None
        data = tg_get_updates(offset=offset)

        for upd in data.get("result", []):
            state["last_update_id"] = upd.get("update_id", state.get("last_update_id"))

            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))

            # só responde no seu chat
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            txt = normalize_cmd(msg.get("text", ""))

            if txt in ("status", "/status"):
                last = state.get("last_summary") or "Ainda não tenho resumo (aguarde 1 ciclo)."
                tg_send("📡 STATUS:\n" + last)

            elif txt in ("agora", "/agora", "run", "/run"):
                state["force_run"] = True
                tg_send("✅ Ok! Vou checar agora.")
    except Exception as e:
        print("Erro lendo comandos:", e, flush=True)

# =========================
# API HELPERS
# =========================
def api_get(path: str, params: dict):
    if not API_FOOTBALL_KEY:
        raise RuntimeError("API_FOOTBALL_KEY não configurada.")
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    r = requests.get(f"{API_BASE}{path}", headers=headers, params=params, timeout=30)
    if r.status_code != 200:
        print("API ERROR:", r.status_code, r.text[:400], flush=True)
        r.raise_for_status()
    return r.json()

def is_target_league(league_name: str) -> bool:
    ln = (league_name or "").lower()
    return any(kw.lower() in ln for kw in TARGET_LEAGUE_KEYWORDS)

def minute_of(fx) -> int:
    m = fx["fixture"]["status"].get("elapsed")
    return int(m) if m is not None else 0

def status_short(fx) -> str:
    return fx["fixture"]["status"].get("short", "")

def parse_score(fx) -> tuple[int, int]:
    h = fx["goals"]["home"] or 0
    a = fx["goals"]["away"] or 0
    return int(h), int(a)

def fetch_fixtures_today():
    # ✅ substitui live=all por date=hoje (mais compatível no FREE)
    today = date.today().isoformat()
    data = api_get("/fixtures", {"date": today})
    return data.get("response", [])

def fetch_stats(fixture_id: int):
    data = api_get("/fixtures/statistics", {"fixture": fixture_id})
    return data.get("response", [])

def get_stat(stats, team_index, stat_name):
    if not stats or team_index >= len(stats):
        return 0
    items = stats[team_index].get("statistics", [])
    for it in items:
        if (it.get("type", "") or "").lower() == stat_name.lower():
            v = it.get("value")
            if v is None:
                return 0
            if isinstance(v, str):
                v = v.replace("%", "").strip()
            try:
                return int(float(v))
            except Exception:
                return 0
    return 0

# =========================
# RUNTIME STATE
# =========================
SENT_ALERTS = set()     # (fixture_id, kind) kind: "HT" / "FT"
ACTIVE_GAMES = set()    # fixture_id “ativos” (limite MAX_ACTIVE_GAMES)
STATS_CACHE = {}        # fixture_id -> (ts, parsed_stats_dict)

def get_cached_stats(fixture_id: int):
    entry = STATS_CACHE.get(fixture_id)
    if not entry:
        return None
    ts, payload = entry
    if (time.time() - ts) > STATS_CACHE_TTL_SECONDS:
        return None
    return payload

def set_cached_stats(fixture_id: int, payload: dict):
    STATS_CACHE[fixture_id] = (time.time(), payload)

def parse_stats(stats_response):
    shots = get_stat(stats_response, 0, "Total Shots") + get_stat(stats_response, 1, "Total Shots")
    sot = get_stat(stats_response, 0, "Shots on Goal") + get_stat(stats_response, 1, "Shots on Goal")
    corners = get_stat(stats_response, 0, "Corner Kicks") + get_stat(stats_response, 1, "Corner Kicks")
    red = get_stat(stats_response, 0, "Red Cards") + get_stat(stats_response, 1, "Red Cards")
    return {"shots": shots, "sot": sot, "corners": corners, "red": red}

# =========================
# MAIN
# =========================
def main():
    if not API_FOOTBALL_KEY:
        print("ERRO: API_FOOTBALL_KEY não configurada.", flush=True)
        tg_send("⚠️ ERRO: API_FOOTBALL_KEY não configurada no Railway.")
        return

    print("Bot iniciado ✅ (modo FREE)", flush=True)
    tg_send("✅ Bot Gols HT/FT ONLINE (modo FREE). Vou alertar 0-0 com pressão (Over 0,5 HT) e no intervalo 0-0 (Over 0,5 FT).")

    state = {"last_update_id": None, "last_summary": None, "force_run": False}

    while True:
        try:
            # comandos do Telegram (status/agora)
            handle_commands(state)

            fixtures = fetch_fixtures_today()

            # filtra só os jogos LIVE
            live = []
            for fx in fixtures:
                sh = status_short(fx)
                if sh in ("1H", "HT", "2H"):
                    live.append(fx)

            # candidatos (0-0 + liga alvo)
            candidates = []
            for fx in live:
                league_name = fx.get("league", {}).get("name", "")
                if not is_target_league(league_name):
                    continue
                h, a = parse_score(fx)
                if h != 0 or a != 0:
                    continue
                m = minute_of(fx)
                sh = status_short(fx)
                candidates.append((fx, m, sh, league_name))

            # prioriza jogos mais avançados
            candidates.sort(key=lambda x: x[1], reverse=True)

            # salva resumo para o comando "status"
            top5 = candidates[:5]
            lines = [f"LIVE={len(live)} | 0-0 alvo={len(candidates)} | ativos={len(ACTIVE_GAMES)}"]
            for fx, m, sh, league_name in top5:
                home = fx["teams"]["home"]["name"]
                away = fx["teams"]["away"]["name"]
                lines.append(f"- {m:02d}' {sh} | {league_name} | {home} x {away}")
            state["last_summary"] = "\n".join(lines)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] " + state["last_summary"].replace("\n", " | "), flush=True)

            rows_for_csv = []
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # limite de checagem de stats p/ economizar requests
            checked = 0

            for fx, m, sh, league_name in candidates:
                fixture_id = fx["fixture"]["id"]
                home = fx["teams"]["home"]["name"]
                away = fx["teams"]["away"]["name"]
                jogo = f"{home} x {away}"

                # Monta linha do CSV mesmo sem stats (pra debug/painel)
                ht_flag = "SIM" if sh == "HT" else "NÃO"
                rows_for_csv.append([jogo, m, "0-0", ht_flag, "", "", "", "", "", "", "", "", now_str])

                # Respeita limite de jogos ativos
                if fixture_id not in ACTIVE_GAMES and len(ACTIVE_GAMES) >= MAX_ACTIVE_GAMES:
                    continue

                # ALERTA FT no intervalo 0-0 (sem stats, economiza)
                if ALERT_FT_AT_HT and sh == "HT":
                    key = (fixture_id, "FT")
                    if key not in SENT_ALERTS:
                        ACTIVE_GAMES.add(fixture_id)
                        stake = stake_for_op(2)
                        tg_send(
                            f"🟡 APOSTAR O0,5 FT (HT 0-0)\n"
                            f"🏆 {league_name}\n"
                            f"⚽ {jogo}\n"
                            f"⏱ HT | 0-0\n"
                            f"💰 Op2 Stake R${stake:.2f}"
                        )
                        SENT_ALERTS.add(key)
                    continue

                # ALERTA HT exige stats (pressão)
                if not (ALERT_HT and sh == "1H" and m >= MIN_TRIGGER):
                    continue

                # economiza: só checar stats de poucos jogos por ciclo
                if checked >= MAX_STATS_CHECK_PER_CYCLE:
                    continue

                cached = get_cached_stats(fixture_id)
                if cached is None:
                    stats_raw = fetch_stats(fixture_id)
                    parsed = parse_stats(stats_raw)
                    set_cached_stats(fixture_id, parsed)
                    checked += 1
                else:
                    parsed = cached

                shots = parsed["shots"]
                sot = parsed["sot"]
                corners = parsed["corners"]
                red = parsed["red"]
                red_ok = (red == 0) if AVOID_RED else True

                passes = (shots >= SHOTS_MIN and sot >= SOT_MIN and corners >= CORNERS_MIN and red_ok)

                if passes:
                    key = (fixture_id, "HT")
                    if key not in SENT_ALERTS:
                        ACTIVE_GAMES.add(fixture_id)
                        stake = stake_for_op(1)
                        red_str = "SIM" if red > 0 else "NÃO"
                        tg_send(
                            f"🟢 APOSTAR O0,5 HT\n"
                            f"🏆 {league_name}\n"
                            f"⚽ {jogo}\n"
                            f"⏱ {m}' | 0-0\n"
                            f"📊 Chutes {shots} | No alvo {sot} | Cantos {corners} | Vermelho {red_str}\n"
                            f"💰 Op1 Stake R${stake:.2f}"
                        )
                        SENT_ALERTS.add(key)

                time.sleep(2)

            # escreve CSV (opcional)
            try:
                with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["jogo","min","placar","ht","vermelho","chutes","no_alvo","escanteios","ataq_perig","odd_ht","odd_ft","obs","atualizado_em"])
                    for row in rows_for_csv:
                        while len(row) < 13:
                            row.append("")
                        w.writerow(row)
            except Exception as e:
                print("Erro ao escrever CSV:", e, flush=True)

        except Exception as e:
            print("Erro no loop:", repr(e), flush=True)
            tg_send(f"⚠️ Erro no bot: {e}")

        # Se pediu "agora", dorme pouco e roda de novo.
        # Senão, respeita POLL_SECONDS.
        if state.get("force_run"):
            state["force_run"] = False
            time.sleep(3)
        else:
            time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
