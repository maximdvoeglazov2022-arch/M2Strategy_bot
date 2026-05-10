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

# ── STATE ─────────────────────────────────────────────────────────────────────
def load_state():
    if Path(STATE_FILE).exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

# ── DATA FETCHERS ──────────────────────────────────────────────────────────────

def get_index_weekly(ticker, weeks=28):
    """
    IMOEX и RGBITR — через candles endpoint.
    /history/boards/SNDX возвращает CLOSE=0 на большинстве дней,
    /candles даёт корректные дневные значения индекса.
    """
    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(weeks=weeks)
    start    = start_dt.strftime('%Y-%m-%d')
    end      = end_dt.strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/engines/stock/markets/index/"
           f"securities/{ticker}/candles.json"
           f"?interval=24&from={start}&till={end}&iss.meta=off")
    try:
        data    = requests.get(url, timeout=12).json()
        candles = data.get("candles", {})
        columns = candles.get("columns", [])
        rows    = candles.get("data", [])
        if not rows:
            raise ValueError("Нет данных")
        ci = columns.index("close")
        bi = columns.index("begin")
        w  = {}
        for row in rows:
            d = str(row[bi])[:10]   # "2026-01-05 00:00:00" → "2026-01-05"
            c = row[ci]
            if c:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, float(c))
        result = [(w[k][0], w[k][1]) for k in sorted(w)]
        log.info(f"Candles {ticker}: {len(result)} нед. точек, "
                 f"последняя {result[-1][0]}={result[-1][1]:.2f}")
        return result
    except Exception as e:
        log.error(f"Candles {ticker}: {e}")
        return []

def get_index_today(ticker):
    """Текущее значение индекса (IMOEX, RGBITR)."""
    url = (f"https://iss.moex.com/iss/engines/stock/markets/index/"
           f"boards/SNDX/securities/{ticker}.json"
           f"?iss.meta=off&iss.only=marketdata"
           f"&marketdata.columns=SECID,CURRENTVALUE,LASTVALUE")
    try:
        rows = requests.get(url, timeout=10).json()["marketdata"]["data"]
        for r in rows:
            if r[0] == ticker:
                val = r[1] if r[1] else r[2]
                return float(val) if val else None
    except Exception as e:
        log.error(f"Index today {ticker}: {e}")
    return None

def get_usd_weekly(weeks=28):
    """USD/RUB через history/currency с limit=500."""
    start = (datetime.today() - timedelta(weeks=weeks)).strftime('%Y-%m-%d')
    url = (f"https://iss.moex.com/iss/history/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?from={start}&iss.meta=off"
           f"&history.columns=TRADEDATE,CLOSE&limit=500&start=0")
    try:
        rows = requests.get(url, timeout=12).json()["history"]["data"]
        w = {}
        for d, c in rows:
            if c:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, float(c))
        result = [(w[k][0], w[k][1]) for k in sorted(w)]
        log.info(f"USD weekly: {len(result)} нед. точек")
        return result
    except Exception as e:
        log.error(f"USD weekly: {e}")
        return []

def get_usd_today():
    url = (f"https://iss.moex.com/iss/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?iss.meta=off&marketdata.columns=SECID,LAST")
    try:
        rows = requests.get(url, timeout=10).json()["marketdata"]["data"]
        for r in rows:
            if r[0] == "USD000UTSTOM" and r[1]:
                return float(r[1])
    except:
        pass
    return None

