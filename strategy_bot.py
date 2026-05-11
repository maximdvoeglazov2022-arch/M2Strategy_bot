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
    """IMOEX и RGBITR через /candles — корректные дневные значения индекса."""
    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(weeks=weeks)
    url = (f"https://iss.moex.com/iss/engines/stock/markets/index/"
           f"securities/{ticker}/candles.json"
           f"?interval=24"
           f"&from={start_dt.strftime('%Y-%m-%d')}"
           f"&till={end_dt.strftime('%Y-%m-%d')}"
           f"&iss.meta=off")
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
            d = str(row[bi])[:10]
            c = row[ci]
            if c:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, float(c))
        result = [(w[k][0], w[k][1]) for k in sorted(w)]
        log.info(f"Candles {ticker}: {len(result)} нед., "
                 f"посл. {result[-1][0]}={result[-1][1]:.2f}")
        return result
    except Exception as e:
        log.error(f"Candles {ticker}: {e}")
        return []

def get_index_today(ticker):
    """Текущее значение индекса MOEX."""
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
    """USD/RUB через /candles валютного рынка MOEX."""
    end_dt   = datetime.today()
    start_dt = end_dt - timedelta(weeks=weeks)
    url = (f"https://iss.moex.com/iss/engines/currency/markets/selt/"
           f"securities/USD000UTSTOM/candles.json"
           f"?interval=24"
           f"&from={start_dt.strftime('%Y-%m-%d')}"
           f"&till={end_dt.strftime('%Y-%m-%d')}"
           f"&iss.meta=off")
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
            d = str(row[bi])[:10]
            c = row[ci]
            if c and float(c) > 10:
                wk = datetime.strptime(d, "%Y-%m-%d").strftime("%Y-W%W")
                w[wk] = (d, float(c))
        result = [(w[k][0], w[k][1]) for k in sorted(w)]
        log.info(f"USD candles: {len(result)} нед., "
                 f"посл. {result[-1][0]}={result[-1][1]:.2f}")
        return result
    except Exception as e:
        log.error(f"USD candles: {e}")
        return []

def get_usd_today():
    """Текущий USD/RUB — WAPRICE (средневзвешенная за день)."""
    url = (f"https://iss.moex.com/iss/engines/currency/markets/selt/"
           f"boards/CETS/securities/USD000UTSTOM.json"
           f"?iss.meta=off"
           f"&marketdata.columns=SECID,LAST,WAPRICE,CURRENTPRICE")
    try:
        rows = requests.get(url, timeout=10).json()["marketdata"]["data"]
        for r in rows:
            if r[0] == "USD000UTSTOM":
                val = r[2] or r[3] or r[1]
                if val and float(val) > 10:
                    return float(val)
    except Exception as e:
        log.error(f"USD today: {e}")
    return None

def get_yahoo_weekly(ticker, weeks=28):
    """SPY, GLD, YCS, UUP, IEF — два Yahoo-эндпоинта с fallback."""
    end   = datetime.today()
    start = end - timedelta(weeks=weeks)
    hdrs  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/120.0.0.0 Safari/537.36"}
    for base in ["https://query1.finance.yahoo.com",
                 "https://query2.finance.yahoo.com"]:
        url = (f"{base}/v8/finance/chart/{ticker}"
               f"?interval=1wk"
               f"&period1={int(start.timestamp())}"
               f"&period2={int(end.timestamp())}")
        try:
            data = requests.get(url, headers=hdrs, timeout=15).json()
            r    = data["chart"]["result"][0]
            ts   = r["timestamp"]
            cls  = r["indicators"]["quote"][0]["close"]
            res  = [(datetime.fromtimestamp(t).strftime("%Y-%m-%d"), float(c))
                    for t, c in zip(ts, cls) if c is not None]
            if len(res) > 5:
                log.info(f"Yahoo {ticker}: {len(res)} нед., "
                         f"посл. {res[-1][0]}={res[-1][1]:.2f}")
                return res
        except Exception as e:
            log.warning(f"Yahoo {base} {ticker}: {e}")
    log.error(f"Yahoo {ticker}: все попытки неудачны")
    return []

def get_yahoo_today(ticker):
    """Последняя дневная цена с Yahoo Finance."""
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

