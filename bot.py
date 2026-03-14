import os
import time
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"

POLL = 180
STATUS_INTERVAL = 1800
MAX_ALERTS = 5

TARGET_LEAGUES = [
    ("England","Premier League"),
    ("Germany","Bundesliga"),
    ("Italy","Serie A"),
    ("Spain","La Liga"),
    ("France","Ligue 1"),
    ("Brazil","Serie A"),
    ("Brazil","Copa do Brasil"),
    ("USA","Major League Soccer"),
    ("Netherlands","Eredivisie"),
    ("Belgium","First Division A"),
    ("Austria","Bundesliga"),
    ("Switzerland","Super League"),
    ("Denmark","Superliga"),
    ("Norway","Eliteserien"),
    ("Sweden","Allsvenskan"),
    ("Turkey","Süper Lig"),
    ("Argentina","Liga Profesional")
]

alerted=set()
alerts_hour=[]
last_status=0


def tg(msg):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url,data={"chat_id":TG_CHAT,"text":msg})


def api(path,params):
    headers={"x-apisports-key":API_KEY}
    r=requests.get(API+path,headers=headers,params=params)
    return r.json()["response"]


def league_ok(country,league):
    for c,l in TARGET_LEAGUES:
        if country==c and league==l:
            return True
    return False


def get_stats(fid):

    stats=api("/fixtures/statistics",{"fixture":fid})

    shots=0
    sot=0
    corners=0
    danger=0

    for team in stats:
        for s in team["statistics"]:

            if s["type"]=="Total Shots":
                shots+=s["value"] or 0

            if s["type"]=="Shots on Goal":
                sot+=s["value"] or 0

            if s["type"]=="Corner Kicks":
                corners+=s["value"] or 0

            if s["type"]=="Dangerous Attacks":
                danger+=s["value"] or 0

    return shots,sot,corners,danger


def bet365_link(home,away):
    return f"https://www.google.com/search?q=bet365+{home}+vs+{away}"


def can_alert():

    now=time.time()

    alerts_hour[:]=[t for t in alerts_hour if now-t<3600]

    return len(alerts_hour)<MAX_ALERTS


def main():

    global last_status

    tg("🤖 BOT OVER INICIADO")

    while True:

        try:

            games=api("/fixtures",{"live":"all"})

            total_live=len(games)
            valid_league=0
            alerts_sent=0

            for g in games:

                league=g["league"]["name"]
                country=g["league"]["country"]

                if not league_ok(country,league):
                    continue

                valid_league+=1

                if g["fixture"]["status"]["short"]!="1H":
                    continue

                minute=g["fixture"]["status"]["elapsed"] or 0

                home=g["teams"]["home"]["name"]
                away=g["teams"]["away"]["name"]

                g1=g["goals"]["home"] or 0
                g2=g["goals"]["away"] or 0

                score=f"{g1}-{g2}"

                fid=g["fixture"]["id"]

                if fid in alerted:
                    continue

                shots,sot,corners,danger=get_stats(fid)

                # HT
                if 18<=minute<=37 and score=="0-0":

                    if shots>=7 and sot>=2 and corners>=2 and danger>=20:

                        if can_alert():

                            link=bet365_link(home,away)

                            msg=f"""🔥 OVER HT

{country} {league}

{home} x {away}

⏱ {minute}'
⚽ {score}

📊 chutes {shots}
🎯 no gol {sot}
🚩 escanteios {corners}
⚡ ataques perigosos {danger}

➡ Over 0.5 HT

🔗 {link}
"""

                            tg(msg)

                            alerted.add(fid)
                            alerts_hour.append(time.time())
                            alerts_sent+=1


                # FT
                if 22<=minute<=65 and score in ["0-0","1-0","0-1"]:

                    if shots>=8 and sot>=2 and corners>=3 and danger>=22:

                        if can_alert():

                            link=bet365_link(home,away)

                            msg=f"""🔵 OVER FT

{country} {league}

{home} x {away}

⏱ {minute}'
⚽ {score}

📊 chutes {shots}
🎯 no gol {sot}
🚩 escanteios {corners}
⚡ ataques perigosos {danger}

➡ Over 1.5 FT

🔗 {link}
"""

                            tg(msg)

                            alerted.add(fid)
                            alerts_hour.append(time.time())
                            alerts_sent+=1


            now=time.time()

            if now-last_status>STATUS_INTERVAL:

                tg(f"""📡 BOT STATUS

Jogos ao vivo analisados: {total_live}
Jogos em ligas válidas: {valid_league}
Alertas enviados neste ciclo: {alerts_sent}
""")

                last_status=now


            time.sleep(POLL)

        except Exception as e:

            tg(f"erro {e}")

            time.sleep(60)


if __name__=="__main__":
    main()