def get_yahoo_weekly(ticker, weeks=28):
    """SPY / GLD — пробуем оба Yahoo-эндпоинта."""
    end   = datetime.today()
    start = end - timedelta(weeks=weeks)
    hdrs  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/120.0.0.0 Safari/537.36"}
    for base in ["https://query1.finance.yahoo.com",
                 "https://query2.finance.yahoo.com"]:
        url = (f"{base}/v8/finance/chart/{ticker}"
               f"?interval=1wk&period1={int(start.timestamp())}"
               f"&period2={int(end.timestamp())}")
        try:
            data = requests.get(url, headers=hdrs, timeout=15).json()
            r    = data["chart"]["result"][0]
            ts   = r["timestamp"]
            cls  = r["indicators"]["quote"][0]["close"]
            res  = [(datetime.fromtimestamp(t).strftime("%Y-%m-%d"), float(c))
                    for t, c in zip(ts, cls) if c is not None]
            if len(res) > 5:
                log.info(f"Yahoo {ticker}: {len(res)} нед. точек")
                return res
        except Exception as e:
            log.warning(f"Yahoo {base} {ticker}: {e}")
    log.error(f"Yahoo {ticker}: все попытки неудачны")
    return []

def get_yahoo_today(ticker):
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"}
    for base in ["https://query1.finance.yahoo.com",
                 "https://query2.finance.yahoo.com"]:
        try:
            url  = f"{base}/v8/finance/chart/{ticker}?interval=1d&range=5d"
            data = requests.get(url, headers=hdrs, timeout=15).json()
            cls  = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            vals = [x for x in cls if x is not None]
            if vals:
                return float(vals[-1])
        except:
            pass
    return None

# ── HELPERS ───────────────────────────────────────────────────────────────────
def P(data): return [x[1] for x in data]
def D(data): return [x[0] for x in data]

def ret(p, n):
    if len(p) < n + 1:
        return 0.0
    return (p[-1] / p[-n-1] - 1) * 100

def find_entry(data, is_open_fn):
    p = P(data); d = D(data)
    if len(p) < 6:
        return None, None
    prev_open = False
    entry_price = None; entry_date = None
    for i in range(5, len(p)):
        sub      = p[:i+1]
        open_now = is_open_fn(sub)
        if open_now and not prev_open:
            entry_price = sub[-1]
            entry_date  = d[i]
        prev_open = open_now
    return entry_price, entry_date

