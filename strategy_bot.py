import os, logging, requests, asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

def get_moex(ticker):
    start = (datetime.today() - timedelta(weeks=12)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/history/engines/stock/markets/index/"
           f"boards/SNDX/securities/{ticker}.json"
           f"?from={start}&iss.meta=off&history.columns=TRADEDATE,CLOSE")
    try:
        rows = requests.get(url, timeout=10).json()["history"]["data"]
        return [x[1] for x in rows if x[1]]
    except:
        return []

def get_usdrub():
    start = (datetime.today() - timedelta(weeks=12)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/history/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?from={start}&iss.meta=off&history.columns=TRADEDATE,CLOSE")
    try:
        rows = requests.get(url, timeout=10).json()["history"]["data"]
        return [x[1] for x in rows if x[1]]
    except:
        return []

def mom(prices, days):
    if len(prices) < days+1: return 0.0
    return (prices[-1]/prices[-days-1]-1)*100

def calc_signals():
    imoex = get_moex("IMOEX")
    rugbi = get_moex("RGBITR")
    usd = get_usdrub()
    ri4 = mom(imoex, 20)
    rr4 = mom(rugbi, 20)
    ru4 = mom(usd, 20
