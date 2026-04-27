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

def get_moex_weekly(ticker, weeks=28):
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

def get_moex_today(ticker):
    url = (f"https://iss.moex.com/iss/engines/stock/markets/index/"
           f"boards/SNDX/securities/{ticker}.json"
           f"?iss.meta=off&iss.only=marketdata"
           f"&marketdata.columns=SECID,CURRENTVALUE,LASTVALUE")
    try:
        data = requests.get(url, timeout=10).json()
        rows = data["marketdata"]["data"]
        for r in rows:
            if r[0] == ticker:
                val = r[1] if r[1] else r[2]
                return float(val) if val else None
        return None
    except Exception as e:
        log.error(f"MOEX today {ticker}: {e}")
        return None

def get_usdrub_weekly(weeks=28):
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

def get_usdrub_today():
    url = (f"https://iss.moex.com/iss/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?iss.meta=off&marketdata.columns=SECID,LAST")
    try:
        rows = requests.get(url, timeout=10).json()["marketdata"]["data"]
        for r in rows:
            if r[0] == "USD000UTSTOM" and r[1]:
                return float(r[1])
        return None
    except:
        return None

def get_yahoo_weekly(ticker, weeks=28):
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

def get_yahoo_today(ticker):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval=1d&range=5d")
    try:
        data = requests.get(url, headers={"User-Agent":"Mozilla/5.0"},
                            timeout=15).json()
        cls = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        vals = [x for x in cls if x is not None]
        return vals[-1] if vals else None
    except:
        return None

def P(data): return [x[1] for x in data]
def D(data): return [x[0] for x in data]

def ret(p, n):
    if len(p) < n+1: return 0.0
    return (p[-1]/p[-n-1]-1)*100

def find_entry(data, is_open_fn):
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