def append_today(weekly_data, today_price):
    """
    Подставляем живую цену в конец недельной серии.
    Если последняя точка не старше 7 дней — заменяем её (та же рабочая неделя).
    Если старше 7 дней — добавляем новую точку (началась новая неделя).
    """
    if not today_price or not weekly_data:
        return weekly_data
    today_str = datetime.today().strftime("%Y-%m-%d")
    last_date = weekly_data[-1][0]
    last_dt   = datetime.strptime(last_date, "%Y-%m-%d")
    days_diff = (datetime.today() - last_dt).days
    if days_diff <= 7:
        # Заменяем — это та же неделя или начало следующей (пн после пт)
        return weekly_data[:-1] + [(today_str, today_price)]
    else:
        # Прошло больше недели — добавляем новую точку
        return weekly_data + [(today_str, today_price)]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def P(data): return [x[1] for x in data]
def D(data): return [x[0] for x in data]

def ret(p, n):
    if len(p) < n + 1:
        return 0.0
    return (p[-1] / p[-n-1] - 1) * 100

def blend_ret(pa, pb, n, wa=0.5, wb=0.5):
    """Взвешенный return двух серий."""
    ra = ret(pa, n) if len(pa) > n else 0.0
    rb = ret(pb, n) if len(pb) > n else 0.0
    return wa * ra + wb * rb

def align(pa, pb):
    """Обрезаем до общей длины (берём хвост)."""
    n = min(len(pa), len(pb))
    return pa[-n:], pb[-n:]

