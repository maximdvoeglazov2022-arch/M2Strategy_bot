import os, logging, requests, asyncio, json
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
STATE_FILE     = "state.json"

def load_state():
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, ensure_ascii=False)

def get_moex(ticker, weeks=28):
    start = (datetime.today() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/history/engines/stock/markets/index/"
           f"boards/SNDX/securities/{ticker}.json"
           f"?from={start}&iss.meta=off&history.columns=TRADEDATE,CLOSE")
    try:
        rows = requests.get(url, timeout=10).json()["history"]["data"]
        w = {}
        for d, c in rows:
            if c:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, c)
        return [(w[k][0], w[k][1]) for k in sorted(w)]
    except Exception as e:
        log.error(f"MOEX {ticker}: {e}"); return []

def get_usdrub(weeks=28):
    start = (datetime.today() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/history/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?from={start}&iss.meta=off&history.columns=TRADEDATE,CLOSE")
    try:
        rows = requests.get(url, timeout=10).json()["history"]["data"]
        w = {}
        for d, c in rows:
            if c:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, c)
        return [(w[k][0], w[k][1]) for k in sorted(w)]
    except Exception as e:
        log.error(f"USDRUB: {e}"); return []

def get_yahoo(ticker, weeks=28):
    end   = datetime.today()
    start = end - timedelta(weeks=weeks)
    url   = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
             f"?interval=1wk&period1={int(start.timestamp())}"
             f"&period2={int(end.timestamp())}")
    try:
        data = requests.get(url, headers={"User-Agent":"Mozilla/5.0"},
                            timeout=15).json()
        r    = data["chart"]["result"][0]
        ts   = r["timestamp"]
        cls  = r["indicators"]["quote"][0]["close"]
        return [(datetime.fromtimestamp(t).strftime("%Y-%m-%d"), c)
                for t, c in zip(ts, cls) if c is not None]
    except Exception as e:
        log.error(f"Yahoo {ticker}: {e}"); return []

def P(data): return [x[1] for x in data]
def D(data): return [x[0] for x in data]
def ret(p, n):
    if len(p) < n+1: return 0.0
    return (p[-1]/p[-n-1]-1)*100

def find_entry(data, is_open_fn):
    """Найти дату и цену последнего входа в позицию через replay сигнала."""
    p = P(data); d = D(data)
    if len(p) < 6: return None, None
    prev_open = False
    entry_price = None; entry_date = None
    for i in range(5, len(p)):
        sub = p[:i+1]
        open_now = is_open_fn(sub)
        if open_now and not prev_open:
            entry_price = sub[-1]
            entry_date  = d[i]
        prev_open = open_now
    return entry_price, entry_date

def spy_open(p):
    return ret(p, 4) > 0

def gld_open(p):
    return ret(p, 4) > 0

def imoex_open(pi, pr):
    return ret(pi, 4) >= ret(pr, 4)

def usd_open(pu, pr):
    return ret(pu, 4) >= ret(pr, 4) or ret(pu, 1) >= ret(pr, 1)