def calc_all():
    state = load_state()

    spy_d   = get_yahoo_weekly("SPY")
    gld_d   = get_yahoo_weekly("GLD")
    imoex_d = get_moex_weekly("IMOEX")
    rugbi_d = get_moex_weekly("RGBITR")
    usd_d   = get_usdrub_weekly()

    ps = P(spy_d);   pg = P(gld_d)
    pi = P(imoex_d); pr = P(rugbi_d); pu = P(usd_d)

    # Дневные цены — для актуального P&L
    spy_now   = get_yahoo_today("SPY")   or (ps[-1] if ps else 0)
    gld_now   = get_yahoo_today("GLD")   or (pg[-1] if pg else 0)
    imoex_now = get_moex_today("IMOEX") or (pi[-1] if pi else 0)
    usd_now   = get_usdrub_today()       or (pu[-1] if pu else 0)

    # ── SPY: 4нед momentum + asymm lookback ──
    sig_spy = "SPY" if (len(ps)>4 and ret(ps,4)>0) else "CASH"
    if sig_spy == "CASH" and len(ps) > 8:
        cash_w = state.get("SPY_cash_w", 0)
        if ret(ps,8) <= -8.0 and cash_w >= 4 and ret(ps,1) > 0:
            sig_spy = "SPY"
    state["SPY_cash_w"] = 0 if sig_spy=="SPY" else state.get("SPY_cash_w",0)+1

    # ── GLD: 4нед momentum + asymm lookback ──
    sig_gld = "GLD" if (len(pg)>4 and ret(pg,4)>0) else "CASH"
    if sig_gld == "CASH" and len(pg) > 8:
        cash_w = state.get("GLD_cash_w", 0)
        if ret(pg,8) <= -8.0 and cash_w >= 4 and ret(pg,1) > 0:
            sig_gld = "GLD"
    state["GLD_cash_w"] = 0 if sig_gld=="GLD" else state.get("GLD_cash_w",0)+1

    # ── IMOEX: 4нед vs RUGBI + asymm LB + trailing 3% ──
    # Шаг 1: базовый сигнал — IMOEX опережает RUGBI за 4 недели
    sig_imoex = "IMOEX" if (len(pi)>4 and len(pr)>4 and ret(pi,4)>=ret(pr,4)) else "CASH"

    # Шаг 2: asymm lookback — только если в кэше ≥4 недель подряд
    if sig_imoex == "CASH" and len(pi) > 8 and len(pr) > 4:
        cash_w = state.get("IMOEX_cash_w", 0)
        if ret(pi,8) <= -8.0 and cash_w >= 4 and ret(pi,1) > 0:
            sig_imoex = "IMOEX"
            state["IMOEX_override"] = True
            state["IMOEX_peak"] = imoex_now

    # Шаг 3: trailing stop 3% — применяется к ЛЮБОЙ открытой позиции IMOEX
    if sig_imoex == "IMOEX":
        peak = state.get("IMOEX_peak", imoex_now)
        if imoex_now > peak:
            state["IMOEX_peak"] = imoex_now  # обновляем пик вверх
        elif imoex_now < peak * 0.97:
            sig_imoex = "CASH"               # трейлинг стоп сработал
            state["IMOEX_override"] = False
            state["IMOEX_peak"] = 0
        else:
            state["IMOEX_peak"] = peak       # держим пик
    else:
        # Вне позиции — сбрасываем пик и override
        state["IMOEX_override"] = False
        state["IMOEX_peak"] = 0

    state["IMOEX_cash_w"] = 0 if sig_imoex=="IMOEX" else state.get("IMOEX_cash_w",0)+1

    # ── USDRUB: двойной 4нед+1нед + окно выхода + trailing 5% ──
    raw_usd = "USD" if (len(pu)>4 and len(pr)>4 and
                        (ret(pu,4)>=ret(pr,4) or ret(pu,1)>=ret(pr,1))) else "CASH"
    prev_usd = state.get("USD_prev", raw_usd)
    sig_usd  = raw_usd
    if raw_usd=="CASH" and prev_usd=="USD":
        if not state.get("USD_exit_pending"):
            state["USD_exit_pending"] = True; sig_usd = "USD"
        else:
            state["USD_exit_pending"] = False
    else:
        state["USD_exit_pending"] = False
    if sig_usd=="USD":
        peak = state.get("USD_peak", usd_now)
        if usd_now > peak: state["USD_peak"] = usd_now
        elif usd_now < peak*0.95:
            sig_usd = "CASH"; state["USD_peak"] = 0
    elif not state.get("USD_peak"):
        state["USD_peak"] = usd_now
    state["USD_prev"] = sig_usd

    # ── Цены входа ──
    for key, sig, data, fn in [
        ("SPY", sig_spy, spy_d, lambda p: ret(p,4)>0),
        ("GLD", sig_gld, gld_d, lambda p: ret(p,4)>0),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        if sig!="CASH" and prev=="CASH":
            ep, ed = find_entry(data, fn)
            state[f"{key}_entry"] = ep; state[f"{key}_entry_date"] = ed
        elif sig=="CASH":
            state.pop(f"{key}_entry", None); state.pop(f"{key}_entry_date", None)
        if sig!="CASH" and not state.get(f"{key}_entry") and data:
            ep, ed = find_entry(data, fn)
            state[f"{key}_entry"] = ep; state[f"{key}_entry_date"] = ed
        state[f"{key}_sig"] = sig

    for key, sig, now_price, data in [
        ("IMOEX", sig_imoex, imoex_now, imoex_d),
        ("USD",   sig_usd,   usd_now,   usd_d),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        if sig!="CASH" and prev=="CASH":
            state[f"{key}_entry"] = now_price
            state[f"{key}_entry_date"] = datetime.today().strftime("%Y-%m-%d")
        elif sig=="CASH":
            state.pop(f"{key}_entry", None); state.pop(f"{key}_entry_date", None)
        if sig!="CASH" and not state.get(f"{key}_entry"):
            state[f"{key}_entry"] = now_price
            state[f"{key}_entry_date"] = datetime.today().strftime("%Y-%m-%d")
        state[f"{key}_sig"] = sig

    save_state(state)

    return {
        "sig_spy":   sig_spy,   "p_spy":   spy_now,
        "sig_gld":   sig_gld,   "p_gld":   gld_now,
        "sig_imoex": sig_imoex, "p_imoex": imoex_now,
        "sig_usd":   sig_usd,   "p_usd":   usd_now,
        "state": state,
    }

def block(key, sig, price, cur_sym, state):
    if sig != "CASH":
        entry = state.get(f"{key}_entry")
        edate = state.get(f"{key}_entry_date", "")
        if entry and price:
            pct  = (price/entry-1)*100
            sign = "+" if pct>=0 else ""
            pl   = f"\n  Вход {edate}: {cur_sym}{entry:.2f} | P&L: {sign}{pct:.1f}%"
        else:
            pl = ""
        return f"🟢 ПОЗИЦИЯ ОТКРЫТА{pl}\n  Сейчас: {cur_sym}{price:.2f}"
    return f"⚪ ВНЕ ПОЗИЦИИ\n  Сейчас: {cur_sym}{price:.2f}"

def make_report(r):
    state = r["state"]
    t     = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        f"📊 Сигналы — {t}\n\n"
        f"🇷🇺 IMOEX\n"
        f"{block('IMOEX', r['sig_imoex'], r['p_imoex'], '', state)}\n\n"
        f"💵 USDRUB\n"
        f"{block('USD', r['sig_usd'], r['p_usd'], '', state)}\n\n"
        f"🇺🇸 SPY (S&P 500)\n"
        f"{block('SPY', r['sig_spy'], r['p_spy'], '$', state)}\n\n"
        f"🥇 GLD (Золото)\n"
        f"{block('GLD', r['sig_gld'], r['p_gld'], '$', state)}"
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот стратегических сигналов\n\n"
        "/signal — текущие позиции и P&L\n"
        "/setentry АКТИВ ЦЕНА ДАТА — ввод вручную\n"
        "/resetimoex — сбросить позицию IMOEX\n"
        "/help — о стратегиях")

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
                "Пример: /setentry SPY 542.10 06.04.2026\n"
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
            f"✅ Вход по {key} обновлён\n"
            f"  Цена: {price} | Дата: {date}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_resetimoex(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сброс позиции IMOEX — используй если бот показывает неверный сигнал."""
    state = load_state()
    state.pop("IMOEX_sig",        None)
    state.pop("IMOEX_entry",      None)
    state.pop("IMOEX_entry_date", None)
    state["IMOEX_override"] = False
    state["IMOEX_peak"]     = 0
    state["IMOEX_cash_w"]   = 0
    save_state(state)
    await update.message.reply_text(
        "✅ IMOEX сброшен → ВНЕ ПОЗИЦИИ\n"
        "Нажми /signal чтобы пересчитать сигнал с нуля.")

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 О стратегиях\n\n"
        "Все стратегии — momentum модели. Каждую неделю "
        "система сравнивает силу активов и держит позицию "
        "в более сильном. Когда сила пропадает — уходит в кэш.\n\n"
        "🇺🇸 SPY (S&P 500 vs кэш)\n"
        "  Период: 2008–2025\n"
        "  CAGR: +16.6% | Max DD: −6.0%\n"
        "  Бенчмарк S&P 500: CAGR +10.7%\n\n"
        "🥇 GLD (Золото vs кэш)\n"
        "  Период: 2010–2025\n"
        "  CAGR: +9.9% | Max DD: −13.3%\n"
        "  Бенчмарк GLD: CAGR +6.9%\n\n"
        "🇷🇺 IMOEX (акции vs облигации РФ)\n"
        "  Период: 2012–2025\n"
        "  CAGR: +13.3% | Max DD: −21.3%\n"
        "  Бенчмарк IMOEX: CAGR ~5%\n\n"
        "💵 USDRUB (доллар vs облигации РФ)\n"
        "  Период: 2010–2026\n"
        "  CAGR: +22.0% | Max DD: −6.7%\n"
        "  Бенчмарк USD B&H: CAGR +6.0%\n\n"
        "Сигналы еженедельные. Не является "
        "инвестиционной рекомендацией.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("signal",     cmd_signal))
    app.add_handler(CommandHandler("setentry",   cmd_setentry))
    app.add_handler(CommandHandler("resetimoex", cmd_resetimoex))
    app.add_handler(CommandHandler("help",       cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
