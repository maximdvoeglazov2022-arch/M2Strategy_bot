# -*- coding: utf-8 -*-
"""
Дополняет macro.json рядами, которые считаются на сервере.
Запускается в GitHub Actions, поэтому ограничений браузера по CORS нет.

Что делает:
  1. Читает macro.json (если файла нет — создаёт).
  2. Тянёт Brent с Yahoo Finance и сворачивает в цену на конец месяца.
  3. Тянёт индексы МосБиржи (акции, облигации, волатильность) и курс доллара
     Банка России — тоже на конец месяца.
  4. Пишет значения в macro.json за месяцы начиная с FROM_MONTH.
  5. Обновляет поле updated.

Что НЕ делает: не трогает показатели, которые вносятся руками
(PMI, индекс потребительских цен, ставка, инфляционные ожидания,
безработица, кредитный импульс, деловой климат). Если такое значение
уже стоит в файле, скрипт его не меняет.

Смысл пункта 3: терминал и сам умеет подтягивать МосБиржу напрямую,
но в закрытых корпоративных сетях этот запрос может не пройти.
Снимок в macro.json страхует от такого случая.
"""
import json
import os
import datetime
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "macro.json")

FROM_MONTH = "2026-01"          # раньше этого месяца ничего не пишем
MANUAL = ("pmiM", "pmiS", "cpiIdx", "rate", "infexp", "unemp", "credimp", "ibk")

UA = {"User-Agent": "Mozilla/5.0"}
YAHOO = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         "BZ=F?interval=1d&range=2y")
MOEX = ("https://iss.moex.com/iss/engines/stock/markets/index/securities/"
        "{sec}/candles.json?from={frm}&interval=24&iss.meta=off")
CBR = "https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={d1}&date_req2={d2}&VAL_NM_RQ=R01235"

INDEXES = [("imoex", "IMOEX"), ("rgbitr", "RGBITR"), ("rvi", "RVI")]


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def month_end(pairs):
    """pairs = [(дата, значение)] -> {'2026-09': значение последнего дня месяца}"""
    best = {}
    for d, v in pairs:
        if v is None:
            continue
        m = d[:7]
        if m not in best or d >= best[m][0]:
            best[m] = (d, round(float(v), 4))
    return {m: v[1] for m, v in best.items()}


def brent():
    js = json.loads(fetch(YAHOO).decode("utf-8"))
    res = js["chart"]["result"][0]
    ts = res["timestamp"]
    cl = res["indicators"]["quote"][0]["close"]
    pairs = []
    for t, c in zip(ts, cl):
        d = datetime.datetime.utcfromtimestamp(t).date().isoformat()
        pairs.append((d, c))
    return month_end(pairs)


def moex(sec, frm):
    js = json.loads(fetch(MOEX.format(sec=sec, frm=frm)).decode("utf-8"))
    c = js["candles"]
    ci = c["columns"].index("close")
    ti = c["columns"].index("end") if "end" in c["columns"] else c["columns"].index("begin")
    return month_end([(str(r[ti])[:10], r[ci]) for r in c["data"]])


def usd_cbr(frm):
    import xml.etree.ElementTree as ET
    d1 = datetime.date.fromisoformat(frm + "-01").strftime("%d/%m/%Y")
    d2 = datetime.date.today().strftime("%d/%m/%Y")
    root = ET.fromstring(fetch(CBR.format(d1=d1, d2=d2)))
    pairs = []
    for rec in root.findall("Record"):
        dt = rec.get("Date")                       # ДД.ММ.ГГГГ
        val = rec.findtext("Value", "").replace(",", ".")
        nom = float(rec.findtext("Nominal", "1") or 1)
        if not dt or not val:
            continue
        d, m, y = dt.split(".")
        pairs.append(("%s-%s-%s" % (y, m, d), float(val) / nom))
    return month_end(pairs)


def main():
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as f:
            doc = json.load(f)
    else:
        doc = {"data": {}}
    data = doc.setdefault("data", {})

    jobs = [("oil", brent)]
    for key, sec in INDEXES:
        jobs.append((key, (lambda s=sec: moex(s, FROM_MONTH))))
    jobs.append(("usd", lambda: usd_cbr(FROM_MONTH)))

    changed = 0
    for key, fn in jobs:
        try:
            got = fn()
        except Exception as e:
            print("  %-8s не загрузился: %s" % (key, e))
            continue
        n = 0
        for month in sorted(got):
            if month < FROM_MONTH:
                continue
            row = data.setdefault(month, {})
            if key in MANUAL and row.get(key) is not None:
                continue
            if row.get(key) != got[month]:
                row[key] = got[month]
                n += 1
        changed += n
        last = max(got) if got else "-"
        print("  %-8s месяцев %d, последний %s, записано %d"
              % (key, len(got), last, n))

    doc["updated"] = datetime.date.today().isoformat()
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
    print("macro.json: изменений %d, месяцев в файле %d" % (changed, len(data)))


if __name__ == "__main__":
    main()