def calc_all():
    state = load_state()

    spy_d   = get_yahoo("SPY")
    gld_d   = get_yahoo("GLD")
    imoex_d = get_moex("IMOEX")
    rugbi_d = get_moex("RGBITR")
    usd_d   = get_usdrub()

    ps = P(spy_d);   pg = P(gld_d)
    pi = P(imoex_d); pr = P(rugbi_d); pu = P(usd_d)

    # ── SPY: 4нед momentum + asymm lookback -8% ──
    sig_spy = "SPY" if (len(ps)>4 and ret(ps,4)>0) else "CASH"
    if sig_spy == "CASH" and len(ps) > 8:
        cash_w = state.get("SPY_cash_w", 0)
        if ret(ps, 8) <= -8.0 and cash_w >= 4 and ret(ps, 1) > 0:
            sig_spy = "SPY"
            log.info("SPY asymm LB triggered")
    state["SPY_cash_w"] = 0 if sig_spy=="SPY" else state.get("SPY_cash_w",0)+1

    # ── GLD: 4нед momentum + asymm lookback -8% ──
    sig_gld = "GLD" if (len(pg)>4 and ret(pg,4)>0) else "CASH"
    if sig_gld == "CASH" and len(pg) > 8:
        cash_w = state.get("GLD_cash_w", 0)
        if ret(pg, 8) <= -8.0 and cash_w >= 4 and ret(pg, 1) > 0:
            sig_gld = "GLD"
    state["GLD_cash_w"] = 0 if sig_gld=="GLD" else state.get("GLD_cash_w",0)+1

    # ── IMOEX: 4нед vs RUGBI + asymm LB -8% + trailing 3% ──
    sig_imoex = "IMOEX" if (len(pi)>4 and len(pr)>4 and ret(pi,4)>=ret(pr,4)) else "CASH"
    if state.get("IMOEX_override") and sig_imoex == "IMOEX":
        peak = state.get("IMOEX_peak", pi[-1] if pi else 0)
        cur  = pi[-1] if pi else 0
        if cur > peak: state["IMOEX_peak"] = cur
        elif cur < peak * 0.97:
            sig_imoex = "CASH"; state["IMOEX_override"] = False
            log.info("IMOEX trailing stop hit")
    if sig_imoex == "CASH" and len(pi) > 8 and len(pr) > 4:
        cash_w = state.get("IMOEX_cash_w", 0)
        if ret(pi,8) <= -8.0 and cash_w >= 4 and ret(pi,1) > 0:
            sig_imoex = "IMOEX"
            state["IMOEX_override"] = True
            state["IMOEX_peak"] = pi[-1]
    state["IMOEX_cash_w"] = 0 if sig_imoex=="IMOEX" else state.get("IMOEX_cash_w",0)+1

    # ── USDRUB: двойной 4нед+1нед + trailing 5% + окно выхода ──
    if len(pu) > 4 and len(pr) > 4:
        raw_usd = "USD" if (ret(pu,4)>=ret(pr,4) or ret(pu,1)>=ret(pr,1)) else "CASH"
    else:
        raw_usd = "CASH"
    prev_usd = state.get("USD_prev", raw_usd)
    sig_usd  = raw_usd
    if raw_usd == "CASH" and prev_usd == "USD":
        if not state.get("USD_exit_pending"):
            state["USD_exit_pending"] = True; sig_usd = "USD"
        else:
            state["USD_exit_pending"] = False
    else:
        state["USD_exit_pending"] = False
    if sig_usd == "USD" and pu:
        peak = state.get("USD_peak", pu[-1])
        if pu[-1] > peak: state["USD_peak"] = pu[-1]
        elif pu[-1] < peak * 0.95:
            sig_usd = "CASH"; state["USD_peak"] = 0
            log.info("USD trailing stop hit")
    elif not state.get("USD_peak") and pu:
        state["USD_peak"] = pu[-1]
    state["USD_prev"] = sig_usd

    # ── Цены входа: если нет в state — восстанавливаем из истории ──
    for key, sig, data, fn in [
        ("SPY",   sig_spy,   spy_d,   lambda p: ret(p,4)>0),
        ("GLD",   sig_gld,   gld_d,   lambda p: ret(p,4)>0),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        if sig != "CASH" and prev == "CASH":
            ep, ed = find_entry(data, fn)
            state[f"{key}_entry"]      = ep
            state[f"{key}_entry_date"] = ed
        elif sig == "CASH":
            state.pop(f"{key}_entry",      None)
            state.pop(f"{key}_entry_date", None)
        if sig != "CASH" and not state.get(f"{key}_entry") and data:
            ep, ed = find_entry(data, fn)
            state[f"{key}_entry"]      = ep
            state[f"{key}_entry_date"] = ed
        state[f"{key}_sig"] = sig

    for key, sig, data, pi2, pr2 in [
        ("IMOEX", sig_imoex, imoex_d, pi, pr),
        ("USD",   sig_usd,   usd_d,   pu, pr),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        cur_p = data[-1][1] if data else None
        cur_d = data[-1][0] if data else None
        if sig != "CASH" and prev == "CASH":
            state[f"{key}_entry"]      = cur_p
            state[f"{key}_entry_date"] = cur_d
        elif sig == "CASH":
            state.pop(f"{key}_entry",      None)
            state.pop(f"{key}_entry_date", None)
        if sig != "CASH" and not state.get(f"{key}_entry") and data:
            state[f"{key}_entry"]      = cur_p
            state[f"{key}_entry_date"] = cur_d
        state[f"{key}_sig"] = sig

    save_state(state)

    return {
        "sig_spy":   sig_spy,   "p_spy":   ps[-1] if ps else 0,
        "sig_gld":   sig_gld,   "p_gld":   pg[-1] if pg else 0,
        "sig_imoex": sig_imoex, "p_imoex": pi[-1] if pi else 0,
        "sig_usd":   sig_usd,   "p_usd":   pu[-1] if pu else 0,
        "state": state,
    }

def pnl(key, cur, state, cur_sym=""):
    entry = state.get(f"{key}_entry")
    edate = state.get(f"{key}_entry_date", "")
    if not entry or not cur: return ""
    pct  = (cur/entry-1)*100
    sign = "+" if pct >= 0 else ""
    return f"\n  Вход {edate}: {cur_sym}{entry:.2f} | P&L: {sign}{pct:.1f}%"

def make_report(r):
    state = r["state"]
    t     = datetime.now().strftime("%d.%m.%Y %H:%M")

    def block(key, sig, asset_name, price, cur_sym):
        if sig != "CASH":
            pl = pnl(key, price, state, cur_sym)
            return (f"🟢 ПОЗИЦИЯ ОТКРЫТА{pl}\n"
                    f"  Текущий: {cur_sym}{price:.2f}")
        return f"⚪ ВНЕ ПОЗИЦИИ\n  Текущий: {cur_sym}{price:.2f}"

    return (
        f"📊 Сигналы — {t}\n\n"
        f"🇷🇺 IMOEX\n"
        f"{block('IMOEX', r['sig_imoex'], 'IMOEX', r['p_imoex'], '')}\n\n"
        f"💵 USDRUB\n"
        f"{block('USD', r['sig_usd'], 'USD', r['p_usd'], '')}\n\n"
        f"🇺🇸 SPY (S&P 500)\n"
        f"{block('SPY', r['sig_spy'], 'SPY', r['p_spy'], '$')}\n\n"
        f"🥇 GLD (Золото)\n"
        f"{block('GLD', r['sig_gld'], 'GLD', r['p_gld'], '$')}"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот сигналов запущен!\n\n"
        "/signal — текущие сигналы\n"
        "/setentry SPY 542.10 15.03.2026 — вход вручную\n"
        "/help — стратегии")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Считаю (~30 сек)...")
    try:
        r = await asyncio.get_event_loop().run_in_executor(None, calc_all)
        await update.message.reply_text(make_report(r))
    except Exception as e:
        log.error(e)
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_setentry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        args = ctx.args
        if len(args) < 2:
            await update.message.reply_text(
                "Формат: /setentry АКТИВ ЦЕНА ДАТА\n"
                "Пример: /setentry SPY 542.10 15.03.2026\n"
                "Активы: SPY GLD IMOEX USD")
            return
        key   = args[0].upper()
        price = float(args[1].replace(",","."))
        date  = args[2] if len(args)>2 else datetime.now().strftime("%d.%m.%Y")
        if key not in ("SPY","GLD","IMOEX","USD"):
            await update.message.reply_text("Активы: SPY GLD IMOEX USD")
            return
        state = load_state()
        state[f"{key}_entry"]      = price
        state[f"{key}_entry_date"] = date
        state[f"{key}_sig"]        = key
        save_state(state)
        await update.message.reply_text(
            f"✅ Вход по {key} установлен\n"
            f"  Цена: {price}\n  Дата: {date}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Правила стратегий:\n\n"
        "🇺🇸 SPY\n"
        "  4нед momentum > 0 → в позиции\n"
        "  Asymm lookback: если -8% за 2мес\n"
        "  и кэш >4нед и текущая нед >0 → ранний вход\n\n"
        "🥇 GLD\n"
        "  Те же правила что SPY\n\n"
        "🇷🇺 IMOEX vs RUGBI\n"
        "  4нед IMOEX > RUGBI → в позиции\n"
        "  Asymm lookback -8% за 2мес\n"
        "  Trailing stop 3% от пика\n\n"
        "💵 USDRUB vs RUGBI\n"
        "  Двойной сигнал: 4нед ИЛИ 1нед\n"
        "  Выход: 2 нед подтверждения\n"
        "  Trailing stop 5% от пика\n\n"
        "Данные: MOEX ISS + Yahoo Finance")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("signal",   cmd_signal))
    app.add_handler(CommandHandler("setentry", cmd_setentry))
    app.add_handler(CommandHandler("help",     cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
