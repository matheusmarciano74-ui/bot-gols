# ============================================
# BOT APOSTAS PRE-JOGO
# Versão: v1.3
# Autor: Matheus + ChatGPT
# --------------------------------------------
# v1.0 - estrutura inicial
# v1.1 - mensagem "BOT APOSTAS INICIADO"
# v1.2 - logs, contador, estabilidade
# v1.3 - Telegram por variables do Railway
# ============================================

import os
import time
import traceback
from datetime import datetime

import requests

# =========================================================
# CONFIGURAÇÕES
# =========================================================

VERSAO = "v1.3"

ODD_MIN = 1.80
STAKE_FIXA = 2.00
INTERVALO_BUSCA_SEGUNDOS = 30
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot_apostas.log")

MOSTRAR_JOGOS_REPROVADOS = False
MAX_ERROS_SEGUIDOS = 20

# =========================================================
# TELEGRAM - lê vários nomes possíveis das variables
# =========================================================

def get_env(*names):
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""

TELEGRAM_BOT_TOKEN = get_env(
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN",
    "BOT_TOKEN",
    "TOKEN"
)

TELEGRAM_CHAT_ID = get_env(
    "TELEGRAM_CHAT_ID",
    "CHAT_ID"
)

TELEGRAM_ATIVADO = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

# =========================================================
# ESTADO GLOBAL
# =========================================================

stats = {
    "inicio": datetime.now(),
    "ciclos": 0,
    "jogos_analisados": 0,
    "oportunidades": 0,
    "erros": 0,
    "erros_seguidos": 0,
    "ultimo_ciclo": None,
    "ultima_oportunidade": None,
    "ultimo_heartbeat": None,
}

# =========================================================
# UTILITÁRIOS
# =========================================================

def garantir_pasta_logs():
    os.makedirs(LOG_DIR, exist_ok=True)


def agora_str():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def log(msg, nivel="INFO", salvar=True):
    linha = f"[{agora_str()}] [{nivel}] {msg}"
    print(linha, flush=True)

    if salvar:
        garantir_pasta_logs()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(linha + "\n")


def banner_inicio():
    print("\n" + "=" * 60)
    print(" BOT APOSTAS INICIADO ")
    print(f" Versão: {VERSAO}")
    print(f" Início: {agora_str()}")
    print("=" * 60 + "\n")


def formatar_tempo_execucao():
    delta = datetime.now() - stats["inicio"]
    total_seg = int(delta.total_seconds())

    horas = total_seg // 3600
    minutos = (total_seg % 3600) // 60
    segundos = total_seg % 60

    return f"{horas:02d}:{minutos:02d}:{segundos:02d}"


def mostrar_status_resumido():
    uptime = formatar_tempo_execucao()
    print("-" * 60)
    print(f"STATUS BOT ONLINE | uptime={uptime}")
    print(f"Ciclos: {stats['ciclos']}")
    print(f"Jogos analisados: {stats['jogos_analisados']}")
    print(f"Oportunidades: {stats['oportunidades']}")
    print(f"Erros totais: {stats['erros']}")
    print(f"Erros seguidos: {stats['erros_seguidos']}")
    print("-" * 60)


def salvar_erro_detalhado(exc):
    garantir_pasta_logs()
    erro_file = os.path.join(LOG_DIR, "bot_apostas_error.log")

    with open(erro_file, "a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(f"[{agora_str()}] EXCEÇÃO CAPTURADA\n")
        f.write(str(exc) + "\n")
        f.write(traceback.format_exc())
        f.write("\n")


# =========================================================
# TELEGRAM
# =========================================================

def enviar_telegram(msg):
    if not TELEGRAM_ATIVADO:
        log("Telegram não configurado nas variables.", "WARN")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg
    }

    try:
        r = requests.post(url, data=payload, timeout=20)
        r.raise_for_status()
        data = r.json()

        if not data.get("ok"):
            log(f"Resposta Telegram inválida: {data}", "ERRO")
            return False

        log("Mensagem enviada ao Telegram.", "OK")
        return True

    except Exception as e:
        log(f"Erro ao enviar Telegram: {e}", "ERRO")
        salvar_erro_detalhado(e)
        return False


def enviar_msg_inicio():
    enviar_telegram(
        f"🤖 BOT APOSTAS INICIADO\n"
        f"Versão: {VERSAO}\n"
        f"Início: {agora_str()}\n"
        f"ODD_MIN: {ODD_MIN}\n"
        f"STAKE: R${STAKE_FIXA:.2f}\n"
        f"INTERVALO: {INTERVALO_BUSCA_SEGUNDOS}s"
    )


def enviar_heartbeat():
    agora = datetime.now()

    if stats["ultimo_heartbeat"] is None:
        precisa = True
    else:
        diff = (agora - stats["ultimo_heartbeat"]).total_seconds()
        precisa = diff >= 300

    if precisa:
        ok = enviar_telegram(
            f"💓 BOT ONLINE\n"
            f"Versão: {VERSAO}\n"
            f"Uptime: {formatar_tempo_execucao()}\n"
            f"Ciclos: {stats['ciclos']}\n"
            f"Jogos analisados: {stats['jogos_analisados']}\n"
            f"Oportunidades: {stats['oportunidades']}\n"
            f"Erros: {stats['erros']}"
        )
        if ok:
            stats["ultimo_heartbeat"] = agora


# =========================================================
# FONTE DE DADOS (MOCK)
# =========================================================

