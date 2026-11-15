# -*- coding: utf-8 -*-
"""
Моментум по акциям: 7 лидеров индекса МосБиржи за 6 месяцев.

Правила (утверждены 08.11.2026):
  вселенная      — текущий состав индекса МосБиржи (MOEX ISS, аналитика индекса);
  ранжирование   — доходность за 182 календарных дня по ценам закрытия TQBR;
  отбор          — верхние 7, равными долями по 1/7;
  пересмотр      — раз в неделю, на закрытии последнего торгового дня недели;
  фильтр         — нет, нога всегда в рынке;
  место          — отдельный блок, в портфель четырёх ног не входит.

Корзина запоминается в momentum_state.json и меняется только когда закрылась
новая неделя. Внутри недели считается предварительный рейтинг — он показывает,
каким станет список, если неделя закроется сегодня, но на состав не влияет.

Доходность считается по ценам без дивидендов: MOEX не отдаёт готовый ряд полной
доходности по отдельным бумагам. Для ранжирования за полгода это смещает оценку
в пользу бумаг с низкой дивидендной доходностью — при сравнении помнить об этом.
"""
import json
import os
import datetime
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}
ANALYTICS = ("https://iss.moex.com/iss/statistics/engines/stock/markets/index/"
             "analytics/{idx}.json?limit=100&iss.meta=off")
CANDLES = ("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/"
           "securities/{sec}/candles.json?interval=24&from={frm}&till={till}&iss.meta=off")

INDEX = "IMOEX"
TOP_N = 7
LOOKBACK = 182                 # календарных дней ≈ 6 месяцев
WATCH = 3                      # сколько ближайших кандидатов показывать
STATE = "momentum_state.json"


def fetch(url, timeout=45):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def table(js, block):
    """Блок ответа ISS -> список словарей."""
    b = js.get(block) or {}
    cols = b.get("columns") or []
    return [dict(zip(cols, row)) for row in (b.get("data") or [])]


def constituents():
    """Текущий состав индекса: тикер и вес."""
    js = json.loads(fetch(ANALYTICS.format(idx=INDEX)).decode("utf-8"))
    rows = table(js, "analytics")
    if not rows:
        raise ValueError("ISS не вернул состав индекса")
    # берём срез за последнюю дату: в ответе могут быть несколько дат подряд
    dates = [r.get("tradedate") for r in rows if r.get("tradedate")]
    if dates:
        last = max(dates)
        rows = [r for r in rows if r.get("tradedate") == last]
    out = {}
    for r in rows:
        t = r.get("ticker") or r.get("secids") or r.get("secid")
        if not t:
            continue
        out[t] = {"ticker": t,
                  "name": r.get("shortnames") or r.get("shortname") or t,
                  "index_weight": r.get("weight")}
    return out


def closes(sec, frm, till):
    """[(дата, закрытие)] по дневным свечам TQBR."""
    js = json.loads(fetch(CANDLES.format(sec=sec, frm=frm, till=till)).decode("utf-8"))
    c = js.get("candles") or {}
    cols = c.get("columns") or []
    if "close" not in cols or "begin" not in cols:
        return []
    ci, bi = cols.index("close"), cols.index("begin")
    return [(str(r[bi])[:10], float(r[ci])) for r in (c.get("data") or []) if r[ci]]


def ret_over(rows, asof, days):
    """Доходность к дате asof за days календарных дней назад (ближайшее закрытие не позже базы)."""
    rows = [p for p in rows if p[0] <= asof]
    if len(rows) < 2:
        return None, None, None
    last_d, last_p = rows[-1]
    base = (datetime.date.fromisoformat(asof) - datetime.timedelta(days=days)).isoformat()
    prior = [p for p in rows if p[0] <= base]
    if not prior:
        return None, None, None
    base_d, base_p = prior[-1]
    if not base_p:
        return None, None, None
    return (last_p / base_p - 1) * 100, last_p, base_d


def week_key(d):
    """Дата пятницы, закрывающей неделю, в которую попадает d (неделя сб..пт)."""
    dt = datetime.date.fromisoformat(d) if isinstance(d, str) else d
    return (dt + datetime.timedelta(days=(4 - dt.weekday()) % 7)).isoformat()


