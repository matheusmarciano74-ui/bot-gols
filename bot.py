import os
import time
import requests
from datetime import date

API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"

POLL_SECONDS = 600

# LIGAS IMPORTANTES
TARGET_LEAGUES = [

    # INGLATERRA
    "Premier League",
    "Championship",
    "FA Cup",
    "League Cup",

    # FRANÇA
    "Ligue 1",
    "Ligue 2",
    "Coupe de France",

    # ALEMANHA
    "Bundesliga",
    "DFB Pokal",

    # ITÁLIA
    "Serie A",
    "Coppa Italia",

    # BRASIL
    "Serie A (Brazil)",
    "Serie B (Brazil)",
    "Copa do Brasil",

    # ARGENTINA
    "Liga Profesional Argentina",
    "Copa de la Liga Profesional",

    # EUROPA
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",

    # CONMEBOL
    "CONMEBOL Libertadores",
    "CONMEBOL Sudamericana"
]

def tg_send(msg):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url,data={"chat_id":TG_CHAT,"text":msg})

def api(path,params):
    headers={"x-apisports-key":API_KEY}
    r=requests.get(API+path,headers=headers,params=params)
    return r.json()["response"]

def get_live_games():

    today=date.today().isoformat()

    data=api("/fixtures",{"date":today})

    live=[]

    for f in data:

        status=f["fixture"]["status"]["short"]

        if status not in ["1H","2H","HT"]:
            continue

        league=f["league"]["name"]

        if league not in TARGET_LEAGUES:
            continue

        home=f["teams"]["home"]["name"]
        away=f["teams"]["away"]["name"]

        ghome=f["goals"]["home"] or 0
        gaway=f["goals"]["away"] or 0

        minute=f["fixture"]["status"]["elapsed"] or 0

        live.append({
            "id":f["fixture"]["id"],
            "league":league,
            "home":home,
            "away":away,
            "minute":minute,
            "score":f"{ghome}-{gaway}",
            "status":status
        })

    return live

def get_stats(fid):

    stats=api("/fixtures/statistics",{"fixture":fid})

    shots=0
    sot=0
    corners=0

    for t in stats:

        for s in t["statistics"]:

            if s["type"]=="Total Shots":
                shots+=s["value"] or 0

            if s["type"]=="Shots on Goal":
                sot+=s["value"] or 0

            if s["type"]=="Corner Kicks":
                corners+=s["value"] or 0

    return shots,sot,corners

def main():

    tg_send("✅ Bot gols iniciado")

    alerted=set()

    while True:

        try:

            games=get_live_games()

            candidates=[]

            for g in games:

                if g["score"]!="0-0":
                    continue

                if g["minute"]<25:
                    continue

                candidates.append(g)

            print("LIVE:",len(games),"cand:",len(candidates))

            for g in candidates[:3]:

                fid=g["id"]

                if fid in alerted:
                    continue

                shots,sot,corners=get_stats(fid)

                if shots>=8 and sot>=2 and corners>=3:

                    msg=f"""
⚽ POSSÍVEL GOL

🏆 {g["league"]}
{g["home"]} x {g["away"]}

⏱ {g["minute"]}' | 0-0

📊 Chutes {shots}
🎯 No alvo {sot}
🚩 Escanteios {corners}

➡ Over 0.5 HT
"""

                    tg_send(msg)

                    alerted.add(fid)

            time.sleep(POLL_SECONDS)

        except Exception as e:

            print(e)

            tg_send(f"Erro bot: {e}")

            time.sleep(60)

if __name__=="__main__":
    main()