def find_entry(data, is_open_fn):
    """Дата и цена последнего входа через replay сигнала по истории."""
    p = P(data); d = D(data)
    if len(p) < 6:
        return None, None
    prev_open   = False
    entry_price = None
    entry_date  = None
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

    # ── Загружаем исторические данные ────────────────────────────────────────
    spy_d   = get_yahoo_weekly("SPY")
    uup_d   = get_yahoo_weekly("UUP")
    ief_d   = get_yahoo_weekly("IEF")
    gld_d   = get_yahoo_weekly("GLD")
    ycs_d   = get_yahoo_weekly("YCS")
    imoex_d = get_index_weekly("IMOEX")
    rugbi_d = get_index_weekly("RGBITR")
    usd_d   = get_usd_weekly()

    # ── Текущие (живые) цены ─────────────────────────────────────────────────
    spy_now   = get_yahoo_today("SPY")
    uup_now   = get_yahoo_today("UUP")
    ief_now   = get_yahoo_today("IEF")
    gld_now   = get_yahoo_today("GLD")
    ycs_now   = get_yahoo_today("YCS")
    imoex_now = get_index_today("IMOEX")
    rugbi_now = get_index_today("RGBITR")
    usd_now   = get_usd_today()

    # Фолбэк: если нет живой цены — берём последнее историческое значение
    spy_now   = spy_now   or (P(spy_d)[-1]   if spy_d   else None)
    uup_now   = uup_now   or (P(uup_d)[-1]   if uup_d   else None)
    ief_now   = ief_now   or (P(ief_d)[-1]   if ief_d   else None)
    gld_now   = gld_now   or (P(gld_d)[-1]   if gld_d   else None)
    ycs_now   = ycs_now   or (P(ycs_d)[-1]   if ycs_d   else None)
    imoex_now = imoex_now or (P(imoex_d)[-1] if imoex_d else None)
    rugbi_now = rugbi_now or (P(rugbi_d)[-1] if rugbi_d else None)
    usd_now   = usd_now   or (P(usd_d)[-1]   if usd_d   else None)

    # ── Подставляем живую цену в конец недельной серии ───────────────────────
    # Это ключевой фикс: p[-1] всегда = текущая цена,
    # p[-5] = ровно 4 недельных периода назад (корректный baseline)
    spy_d   = append_today(spy_d,   spy_now)
    uup_d   = append_today(uup_d,   uup_now)
    ief_d   = append_today(ief_d,   ief_now)
    gld_d   = append_today(gld_d,   gld_now)
    ycs_d   = append_today(ycs_d,   ycs_now)
    imoex_d = append_today(imoex_d, imoex_now)
    rugbi_d = append_today(rugbi_d, rugbi_now)
    usd_d   = append_today(usd_d,   usd_now)

    ps  = P(spy_d)
    puu = P(uup_d);  pie = P(ief_d)
    pg  = P(gld_d);  py  = P(ycs_d)
    pi  = P(imoex_d); pr = P(rugbi_d)
    pu  = P(usd_d)

    data_ok = {
        "SPY":   len(ps)  > 5,
        "UUP":   len(puu) > 5,
        "IEF":   len(pie) > 5,
        "GLD":   len(pg)  > 5,
        "YCS":   len(py)  > 5,
        "IMOEX": len(pi)  > 5,
        "RUGBI": len(pr)  > 5,
        "USD":   len(pu)  > 5,
    }
    log.info(f"Точек (с today): SPY={len(ps)} UUP={len(puu)} IEF={len(pie)} "
             f"GLD={len(pg)} YCS={len(py)} "
             f"IMOEX={len(pi)} RUGBI={len(pr)} USD={len(pu)}")

    # Бенчмарк SPY: 50% UUP + 50% IEF
    puu_a, pie_a = align(puu, pie)
    spy_bench_4w = blend_ret(puu_a, pie_a, 4)
    spy_bench_8w = blend_ret(puu_a, pie_a, 8)

    # Дата baseline 4 нед назад — для дебага
    def baseline_date(series):
        if len(series) >= 5:
            return series[-5][0]
        return "?"

    debug = {
        "SPY_4w":       round(ret(ps,4),2)      if data_ok["SPY"]   else None,
        "SPY_bench_4w": round(spy_bench_4w,2)   if (data_ok["UUP"] and data_ok["IEF"]) else None,
        "UUP_4w":       round(ret(puu,4),2)     if data_ok["UUP"]   else None,
        "IEF_4w":       round(ret(pie,4),2)     if data_ok["IEF"]   else None,
        "GLD_4w":       round(ret(pg,4),2)      if data_ok["GLD"]   else None,
        "YCS_4w":       round(ret(py,4),2)      if data_ok["YCS"]   else None,
        "IMOEX_4w":     round(ret(pi,4),2)      if data_ok["IMOEX"] else None,
        "RUGBI_4w":     round(ret(pr,4),2)      if data_ok["RUGBI"] else None,
        "USD_4w":       round(ret(pu,4),2)      if data_ok["USD"]   else None,
        "USD_1w":       round(ret(pu,1),2)      if data_ok["USD"]   else None,
        "IMOEX_8w":     round(ret(pi,8),2)      if data_ok["IMOEX"] else None,
        "GLD_8w":       round(ret(pg,8),2)      if data_ok["GLD"]   else None,
        "SPY_8w":       round(ret(ps,8),2)      if data_ok["SPY"]   else None,
        "base_spy":     baseline_date(spy_d),
        "base_gld":     baseline_date(gld_d),
        "base_imoex":   baseline_date(imoex_d),
        "base_usd":     baseline_date(usd_d),
        "pts": {
            "SPY":len(ps),"UUP":len(puu),"IEF":len(pie),
            "GLD":len(pg),"YCS":len(py),
            "IMOEX":len(pi),"RUGBI":len(pr),"USD":len(pu),
        },
    }
    log.info(f"Базовые даты: SPY={debug['base_spy']} GLD={debug['base_gld']} "
             f"IMOEX={debug['base_imoex']} USD={debug['base_usd']}")

    # ── SPY: 4нед momentum vs (50% UUP + 50% IEF) + asymm lookback ──────────
    spy_data_ready = data_ok["SPY"] and data_ok["UUP"] and data_ok["IEF"]
    if not spy_data_ready:
        sig_spy = state.get("SPY_sig", "CASH")
        log.warning("SPY/UUP/IEF: нет данных")
    else:
        min_len = min(len(ps), len(puu_a), len(pie_a))
        ps_a    = ps[-min_len:]
        sig_spy = "SPY" if ret(ps_a, 4) >= spy_bench_4w else "CASH"
        if sig_spy == "CASH" and len(ps) > 8:
            cw = state.get("SPY_cash_w", 0)
            if ret(ps,8) <= -8.0 and cw >= 4 and ret(ps,1) > 0:
                sig_spy = "SPY"
                log.info("SPY: asymm lookback → вход")
        state["SPY_cash_w"] = (0 if sig_spy == "SPY"
                               else state.get("SPY_cash_w", 0) + 1)

    # ── GLD: 4нед momentum GLD vs YCS + asymm lookback ───────────────────────
    if not data_ok["GLD"] or not data_ok["YCS"]:
        sig_gld = state.get("GLD_sig", "CASH")
        log.warning("GLD/YCS: нет данных")
    else:
        sig_gld = "GLD" if ret(pg,4) >= ret(py,4) else "CASH"
        if sig_gld == "CASH" and len(pg) > 8:
            cw = state.get("GLD_cash_w", 0)
            if ret(pg,8) <= -8.0 and cw >= 4 and ret(pg,1) > 0:
                sig_gld = "GLD"
                log.info("GLD: asymm lookback → вход")
        state["GLD_cash_w"] = (0 if sig_gld == "GLD"
                               else state.get("GLD_cash_w", 0) + 1)

    # ── IMOEX: 4нед relative momentum vs RUGBI + asymm LB + trailing 3% ─────
    if not data_ok["IMOEX"] or not data_ok["RUGBI"]:
        sig_imoex = state.get("IMOEX_sig", "CASH")
        log.warning("IMOEX/RUGBI: нет данных")
    else:
        sig_imoex = "IMOEX" if ret(pi,4) >= ret(pr,4) else "CASH"
        if sig_imoex == "CASH" and len(pi) > 8:
            cw = state.get("IMOEX_cash_w", 0)
            if ret(pi,8) <= -8.0 and cw >= 4 and ret(pi,1) > 0:
                sig_imoex = "IMOEX"
                state["IMOEX_override"] = True
                state["IMOEX_peak"]     = imoex_now
                log.info("IMOEX: asymm lookback → вход")
        if sig_imoex == "IMOEX" and imoex_now:
            peak = state.get("IMOEX_peak") or imoex_now
            if imoex_now > peak:
                state["IMOEX_peak"] = imoex_now
            elif imoex_now < peak * 0.97:
                log.info(f"IMOEX trailing stop: {imoex_now:.0f} < {peak*0.97:.0f}")
                sig_imoex = "CASH"
                state["IMOEX_override"] = False
                state["IMOEX_peak"]     = 0
            else:
                state["IMOEX_peak"] = peak
        else:
            state["IMOEX_override"] = False
            state["IMOEX_peak"]     = 0
        state["IMOEX_cash_w"] = (0 if sig_imoex == "IMOEX"
                                  else state.get("IMOEX_cash_w", 0) + 1)

    # ── USDRUB: двойной 4нед+1нед vs RUGBI + окно выхода + trailing 5% ───────
    if not data_ok["USD"] or not data_ok["RUGBI"]:
        sig_usd = state.get("USD_sig", "CASH")
        log.warning("USD/RUGBI: нет данных")
    else:
        raw_usd = ("USD" if (ret(pu,4) >= ret(pr,4) or
                             ret(pu,1) >= ret(pr,1)) else "CASH")
        prev_usd = state.get("USD_prev", raw_usd)
        sig_usd  = raw_usd
        if raw_usd == "CASH" and prev_usd == "USD":
            if not state.get("USD_exit_pending"):
                state["USD_exit_pending"] = True
                sig_usd = "USD"
                log.info("USD: 1-я неделя выхода — ждём подтверждения")
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

    # ── Запись цен входа ──────────────────────────────────────────────────────
    for key, sig, data, fn in [
        ("SPY", sig_spy, spy_d,
         lambda p: ret(p,4) >= blend_ret(puu_a[-len(p):], pie_a[-len(p):], 4)
                   if (len(puu_a) >= len(p) and len(pie_a) >= len(p))
                   else ret(p,4) > 0),
        ("GLD", sig_gld, gld_d,
         lambda p: ret(p,4) >= ret(py[-len(p):], 4)
                   if len(py) >= len(p) else ret(p,4) > 0),
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

    return dict(
        sig_spy=sig_spy,     p_spy=spy_now,
        sig_gld=sig_gld,     p_gld=gld_now,
        sig_imoex=sig_imoex, p_imoex=imoex_now,
        sig_usd=sig_usd,     p_usd=usd_now,
        state=state, debug=debug, data_ok=data_ok,
    )

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
    d   = r["debug"]
    ok  = r["data_ok"]
    pts = d.get("pts", {})
    st  = r["state"]
    p_spy   = r["p_spy"];   p_gld   = r["p_gld"]
    p_imoex = r["p_imoex"]; p_usd   = r["p_usd"]

    lines = [f"🔍 Диагностика — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n",
             "── Данные (точек, с текущей ценой) ──"]

    def row(key, label, extra=""):
        mark = "✅" if ok.get(key) else "❌"
        return f"  {label}: {mark} {pts.get(key,0)} нед{extra}"

    lines.append(row("SPY",  "SPY  ",
                     f"  | Сейчас: ${p_spy:.2f}" if p_spy else ""))
    lines.append(row("UUP",  "UUP  ", "  (бенчмарк SPY 50%)"))
    lines.append(row("IEF",  "IEF  ", "  (бенчмарк SPY 50%)"))
    lines.append(row("GLD",  "GLD  ",
                     f"  | Сейчас: ${p_gld:.2f}" if p_gld else ""))
    lines.append(row("YCS",  "YCS  ", "  (бенчмарк GLD)"))
    lines.append(row("IMOEX","IMOEX",
                     f" | Сейчас: {p_imoex:.2f}" if p_imoex else ""))
    lines.append(row("RUGBI","RUGBI", "  (бенчмарк IMOEX/USD)"))
    lines.append(row("USD",  "USD  ",
                     f"   | Сейчас: {p_usd:.2f}" if p_usd else ""))

    lines.append("\n── Baseline (от какой даты считаем 4 нед) ──")
    lines.append(f"  SPY/UUP/IEF: от {d.get('base_spy','?')}")
    lines.append(f"  GLD/YCS:     от {d.get('base_gld','?')}")
    lines.append(f"  IMOEX/RUGBI: от {d.get('base_imoex','?')}")
    lines.append(f"  USD:         от {d.get('base_usd','?')}")

    lines.append("\n── Momentum (4 нед) ──")
    bench = d.get("SPY_bench_4w","?")
    lines.append(f"  SPY:   {d.get('SPY_4w','?')}% vs Bench(UUP50+IEF50): {bench}%")
    lines.append(f"  GLD:   {d.get('GLD_4w','?')}% vs YCS: {d.get('YCS_4w','?')}%")
    lines.append(f"  IMOEX: {d.get('IMOEX_4w','?')}% vs RUGBI: {d.get('RUGBI_4w','?')}%")
    lines.append(f"  USD:   {d.get('USD_4w','?')}%  1нед: {d.get('USD_1w','?')}%"
                 f"  vs RUGBI: {d.get('RUGBI_4w','?')}%")
    lines.append(f"  SPY 8нед: {d.get('SPY_8w','?')}%  "
                 f"GLD 8нед: {d.get('GLD_8w','?')}%  "
                 f"IMOEX 8нед: {d.get('IMOEX_8w','?')}%")

    lines.append("\n── Сигналы ──")
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
    """Диагностика: данные, baseline даты, моменты, сигналы."""
    await update.message.reply_text("⏳ Загружаю данные...")
    try:
        r = await asyncio.get_event_loop().run_in_executor(None, calc_all)
        await update.message.reply_text(make_debug(r))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_resetall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Полный сброс state.json."""
    save_state({})
    await update.message.reply_text(
        "✅ Все позиции сброшены\n"
        "Нажми /signal для пересчёта.")

async def cmd_setentry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        args = ctx.args
        if len(args) < 2:
            await update.message.reply_text(
                "Формат: /setentry АКТИВ ЦЕНА ДАТА\n"
                "Пример: /setentry GLD 433.77 10.05.2026\n"
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
        "Системные momentum-стратегии на 4 рынках.\n"
        "Еженедельные сигналы на основе\n"
        "количественного анализа.\n\n"
        "🇺🇸 SPY (S&P 500)\n"
        "  CAGR: +15.2% | Max DD: 0%\n"
        "  Период: 2008–2025\n\n"
        "🥇 GLD (Золото)\n"
        "  CAGR: +7.5% | Max DD: −9.1%\n"
        "  Период: 2010–2025\n\n"
        "🇷🇺 IMOEX (РФ акции)\n"
        "  CAGR: +10.5% | Max DD: −14.0%\n"
        "  Период: 2012–2025\n\n"
        "💵 USDRUB\n"
        "  CAGR: +15.6% | Max DD: −4.8%\n"
        "  Период: 2012–2025\n\n"
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
