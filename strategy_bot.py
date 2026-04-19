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
    ru4 = mom(usd, 20)
    ru1 = mom(usd, 5)
    rr1 = mom(rugbi, 5)
    si = "IMOEX" if ri4 >= rr4 else "RUGBI"
    su = "USD" if (ru4 >= rr4 or ru1 >= rr1) else "RUGBI"
    return {"si": si, "su": su, "ri4": ri4, "rr4": rr4,
            "ru4": ru4, "ru1": ru1, "up": usd[-1] if usd else 0}

def make_report(s):
    ei = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["si"]=="IMOEX" else "⚪ ВНЕ ПОЗИЦИИ"
    eu = "🟢 ПОЗИЦИЯ ОТКРЫТА" if s["su"]=="USD" else "⚪ ВНЕ ПОЗИЦИИ"
    t = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"📊 Сигналы — {t}\n\n"
        f"IMOEX\n{ei}\n\n"
        f"USDRUB\n{eu}\n\n"
        f"USD/RUB: {s['up']:.2f}"
            f"Не является инвест. рекомендацией")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот сигналов запущен!\n/signal — сигналы\n/help — помощь")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Считаю...")
    try:
        s = await asyncio.get_event_loop().run_in_executor(None, calc_signals)
        await update.message.reply_text(make_report(s))
    except Exception as e:
        log.error(e)
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Стратегии:\nIMOEX vs RUGBI — 4нед momentum\n"
        "USDRUB vs RUGBI — 4нед+1нед\nДанные: MOEX ISS API")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("help", cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
