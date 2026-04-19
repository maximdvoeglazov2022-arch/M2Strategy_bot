import os, json, logging, requests
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

def get_moex(ticker, weeks=8):
    end = datetime.today()
    start = end - timedelta(weeks=weeks+4)
    url = (f"https://iss.moex.com/iss/history/engines/stock/markets/index/"
           f"boards/SNDX/securities/{ticker}.json"
           f"?from={start.strftime('%Y-%m-%d')}&iss.meta=off"
           f"&history.columns=TRADEDATE,CLOSE")
    try:
        r = requests.get(url, timeout=10)
        rows = r.json().get("history", {}).get("data", [])
        return [x[1] for x in rows if x[1]]
    except:
        return []

def get_usdrub(weeks=8):
    end = datetime.today()
    start = end - timedelta(weeks=weeks+4)
    url = (f"https://iss.moex.com/iss/history/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?from={start.strftime('%Y-%m-%d')}&iss.meta=off"
           f"&history.columns=TRADEDATE,CLOSE")
    try:
        r = requests.get(url, timeout=10)
        rows = r.json().get("history", {}).get("data", [])
        return [x[1] for x in rows if x[1]]
    except:
        return []

def momentum(prices, weeks):
    w = weeks * 5
    if len(prices) < w+1: return 0
    return (prices[-1]/prices[-w-1]-1)*100

def calc_signals():
    imoex = get_moex("IMOEX")
    rugbi = get_moex("RGBITR")
    usd   = get_usdrub()

    r_imoex_4w = momentum(imoex, 4)
    r_rugbi_4w = momentum(rugbi, 4)
    r_usd_4w   = momentum(usd, 4)
    r_usd_1w   = momentum(usd, 1)
    r_rugbi_1w = momentum(rugbi, 1)

    sig_imoex = "IMOEX" if r_imoex_4w >= r_rugbi_4w else "RUGBI"
    sig_usd_4w = "USD" if r_usd_4w >= r_rugbi_4w else "RUGBI"
    sig_usd_1w = "USD" if r_usd_1w >= r_rugbi_1w else "RUGBI"
    sig_usd = "USD" if (sig_usd_4w == "USD" or sig_usd_1w == "USD") else "RUGBI"

    usd_price = usd[-1] if usd else 0

    return {
        "imoex": sig_imoex,
        "usd": sig_usd,
        "r_imoex": r_imoex_4w,
        "r_rugbi": r_rugbi_4w,
        "r_usd_4w": r_usd_4w,
        "r_usd_1w": r_usd_1w,
        "usd_price": usd_price,
    }

def format_report(s):
    ei = "🟢" if s["imoex"]=="IMOEX" else "🔴"
    eu = "🟢" if s["usd"]=="USD" else "🔴"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"📊 *Сигналы* — {now}\n\n"
        f"{ei} *IMOEX* — позиция: *{s['imoex']}*\n"
        f"   IMOEX 4нед: {s['r_imoex']:+.1f}%\n"
        f"   RUGBI 4нед: {s['r_rugbi']:+.1f}%\n\n"
        f"{eu} *USDRUB* — позиция: *{s['usd']}*\n"
        f"   USD/RUB: {s['usd_price']:.2f}\n"
        f"   USD 4нед: {s['r_usd_4w']:+.1f}% | 1нед: {s['r_usd_1w']:+.1f}%\n\n"
        f"_Не является инвест. рекомендацией_"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот сигналов запущен!\n\n"
        "/signal — сигналы прямо сейчас\n"
        "/help — описание стратегий"
    )

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Считаю...")
    try:
        s = await ctx.application.loop.run_in_executor(None, calc_signals)
        await update.message.reply_text(format_report(s), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Стратегии:*\n"
        "🔹 IMOEX vs RUGBI — 4-нед momentum\n"
        "🔹 USDRUB vs RUGBI — двойной сигнал 4нед+1нед\n\n"
        "Данные: MOEX ISS API",
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("help", cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
