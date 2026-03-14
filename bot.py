import os
import time
import requests

API_KEY = os.getenv("API_FOOTBALL_KEY")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")

API = "https://v3.football.api-sports.io"

POLL = 180
STATUS_INTERVAL = 1800  # 30 min
MAX_ALERTS = 5

TARGET_LEAGUES = [
    ("England", "Premier League"),
    ("Germany", "Bundesliga"),
    ("Italy", "Serie A"),
    ("Spain", "La Liga"),
    ("France", "Ligue 1"),
    ("Brazil", "Serie A"),
    ("Brazil", "Copa do Brasil"),
    ("USA", "Major League Soccer"),
    ("Netherlands", "Eredivisie"),
    ("Belgium", "First Division A"),
    ("Austria", "Bundesliga"),
    ("Switzerland", "Super League"),
    ("Denmark", "Superliga"),
    ("Norway", "Eliteserien"),
    ("Sweden", "Allsvenskan"),
    ("Turkey", "Süper Lig"),
    ("Argentina", "Liga Profesional"),
]

alerted = set()
alerts_hour = []
last_status_time = 0

def tg(msg):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TG_CHAT, "text": msg}, timeout=20)

def api(path, params):
