# -*- coding: utf-8 -*-
"""
Публикация сигналов для терминала MacroScope.
Запускается GitHub Actions по расписанию. Берёт расчёт из strategy_bot.py,
сохраняет signals.json рядом с state.json.
"""
import json, datetime
from strategy_bot import calc_all, load_state

NAMES = {
    "IMOEX": ("Индекс МосБиржи", ""),
    "USD":   ("Фьючерс USD/RUB", ""),
    "SPY":   ("S&P 500", "$"),
    "GLD":   ("Золото", "$"),
}
ORDER = ["IMOEX", "USD", "SPY", "GLD"]


def main():
    r = calc_all()
    st = r["state"]
    sig = {"IMOEX": r["sig_imoex"], "USD": r["sig_usd"],
           "SPY": r["sig_spy"], "GLD": r["sig_gld"]}
    price = {"IMOEX": r["p_imoex"], "USD": r["p_usd"],
             "SPY": r["p_spy"], "GLD": r["p_gld"]}

    items = []
    for k in ORDER:
        label, cur = NAMES[k]
        open_ = sig[k] != "CASH"
        entry = st.get(f"{k}_entry")
        edate = st.get(f"{k}_entry_date", "")
        pnl = None
        if open_ and entry and price[k]:
            pnl = round((price[k] / entry - 1) * 100, 2)
        items.append({
            "key": k,
            "name": label,
            "currency": cur,
            "open": open_,
            "price": round(price[k], 2) if price[k] else None,
            "entry": round(entry, 2) if (open_ and entry) else None,
            "entry_date": edate if open_ else "",
            "pnl": pnl,
            "ok": bool(r["data_ok"].get("IMOEX" if k == "IMOEX" else
                                        "USD" if k == "USD" else k, False)),
        })

    out = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "signals": items,
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("signals.json записан:", out["updated"])
    for it in items:
        print(f"  {it['name']:20s} {'ОТКРЫТА' if it['open'] else 'вне позиции':12s} "
              f"{it['price'] if it['price'] else '—'}")


if __name__ == "__main__":
    main()
