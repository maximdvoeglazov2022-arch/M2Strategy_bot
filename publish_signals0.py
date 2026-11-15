# -*- coding: utf-8 -*-
"""
Публикация сигналов для терминала MacroScope.

Считает четыре ноги по спецификации «M2 Advisory · Global Macro» v1.0
(относительный моментум: доходность актива минус доходность индикатора
за окно, наблюдение на закрытии недели). Эталон логики — m2_signals.py,
этот файл только достаёт данные и раскладывает результат в signals.json.

Прежняя версия брала расчёт из strategy_bot.py — там правила бота v6,
они другие. Бот при этом не трогается и работает как работал.

Запускается GitHub Actions, поэтому ограничений браузера по CORS нет
и зарубежные площадки доступны напрямую.
"""
import json
import datetime
import urllib.request
import sys

import pandas as pd

from m2_signals import compute_all, overheat_z, LEGS

YEARS = 5                      # окна до 130 т.д. плюс запас на журнал сигналов
UA = {"User-Agent": "Mozilla/5.0"}

MOEX_IDX = ("https://iss.moex.com/iss/engines/stock/markets/index/securities/"
            "{sec}/candles.json?interval=24&from={frm}&till={till}&iss.meta=off")
CBR = ("https://www.cbr.ru/scripts/XML_dynamic.asp"
       "?date_req1={d1}&date_req2={d2}&VAL_NM_RQ=R01235")
STOOQ = "https://stooq.com/q/d/l/?s={sym}&i=d"
YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?interval=1d&range={rng}&events=div%7Csplit")

NAMES = {
    "IMOEX": ("Индекс МосБиржи", ""),
    "USD":   ("Доллар к рублю", ""),
    "GLD":   ("Золото", "$"),
    "SPY":   ("S&P 500", "$"),
}
ORDER = ["IMOEX", "USD", "GLD", "SPY"]


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def series(pairs, name):
    """[(дата, значение)] -> pd.Series с DatetimeIndex, по возрастанию, без дублей."""
    if not pairs:
        return pd.Series(dtype="float64", name=name)
    idx = pd.to_datetime([p[0] for p in pairs])
    s = pd.Series([float(p[1]) for p in pairs], index=idx, name=name)
    return s[~s.index.duplicated(keep="last")].sort_index().dropna()


# ──────────────────────────────────────────────────────────── источники

def moex_index(sec, frm, till):
    """Дневные закрытия индекса. Только /candles: /history/boards отдаёт CLOSE=0."""
    js = json.loads(fetch(MOEX_IDX.format(sec=sec, frm=frm, till=till)).decode("utf-8"))
    c = js["candles"]
    ci = c["columns"].index("close")
    bi = c["columns"].index("begin")
    rows = [(str(r[bi])[:10], r[ci]) for r in c["data"] if r[ci]]
    return series(rows, sec)


def cbr_usd(frm, till):
    """Официальный курс ЦБ по дате установления. Сдвиг на дату публикации делает m2_signals."""
    import xml.etree.ElementTree as ET
    d1 = datetime.date.fromisoformat(frm).strftime("%d/%m/%Y")
    d2 = datetime.date.fromisoformat(till).strftime("%d/%m/%Y")
    root = ET.fromstring(fetch(CBR.format(d1=d1, d2=d2)))
    rows = []
    for rec in root.findall("Record"):
        dt = rec.get("Date")
        val = (rec.findtext("Value") or "").replace(",", ".")
        nom = float(rec.findtext("Nominal", "1") or 1)
        if not dt or not val:
            continue
        d, m, y = dt.split(".")
        rows.append(("%s-%s-%s" % (y, m, d), float(val) / nom))
    return series(rows, "USDRUB_CBR")


def stooq(sym):
    """Закрытия Stooq — уже с поправкой на дивиденды и сплиты."""
    txt = fetch(STOOQ.format(sym=sym)).decode("utf-8", "replace")
    lines = txt.strip().split("\n")
    if len(lines) < 30:
        raise ValueError("пустой ответ (%d строк)" % len(lines))
    head = lines[0].split(",")
    di, ci = head.index("Date"), head.index("Close")
    rows = []
    for ln in lines[1:]:
        p = ln.split(",")
        if len(p) <= max(di, ci):
            continue
        try:
            rows.append((p[di], float(p[ci])))
        except ValueError:
            continue
    return series(rows, sym)


def yahoo_adj(sym):
    """Запасной источник: adjclose Yahoo — та же база, что у Stooq (с дивидендами)."""
    js = json.loads(fetch(YAHOO.format(sym=sym, rng="%dy" % (YEARS + 1))).decode("utf-8"))
    res = js["chart"]["result"][0]
    ts = res["timestamp"]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    if not adj:
        adj = res["indicators"]["quote"][0]["close"]
    rows = []
    for t, v in zip(ts, adj):
        if v is None:
            continue
        rows.append((datetime.datetime.utcfromtimestamp(t).date().isoformat(), v))
    return series(rows, sym)


def us_series(sym_stooq, sym_yahoo):
    """Stooq, при отказе — Yahoo. Ряды сопоставимы: оба с учётом дивидендов."""
    try:
        s = stooq(sym_stooq)
        if len(s) >= 300:
            return s, "stooq"
        raise ValueError("слишком короткий ряд: %d" % len(s))
    except Exception as e:
        print("    stooq %s не вышел (%s), пробую Yahoo" % (sym_stooq, e))
        return yahoo_adj(sym_yahoo), "yahoo"


# ──────────────────────────────────────────────────────────── сбор

