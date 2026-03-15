import requests
import time
import os
from datetime import datetime

API_KEY = os.getenv("API_KEY")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT = os.getenv("TG_CHAT")

BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"

LEAGUES_ALLOWED = [
39,140,135,78,61,71,88,94,253,2,3
]

headers = {
"x-rapidapi-key": API_KEY,
"x-rapidapi-host": "api-football-v1.p.rapidapi.com"
}

games_checked = 0
games_valid = 0
combos_sent = 0
last_run = "-"


def send(msg):

    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"

    requests.post(url,data={
        "chat_id":TG_CHAT,
        "text":msg
    })


def api(path,params):

    url = BASE_URL + path

    r=requests.get(url,headers=headers,params=params)

    return r.json()


def get_today_matches():

    data=api("/fixtures",{
        "next":50
    })

    return data["response"]


def get_team_stats(team,league,season):

    data=api("/teams/statistics",{
        "team":team,
        "league":league,
        "season":season
    })

    return data["response"]


def good_match(match):

    global games_checked
    global games_valid

    games_checked += 1

    league_id=match["league"]["id"]

    if league_id not in LEAGUES_ALLOWED:
        return False

    home=match["teams"]["home"]["id"]
    away=match["teams"]["away"]["id"]
    season=match["league"]["season"]

    stats_home=get_team_stats(home,league_id,season)
    stats_away=get_team_stats(away,league_id,season)

    goals_home=stats_home["goals"]["for"]["average"]["home"]
    goals_away=stats_away["goals"]["for"]["average"]["away"]

    if float(goals_home) < 1.2:
        return False

    if float(goals_away) < 1.0:
        return False

    games_valid += 1

    return True


def build_combo():

    matches=get_today_matches()

    good=[]

    for m in matches:

        try:

            if good_match(m):

                good.append(m)

        except:

            pass

    if len(good) >= 3:

        return good[:3]

    if len(good) >= 2:

        return good[:2]

    return None


def format_combo(combo):

    msg="🔥 COMBO OVER 0.5\n\n"

    for m in combo:

        home=m["teams"]["home"]["name"]
        away=m["teams"]["away"]["name"]
        league=m["league"]["name"]

        msg+=f"{league}\n{home} x {away}\n\n"

    msg+="Odd estimada ~1.30\n\n"

    first=combo[0]

    home=first["teams"]["home"]["name"]
    away=first["teams"]["away"]["name"]

    search=f"https://www.bet365.com/#/AC/B1/C1/D13/E181225/F2/?search={home}%20{away}"

    msg+=f"🔎 Abrir Bet365:\n{search}"

    return msg


def check_commands():

    url=f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"

    r=requests.get(url).json()

    for u in r["result"]:

        try:

            chat=u["message"]["chat"]["id"]

            text=u["message"]["text"]

            if str(chat) != str(TG_CHAT):
                continue

            if text == "/status":

                msg=f"""
🤖 BOT ONLINE

Jogos analisados: {games_checked}
Jogos aprovados: {games_valid}
Combos enviados hoje: {combos_sent}
Última análise: {last_run}
"""

                send(msg)

        except:
            pass


def main():

    global combos_sent
    global last_run

    send("🤖 BOT OVER 0.5 iniciado")

    while True:

        try:

            combo=build_combo()

            if combo:

                msg=format_combo(combo)

                send(msg)

                combos_sent += 1

            last_run=datetime.now().strftime("%H:%M")

            check_commands()

        except Exception as e:

            send(f"erro {e}")

        time.sleep(1800)


main()