def buscar_jogos():
    segundo = int(time.time()) % 5

    base = [
        {
            "id": "JOGO001",
            "time1": "Time A",
            "time2": "Time B",
            "odd": 1.72 + (segundo * 0.01),
            "mercado": "Pré-jogo",
            "origem": "SIMULADO"
        },
        {
            "id": "JOGO002",
            "time1": "Time C",
            "time2": "Time D",
            "odd": 1.88 + (segundo * 0.01),
            "mercado": "Pré-jogo",
            "origem": "SIMULADO"
        },
        {
            "id": "JOGO003",
            "time1": "Time E",
            "time2": "Time F",
            "odd": 2.03 + (segundo * 0.01),
            "mercado": "Pré-jogo",
            "origem": "SIMULADO"
        },
        {
            "id": "JOGO004",
            "time1": "Time G",
            "time2": "Time H",
            "odd": 1.54 + (segundo * 0.01),
            "mercado": "Pré-jogo",
            "origem": "SIMULADO"
        },
    ]

    return base


# =========================================================
# REGRAS DE ANÁLISE
# =========================================================

def jogo_aprovado(jogo):
    odd = float(jogo.get("odd", 0))

    if odd >= ODD_MIN:
        return True, "Odd aprovada"

    return False, "Odd abaixo do mínimo"


def analisar_jogos(jogos):
    aprovados = []
    reprovados = []

    for jogo in jogos:
        ok, motivo = jogo_aprovado(jogo)

        if ok:
            aprovados.append(jogo)
        else:
            reprovados.append((jogo, motivo))

    return aprovados, reprovados


# =========================================================
# AÇÕES
# =========================================================

def exibir_oportunidade(jogo):
    stats["oportunidades"] += 1
    stats["ultima_oportunidade"] = datetime.now()

    log("APOSTA ENCONTRADA", "OK")
    log(f"Jogo: {jogo['time1']} x {jogo['time2']}", "OK")
    log(f"Odd: {jogo['odd']}", "OK")
    log(f"Stake: R${STAKE_FIXA:.2f}", "OK")
    log(f"Mercado: {jogo.get('mercado', 'N/D')}", "OK")
    log(f"Origem: {jogo.get('origem', 'N/D')}", "OK")

    enviar_telegram(
        f"🔥 APOSTA ENCONTRADA\n"
        f"Jogo: {jogo['time1']} x {jogo['time2']}\n"
        f"Odd: {jogo['odd']}\n"
        f"Stake: R${STAKE_FIXA:.2f}\n"
        f"Mercado: {jogo.get('mercado', 'N/D')}\n"
        f"Origem: {jogo.get('origem', 'N/D')}"
    )


def processar_ciclo():
    stats["ciclos"] += 1
    stats["ultimo_ciclo"] = datetime.now()

    log(f"Iniciando ciclo #{stats['ciclos']}")

    jogos = buscar_jogos()

    if not isinstance(jogos, list):
        raise TypeError("A função buscar_jogos() não retornou uma lista.")

    total = len(jogos)
    stats["jogos_analisados"] += total

    log(f"Jogos recebidos: {total}")

    aprovados, reprovados = analisar_jogos(jogos)

    if aprovados:
        for jogo in aprovados:
            exibir_oportunidade(jogo)
    else:
        log("Nenhuma oportunidade encontrada neste ciclo.")

    if MOSTRAR_JOGOS_REPROVADOS and reprovados:
        for jogo, motivo in reprovados:
            log(
                f"Reprovado: {jogo['time1']} x {jogo['time2']} | "
                f"Odd={jogo['odd']} | Motivo={motivo}",
                "DEBUG"
            )

    log(
        f"Resumo ciclo #{stats['ciclos']}: "
        f"aprovados={len(aprovados)} | reprovados={len(reprovados)}"
    )


# =========================================================
# LOOP PRINCIPAL
# =========================================================

def executar_bot():
    banner_inicio()
    log("BOT APOSTAS INICIADO")
    log(f"Versão carregada: {VERSAO}")
    log(f"ODD_MIN={ODD_MIN}")
    log(f"STAKE_FIXA=R${STAKE_FIXA:.2f}")
    log(f"INTERVALO_BUSCA_SEGUNDOS={INTERVALO_BUSCA_SEGUNDOS}")
    log(f"Arquivo de log: {LOG_FILE}")
    log(f"Telegram ativo: {TELEGRAM_ATIVADO}")

    enviar_msg_inicio()

    while True:
        try:
            mostrar_status_resumido()
            processar_ciclo()
            enviar_heartbeat()

            stats["erros_seguidos"] = 0

        except KeyboardInterrupt:
            log("Bot encerrado manualmente pelo usuário.", "WARN")
            enviar_telegram("🛑 Bot encerrado manualmente.")
            break

        except Exception as e:
            stats["erros"] += 1
            stats["erros_seguidos"] += 1

            log(f"ERRO NO CICLO: {e}", "ERRO")
            salvar_erro_detalhado(e)
            enviar_telegram(f"❌ ERRO NO BOT: {e}")

            if stats["erros_seguidos"] >= MAX_ERROS_SEGUIDOS:
                log(
                    f"Limite de erros seguidos atingido ({MAX_ERROS_SEGUIDOS}). "
                    f"Encerrando para segurança.",
                    "CRITICO"
                )
                enviar_telegram("🚨 Bot encerrado por limite de erros seguidos.")
                break

        log(f"Aguardando {INTERVALO_BUSCA_SEGUNDOS}s para próximo ciclo...")
        time.sleep(INTERVALO_BUSCA_SEGUNDOS)


if __name__ == "__main__":
    executar_bot()