def last_completed_obs(dates, today):
    """Последний торговый день закрывшейся недели.
    Неделя считается закрытой, если её пятница уже прошла (или сегодня пятница)."""
    if not dates:
        return None
    cur = week_key(today)
    done = [d for d in dates if week_key(d) < cur]
    if week_key(today.isoformat()) == today.isoformat():      # сегодня пятница
        same = [d for d in dates if week_key(d) == cur]
        if same:
            return max(same)
    return max(done) if done else None


def load_state():
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1, sort_keys=True)


def rank(prices, meta, asof):
    """Рейтинг бумаг по доходности за LOOKBACK дней на дату asof."""
    out = []
    for t, rows in prices.items():
        r, p, base_d = ret_over(rows, asof, LOOKBACK)
        if r is None:
            continue
        out.append({"ticker": t, "name": meta.get(t, {}).get("name", t),
                    "ret6m": round(r, 2), "price": round(p, 4), "base_date": base_d})
    out.sort(key=lambda x: -x["ret6m"])
    return out


def compute():
    today = datetime.date.today()
    till = today.isoformat()
    frm = (today - datetime.timedelta(days=LOOKBACK + 60)).isoformat()

    meta = constituents()
    print("  состав индекса: %d бумаг" % len(meta))

    prices, bad = {}, []
    for t in sorted(meta):
        try:
            rows = closes(t, frm, till)
            if len(rows) < 30:
                raise ValueError("мало свечей: %d" % len(rows))
            prices[t] = rows
        except Exception as e:
            bad.append("%s (%s)" % (t, e))
    if bad:
        print("  без котировок: %s" % ", ".join(bad))
    if len(prices) < TOP_N * 2:
        raise ValueError("слишком мало бумаг с котировками: %d" % len(prices))

    all_dates = sorted({d for rows in prices.values() for d, _ in rows})
    obs = last_completed_obs(all_dates, today)
    if obs is None:
        raise ValueError("не нашёл закрывшуюся неделю")
    print("  последняя закрытая неделя: %s" % obs)

    official = rank(prices, meta, obs)                 # рейтинг на закрытии недели
    preliminary = rank(prices, meta, all_dates[-1])    # рейтинг на последнем закрытии

    st = load_state()
    rotated = st.get("week") != obs
    if rotated:
        basket = [x["ticker"] for x in official[:TOP_N]]
        entry = {x["ticker"]: x["price"] for x in official[:TOP_N]}
        st = {"week": obs, "basket": basket, "entry": entry}
        save_state(st)
        print("  корзина пересобрана: %s" % ", ".join(basket))
    else:
        basket = st.get("basket") or [x["ticker"] for x in official[:TOP_N]]
        entry = st.get("entry") or {}
        print("  корзина без изменений: %s" % ", ".join(basket))

    by_t = {x["ticker"]: x for x in preliminary}
    held, tot = [], 0.0
    for t in basket:
        x = by_t.get(t) or {"ticker": t, "name": meta.get(t, {}).get("name", t),
                            "ret6m": None, "price": None}
        e = entry.get(t)
        since = round((x["price"] / e - 1) * 100, 2) if (e and x.get("price")) else None
        if since is not None:
            tot += since
        held.append({"ticker": t, "name": x["name"], "ret6m": x["ret6m"],
                     "price": x["price"], "entry": e, "since_rebalance": since,
                     "weight": round(1.0 / len(basket), 4)})

    top_now = [x["ticker"] for x in preliminary[:TOP_N]]
    watch = [{"ticker": x["ticker"], "name": x["name"], "ret6m": x["ret6m"]}
             for x in preliminary[TOP_N:TOP_N + WATCH]]
    leaving = [t for t in basket if t not in top_now]
    entering = [t for t in top_now if t not in basket]

    return {
        "name": "Моментум: 7 лидеров индекса МосБиржи",
        "rule": "доходность за 6 месяцев, верхние 7 равными долями, пересмотр на закрытии недели",
        "filter": "нет — блок всегда в рынке",
        "in_portfolio": False,
        "lookback_days": LOOKBACK,
        "week": obs,
        "rotated_now": rotated,
        "universe": len(prices),
        "basket": held,
        "basket_return_since_rebalance": round(tot / len(basket), 2) if basket else None,
        "preliminary_top": [{"ticker": x["ticker"], "name": x["name"], "ret6m": x["ret6m"]}
                            for x in preliminary[:TOP_N]],
        "watch": watch,
        "pending_out": leaving,
        "pending_in": entering,
        "note": "цены без дивидендов; предварительный список вступит в силу на закрытии недели",
    }