# ── SIGNAL CALCULATION ────────────────────────────────────────────────────────
def calc_all():
    state = load_state()

    spy_d   = get_yahoo_weekly("SPY")
    gld_d   = get_yahoo_weekly("GLD")
    imoex_d = get_index_weekly("IMOEX")
    rugbi_d = get_index_weekly("RGBITR")
    usd_d   = get_usd_weekly()

    ps = P(spy_d);   pg = P(gld_d)
    pi = P(imoex_d); pr = P(rugbi_d); pu = P(usd_d)

    data_ok = {
        "SPY":   len(ps) > 5,
        "GLD":   len(pg) > 5,
        "IMOEX": len(pi) > 5,
        "RUGBI": len(pr) > 5,
        "USD":   len(pu) > 5,
    }
    log.info(f"Точек данных: SPY={len(ps)} GLD={len(pg)} "
             f"IMOEX={len(pi)} RUGBI={len(pr)} USD={len(pu)}")

    spy_now   = get_yahoo_today("SPY")        or (ps[-1] if ps else None)
    gld_now   = get_yahoo_today("GLD")        or (pg[-1] if pg else None)
    imoex_now = get_index_today("IMOEX")      or (pi[-1] if pi else None)
    usd_now   = get_usd_today()               or (pu[-1] if pu else None)

    debug = {
        "SPY_4w":   round(ret(ps,4),2) if data_ok["SPY"]   else None,
        "GLD_4w":   round(ret(pg,4),2) if data_ok["GLD"]   else None,
        "IMOEX_4w": round(ret(pi,4),2) if data_ok["IMOEX"] else None,
        "RUGBI_4w": round(ret(pr,4),2) if data_ok["RUGBI"] else None,
        "USD_4w":   round(ret(pu,4),2) if data_ok["USD"]   else None,
        "USD_1w":   round(ret(pu,1),2) if data_ok["USD"]   else None,
        "IMOEX_8w": round(ret(pi,8),2) if data_ok["IMOEX"] else None,
        "pts":      {"SPY":len(ps),"GLD":len(pg),"IMOEX":len(pi),
                     "RUGBI":len(pr),"USD":len(pu)},
    }
    log.info(f"Моменты: {debug}")

    # ── SPY ───────────────────────────────────────────────────────────────────
    if not data_ok["SPY"]:
        sig_spy = state.get("SPY_sig", "CASH")
        log.warning("SPY: нет данных")
    else:
        sig_spy = "SPY" if ret(ps,4) > 0 else "CASH"
        if sig_spy == "CASH" and len(ps) > 8:
            cw = state.get("SPY_cash_w", 0)
            if ret(ps,8) <= -8.0 and cw >= 4 and ret(ps,1) > 0:
                sig_spy = "SPY"
        state["SPY_cash_w"] = 0 if sig_spy == "SPY" \
                              else state.get("SPY_cash_w", 0) + 1

    # ── GLD ───────────────────────────────────────────────────────────────────
    if not data_ok["GLD"]:
        sig_gld = state.get("GLD_sig", "CASH")
        log.warning("GLD: нет данных")
    else:
        sig_gld = "GLD" if ret(pg,4) > 0 else "CASH"
        if sig_gld == "CASH" and len(pg) > 8:
            cw = state.get("GLD_cash_w", 0)
            if ret(pg,8) <= -8.0 and cw >= 4 and ret(pg,1) > 0:
                sig_gld = "GLD"
        state["GLD_cash_w"] = 0 if sig_gld == "GLD" \
                              else state.get("GLD_cash_w", 0) + 1

    # ── IMOEX ─────────────────────────────────────────────────────────────────
    if not data_ok["IMOEX"] or not data_ok["RUGBI"]:
        sig_imoex = state.get("IMOEX_sig", "CASH")
        log.warning("IMOEX/RUGBI: нет данных")
    else:
        # Шаг 1: относительный momentum IMOEX vs RUGBI
        sig_imoex = "IMOEX" if ret(pi,4) >= ret(pr,4) else "CASH"

        # Шаг 2: asymm lookback (только если ≥4 нед в кэше)
        if sig_imoex == "CASH" and len(pi) > 8:
            cw = state.get("IMOEX_cash_w", 0)
            if ret(pi,8) <= -8.0 and cw >= 4 and ret(pi,1) > 0:
                sig_imoex = "IMOEX"
                state["IMOEX_override"] = True
                state["IMOEX_peak"] = imoex_now

        # Шаг 3: trailing stop 3% для любого входа
        if sig_imoex == "IMOEX" and imoex_now:
            peak = state.get("IMOEX_peak") or imoex_now
            if imoex_now > peak:
                state["IMOEX_peak"] = imoex_now
            elif imoex_now < peak * 0.97:
                log.info(f"IMOEX trailing stop: {imoex_now:.0f} < {peak*0.97:.0f}")
                sig_imoex = "CASH"
                state["IMOEX_override"] = False
                state["IMOEX_peak"] = 0
            else:
                state["IMOEX_peak"] = peak
        else:
            state["IMOEX_override"] = False
            state["IMOEX_peak"] = 0

        state["IMOEX_cash_w"] = 0 if sig_imoex == "IMOEX" \
                                else state.get("IMOEX_cash_w", 0) + 1

    # ── USDRUB ────────────────────────────────────────────────────────────────
    if not data_ok["USD"] or not data_ok["RUGBI"]:
        sig_usd = state.get("USD_sig", "CASH")
        log.warning("USD: нет данных")
    else:
        raw_usd  = "USD" if (ret(pu,4) >= ret(pr,4) or
                             ret(pu,1) >= ret(pr,1)) else "CASH"
        prev_usd = state.get("USD_prev", raw_usd)
        sig_usd  = raw_usd
        if raw_usd == "CASH" and prev_usd == "USD":
            if not state.get("USD_exit_pending"):
                state["USD_exit_pending"] = True
                sig_usd = "USD"
            else:
                state["USD_exit_pending"] = False
        else:
            state["USD_exit_pending"] = False
        if sig_usd == "USD" and usd_now:
            peak = state.get("USD_peak") or usd_now
            if usd_now > peak:
                state["USD_peak"] = usd_now
            elif usd_now < peak * 0.95:
                log.info(f"USD trailing stop: {usd_now:.2f} < {peak*0.95:.2f}")
                sig_usd = "CASH"
                state["USD_peak"] = 0
        elif not state.get("USD_peak") and usd_now:
            state["USD_peak"] = usd_now
        state["USD_prev"] = sig_usd

    # ── Цены входа ────────────────────────────────────────────────────────────
    for key, sig, data, fn in [
        ("SPY", sig_spy, spy_d, lambda p: ret(p,4) > 0),
        ("GLD", sig_gld, gld_d, lambda p: ret(p,4) > 0),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        if sig == "CASH":
            state.pop(f"{key}_entry",      None)
            state.pop(f"{key}_entry_date", None)
        elif prev == "CASH" or not state.get(f"{key}_entry"):
            ep, ed = find_entry(data, fn)
            if ep:
                state[f"{key}_entry"]      = ep
                state[f"{key}_entry_date"] = ed
        state[f"{key}_sig"] = sig

    for key, sig, now_price in [
        ("IMOEX", sig_imoex, imoex_now),
        ("USD",   sig_usd,   usd_now),
    ]:
        prev = state.get(f"{key}_sig", "CASH")
        if sig == "CASH":
            state.pop(f"{key}_entry",      None)
            state.pop(f"{key}_entry_date", None)
        elif prev == "CASH" or not state.get(f"{key}_entry"):
            if now_price:
                state[f"{key}_entry"]      = now_price
                state[f"{key}_entry_date"] = datetime.today().strftime("%Y-%m-%d")
        state[f"{key}_sig"] = sig

    save_state(state)
    return dict(sig_spy=sig_spy, p_spy=spy_now,
                sig_gld=sig_gld, p_gld=gld_now,
                sig_imoex=sig_imoex, p_imoex=imoex_now,
                sig_usd=sig_usd, p_usd=usd_now,
                state=state, debug=debug, data_ok=data_ok)

# ── FORMATTING ────────────────────────────────────────────────────────────────
def block(key, sig, price, sym, state):
    if sig != "CASH":
        entry = state.get(f"{key}_entry")
        edate = state.get(f"{key}_entry_date", "")
        if entry and price:
            pct  = (price / entry - 1) * 100
            sign = "+" if pct >= 0 else ""
            pl   = f"\n  Вход {edate}: {sym}{entry:.2f} | P&L: {sign}{pct:.1f}%"
        else:
            pl = ""
        return f"🟢 ПОЗИЦИЯ ОТКРЫТА{pl}\n  Сейчас: {sym}{price:.2f}"
    p_str = f"{sym}{price:.2f}" if price else "—"
    return f"⚪ ВНЕ ПОЗИЦИИ\n  Сейчас: {p_str}"

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

def make_debug(r):
    d  = r["debug"]
    ok = r["data_ok"]
    pts = d.get("pts", {})
    lines = [f"🔍 Диагностика — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n",
             "── Данные (точек) ──"]
    p_spy   = r["p_spy"]
    p_gld   = r["p_gld"]
    p_imoex = r["p_imoex"]
    p_usd   = r["p_usd"]
    lines.append(f"  SPY:   {'✅' if ok['SPY']   else '❌'} {pts.get('SPY',0)} нед"
                 + (f"  | Сейчас: ${p_spy:.2f}" if p_spy else ""))
    lines.append(f"  GLD:   {'✅' if ok['GLD']   else '❌'} {pts.get('GLD',0)} нед"
                 + (f"  | Сейчас: ${p_gld:.2f}" if p_gld else ""))
    lines.append(f"  IMOEX: {'✅' if ok['IMOEX'] else '❌'} {pts.get('IMOEX',0)} нед"
                 + (f" | Сейчас: {p_imoex:.2f}" if p_imoex else ""))
    lines.append(f"  RUGBI: {'✅' if ok['RUGBI'] else '❌'} {pts.get('RUGBI',0)} нед")
    lines.append(f"  USD:   {'✅' if ok['USD']   else '❌'} {pts.get('USD',0)} нед"
                 + (f"   | Сейчас: {p_usd:.2f}" if p_usd else ""))
    lines.append("")
    lines.append("── Momentum (4 нед) ──")
    lines.append(f"  SPY:   {d.get('SPY_4w','?')}%")
    lines.append(f"  GLD:   {d.get('GLD_4w','?')}%")
    lines.append(f"  IMOEX: {d.get('IMOEX_4w','?')}% vs RUGBI: {d.get('RUGBI_4w','?')}%")
    lines.append(f"  USD:   {d.get('USD_4w','?')}% (1нед: {d.get('USD_1w','?')}%)")
    lines.append(f"  IMOEX 8нед: {d.get('IMOEX_8w','?')}%")
    lines.append("")
    lines.append("── Сигналы ──")
    st = r["state"]
    lines.append(f"  SPY:   {r['sig_spy']}")
    lines.append(f"  GLD:   {r['sig_gld']}")
    lines.append(f"  IMOEX: {r['sig_imoex']}  "
                 f"(override={st.get('IMOEX_override',False)}, "
                 f"cash_w={st.get('IMOEX_cash_w',0)}, "
                 f"peak={st.get('IMOEX_peak',0):.0f})")
    lines.append(f"  USD:   {r['sig_usd']}  "
                 f"(peak={st.get('USD_peak',0):.2f})")
    return "\n".join(lines)

# ── COMMANDS ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот стратегических сигналов\n\n"
        "/signal — текущие позиции и P&L\n"
        "/debug — диагностика данных и моментов\n"
        "/setentry АКТИВ ЦЕНА ДАТА — ввод вручную\n"
        "/resetall — сброс всех позиций\n"
        "/help — о стратегиях")