def collect():
    till = datetime.date.today()
    frm = till.replace(year=till.year - YEARS)
    f, t = frm.isoformat(), till.isoformat()
    data, src = {}, {}

    for sec in ("IMOEX", "RVI", "RUGBITR1Y"):
        data[sec] = moex_index(sec, f, t)
        src[sec] = "moex"
        print("  %-10s %4d дн., последний %s = %.2f"
              % (sec, len(data[sec]), data[sec].index[-1].date(), data[sec].iloc[-1]))

    data["USDRUB_CBR"] = cbr_usd(f, t)
    src["USDRUB_CBR"] = "cbr"
    print("  %-10s %4d дн., последний %s = %.4f"
          % ("USDRUB", len(data["USDRUB_CBR"]),
             data["USDRUB_CBR"].index[-1].date(), data["USDRUB_CBR"].iloc[-1]))

    for key, pair in (("GLD", ("gld.us", "GLD")), ("YCS", ("ycs.us", "YCS")),
                      ("SPY", ("spy.us", "SPY")), ("IEF", ("ief.us", "IEF")),
                      ("UUP", ("uup.us", "UUP"))):
        data[key], src[key] = us_series(pair[0], pair[1])
        print("  %-10s %4d дн., последний %s = %.2f (%s)"
              % (key, len(data[key]), data[key].index[-1].date(),
                 data[key].iloc[-1], src[key]))
    return data, src


# ──────────────────────────────────────────────────────────── вход в позицию

def entry_of(weights, frame, asset):
    """Дата и цена входа в позицию, которая держится СЕЙЧАС.

    Доля этой недели решена на закрытии прошлой — это weights[-2], та же величина,
    что уходит в held_weight. Отсчитывать вход от weights[-1] нельзя: там цель на
    следующую неделю, и при развороте сигнала вход «исчезал» у ещё открытой позиции.

    Частичная фиксация (1 → 0.25) позицию не прерывает, вход остаётся прежним."""
    w = weights.dropna()
    if len(w) < 2:
        return None, None
    pos = len(w) - 2
    if w.iloc[pos] <= 0:
        return None, None
    while pos > 0 and w.iloc[pos - 1] > 0:
        pos -= 1
    start = w.index[pos]
    if start not in frame.index:
        return None, None
    return str(start.date()), round(float(frame.loc[start, asset]), 4)


def main():
    print("Сбор данных…")
    try:
        data, src = collect()
    except Exception as e:
        print("НЕ УДАЛОСЬ СОБРАТЬ ДАННЫЕ: %s: %s" % (type(e).__name__, e))
        sys.exit(1)

    print("Расчёт сигналов…")
    res = compute_all(data)

    items = []
    for key in ORDER:
        r = res[key]
        cfg = LEGS[key]
        cur = r["current"]
        held = r["held_weight"]
        price = round(float(cur["asset"]), 4)
        ed, ep = entry_of(r["weights"], r["frame"], cfg.asset)
        open_ = held > 0
        pnl = round((price / ep - 1) * 100, 2) if (open_ and ep) else None
        label, currency = NAMES[key]

        items.append({
            "key": key,
            "name": label,
            "currency": currency,
            "open": bool(open_),
            "price": price,
            "entry": ep if open_ else None,
            "entry_date": ed if open_ else "",
            "pnl": pnl,
            "ok": True,
            # детали спецификации: терминал может показывать их, а может игнорировать
            "weight_held": held,
            "weight_target": r["target_weight"],
            "signal": int(cur["signal"]),
            "obs": str(pd.Timestamp(cur["obs"]).date()),
            "base_date": str(pd.Timestamp(cur["base_date"]).date()),
            "window": "%d %s" % (cfg.window, cfg.unit),
            "indicator": "+".join(cfg.indicator),
            "excess": round(float(cur["excess"]) * 100, 2),
            "flip_level": round(float(cur["flip_level"]), 4),
            "to_flip": round(float(cur["to_flip"]) * 100, 2),
            "weeks_in_state": r["weeks_in_state"],
            "week_closed": r["week_closed"],
        })
        # перегрев показываем там, где оверлей предусмотрен (GLD, SPY), даже когда он выключен:
        # видно, насколько далеко до порога выхода 2σ
        if cfg.tp is not None:
            try:
                z = overheat_z(r["frame"][cfg.asset], cfg.tp["lookback"], cfg.tp["vol_days"]).iloc[-1]
                items[-1]["overheat_z"] = round(float(z), 2) if pd.notna(z) else None
                items[-1]["overheat_on"] = bool(cfg.tp.get("enabled"))
            except Exception as e:
                print("    перегрев %s не посчитан: %s" % (key, e))
        print("  %-6s сигнал %d, держим %.2f, цель %.2f, excess %+.2f%%, до разворота %+.2f%%"
              % (key, cur["signal"], held, r["target_weight"],
                 float(cur["excess"]) * 100, float(cur["to_flip"]) * 100))

    # Моментум по акциям — отдельный блок, в портфель четырёх ног не входит.
    # Считается мягко: если MOEX не отдал состав индекса, четыре ноги всё равно публикуются.
    momentum = None
    try:
        print("Моментум по акциям…")
        import m2_momentum
        momentum = m2_momentum.compute()
        print("  корзина: %s" % ", ".join(x["ticker"] for x in momentum["basket"]))
        if momentum["pending_in"]:
            print("  на замену в конце недели: +%s / -%s"
                  % (",".join(momentum["pending_in"]), ",".join(momentum["pending_out"])))
    except Exception as e:
        print("  МОМЕНТУМ НЕ ПОСЧИТАН: %s: %s" % (type(e).__name__, e))

    doc = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "spec": "M2 Global Macro v1.0",
        "signals": items,
        "momentum": momentum,
        "sources": src,
    }
    with open("signals.json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    print("signals.json записан: ног %d" % len(items))


if __name__ == "__main__":
    main()
