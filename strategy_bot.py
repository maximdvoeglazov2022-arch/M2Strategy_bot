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

def get_yahoo(ticker):
    end = datetime.today()
    start = end - timedelta(weeks=12)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1wk&period1={int(start.timestamp())}&period2={int(end.timestamp())}")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        data = requests.get(url, headers=headers, timeout=15).json()
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [x for x in closes if x is not None]
    except:
        return []

def mom(prices, days):
    if len(prices) < days+1: return 0.0
    return (prices[-1]/prices[-days-1]-1)*100

def calc_signals():
    imoex = get_moex("IMOEX")
    rugbi = get_moex("RGBITR")
    usd   = get_usdrub()
    spy   = get_yahoo("SPY")
    gld   = get_yahoo("GLD")

    ri4 = mom(imoex, 20)
    rr4 = mom(rugbi, 20)
    ru4 = mom(usd, 20)
    ru1 = mom(usd, 5)
    rr1 = mom(rugbi, 5)
    rs4 = mom(spy, 4)
    rg4 = mom(gld, 4)

    si  = "IMOEX" if ri4 >= rr4 else "RUGBI"
    su  = "USD"   if (ru4 >= rr4 or ru1 >= rr1) else "RUGBI"
    ss  = "SPY"   if rs4 > 0 else "CASH"
    sg  = "GLD"   if rg4 > 0 else "CASH"

    return {
        "si": si, "su": su, "ss": ss, "sg": sg,
        "up": usd[-1] if usd else 0,
        "sp": spy[-1] if spy else 0,
        "gp": gld[-1] if gld else 0,
    }

def make_report(s):
    ei = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["si"]=="IMOEX" else "⚪ ВНЕ ПОЗИЦИИ"
    eu = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["su"]=="USD"   else "⚪ ВНЕ ПОЗИЦИИ"
    es = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["ss"]=="SPY"   else "⚪ ВНЕ ПОЗИЦИИ"
    eg = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["sg"]=="GLD"   else "⚪ ВНЕ ПОЗИЦИИ"
    t  = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"📊 Сигналы — {t}\n\n"
        f"🇷🇺 IMOEX\n{ei}\n\n"
        f"💵 USDRUB\n{eu}\n"
        f"USD/RUB: {s['up']:.2f}\n\n"
        f"🇺🇸 SPY (S&P 500)\n{es}\n"
        f"SPY: ${s['sp']:.2f}\n\n"
        f"🥇 GLD (Золото)\n{eg}\n"
        f"GLD: ${s['gp']:.2f}"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот сигналов запущен!\n/signal — сигналы\n/help — помощь")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Считаю... (~30 сек)")
    try:
        s = await asyncio.get_event_loop().run_in_executor(None, calc_signals)
        await update.message.reply_text(make_report(s))
    except Exception as e:
        log.error(e)
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Стратегии:\n"
        "IMOEX vs RUGBI — 4нед momentum\n"
        "USDRUB vs RUGBI — 4нед+1нед\n"
        "SPY — 4нед momentum vs кэш\n"
        "GLD — 4нед momentum vs кэш\n\n"
        "Данные: MOEX ISS + Yahoo Finance")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("help",   cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