async def cmd_signal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Считаю (~30 сек)...")
    try:
        r = await asyncio.get_event_loop().run_in_executor(None, calc_all)
        await update.message.reply_text(make_report(r))
    except Exception as e:
        log.error(e)
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_debug(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Загружаю данные...")
    try:
        r = await asyncio.get_event_loop().run_in_executor(None, calc_all)
        await update.message.reply_text(make_debug(r))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_resetall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    save_state({})
    await update.message.reply_text(
        "✅ Все позиции сброшены — state.json очищен\n"
        "Нажми /signal чтобы пересчитать с нуля.")

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
        price = float(args[1].replace(",", "."))
        date  = args[2] if len(args) > 2 else datetime.now().strftime("%d.%m.%Y")
        if key not in ("SPY", "GLD", "IMOEX", "USD"):
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

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 О стратегиях\n\n"
        "Все стратегии — momentum модели. Каждую неделю "
        "система сравнивает силу активов и держит позицию "
        "в более сильном. Когда сила пропадает — уходит в кэш.\n\n"
        "🇺🇸 SPY (S&P 500 vs кэш)\n"
        "  CAGR: +15.2% | Max DD: 0%\n\n"
        "🥇 GLD (Золото vs кэш)\n"
        "  CAGR: +7.5% | Max DD: −9.1%\n\n"
        "🇷🇺 IMOEX (акции vs облигации РФ)\n"
        "  CAGR: +10.5% | Max DD: −14.0%\n\n"
        "💵 USDRUB (доллар vs облигации РФ)\n"
        "  CAGR: +15.6% | Max DD: −4.8%\n\n"
        "Сигналы еженедельные.\n"
        "Не является инвестиционной рекомендацией.")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("signal",   cmd_signal))
    app.add_handler(CommandHandler("debug",    cmd_debug))
    app.add_handler(CommandHandler("resetall", cmd_resetall))
    app.add_handler(CommandHandler("setentry", cmd_setentry))
    app.add_handler(CommandHandler("help",     cmd_help))
    log.info("Бот запущен")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
