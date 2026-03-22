def enviar_alerta(jogo):
    busca = f"{jogo['home']} x {jogo['away']} bet365"
    link = f"https://www.google.com/search?q={quote_plus(busca)}"

    msg = (
        f"🔥 SINAL AO VIVO\n\n"
        f"⚽ {jogo['home']} x {jogo['away']}\n"
        f"⏱ {jogo['minute']}'\n\n"
        f"🎯 Over 0.5 HT\n"
        f"💸 Odd: {jogo['odd']} ({jogo['book']})\n\n"
        f"📲 AÇÃO RÁPIDA:\n"
        f"1. Abra a bet365\n"
        f"2. Pesquise:\n"
        f"{jogo['home']} x {jogo['away']}\n"
        f"3. Entre em:\n"
        f"Mais de 0.5 gols (1º tempo)\n\n"
        f"🔎 ABRIR JOGO:\n{link}\n\n"
        f"⚠️ Stake: 2% banca"
    )

    send_telegram(msg)
