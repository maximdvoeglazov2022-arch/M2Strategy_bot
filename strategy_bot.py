import os, logging, requests, asyncio, json
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
STATE_FILE = "state.json"

def load_state():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

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

    si = "IMOEX" if ri4 >= rr4 else "CASH"
    su = "USD"   if (ru4 >= rr4 or ru1 >= rr1) else "CASH"
    ss = "SPY"   if rs4 > 0 else "CASH"
    sg = "GLD"   if rg4 > 0 else "CASH"

    return {
        "si": si, "su": su, "ss": ss, "sg": sg,
        "up": usd[-1]   if usd   else 0,
        "ip": imoex[-1] if imoex else 0,
        "sp": spy[-1]   if spy   else 0,
        "gp": gld[-1]   if gld   else 0,
    }

def pnl_str(current, entry, currency=""):
    if not entry or not current:
        return ""
    pct = (current/entry - 1)*100
    sign = "+" if pct >= 0 else ""
    return f"  Вход: {currency}{entry:.2f} | P&L: {sign}{pct:.1f}%"

def update_entries(s, state):
    now = datetime.now().strftime("%d.%m.%Y")
    for key, sig, price_key, label in [
        ("IMOEX", s["si"], "ip", "IMOEX"),
        ("USD",   s["su"], "up", "USD"),
        ("SPY",   s["ss"], "sp", "SPY"),
        ("GLD",   s["sg"], "gp", "GLD"),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        curr = sig
        if curr != "CASH" and prev == "CASH":
            state[f"{key}_entry"] = s[price_key]
            state[f"{key}_entry_date"] = now
            log.info(f"Новая позиция {key} по цене {s[price_key]}")
        if curr == "CASH":
            state.pop(f"{key}_entry", None)
            state.pop(f"{key}_entry_date", None)
        state[f"{key}_sig"] = curr
    save_state(state)

def make_report(s, state):
    t = datetime.now().strftime("%d.%m.%Y %H:%M")

    def block(name, sig, price, currency, key):
        if sig != "CASH":
            entry = state.get(f"{key}_entry")
            entry_date = state.get(f"{key}_entry_date", "")
            pl = pnl_str(price, entry, currency)
            date_str = f"  Дата входа: {entry_date}\n" if entry_date else ""
            return f"🟢 ПОЗИЦИЯ ОТКРЫТА\n{date_str}{pl}"
        return "⚪ ВНЕ ПОЗИЦИИ"

    bi = block("IMOEX", s["si"], s["ip"], "",  "IMOEX")
    bu = block("USD",   s["su"], s["up"], "",  "USD")
    bs = block("SPY",   s["ss"], s["sp"], "$", "SPY")
    bg = block("GLD",   s["sg"], s["gp"], "$", "GLD")

    return (
        f"📊 Сигналы — {t}\n\n"
        f"🇷🇺 IMOEX\n{bi}\n"
        f"  Текущий: {s['ip']:.0f}\n\n"
        f"💵 USDRUB\n{bu}\n"
        f"  USD/RUB: {s['up']:.2f}\n\n"
        f"🇺🇸 SPY (S&P 500)\n{bs}\n"
        f"  SPY: ${s['sp']:.2f}\n\n"
        f"🥇 GLD (Золото)\n{bg}\n"
        f"  GLD: ${s['gp']:.2f}"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Бот сигналов запущен!\n/signal — сигналы\n/help — помощь")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Считаю... (~30 сек)")
    try:
        state = load_state()
        s = await asyncio.get_event_loop().run_in_executor(None, calc_signals)
        update_entries(s, state)
        await update.message.reply_text(make_report(s, state))
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
