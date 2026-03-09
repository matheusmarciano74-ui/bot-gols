import os
import requests
import time

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = str(os.getenv("TELEGRAM_CHAT_ID"))

offset=None

def send(msg):

    url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    requests.post(url,data={
        "chat_id":CHAT,
        "text":msg
    })

def updates(offset=None):

    url=f"https://api.telegram.org/bot{TOKEN}/getUpdates"

    params={"timeout":5}

    if offset:
        params["offset"]=offset

    return requests.get(url,params=params).json()

send("BOT TESTE ONLINE")

while True:

    r=updates(offset)

    if r.get("result"):

        for u in r["result"]:

            offset=u["update_id"]+1

            msg=u.get("message")

            if not msg:
                continue

            text=msg.get("text","")

            if text:

                send(f"recebi: {text}")

    time.sleep(2)
