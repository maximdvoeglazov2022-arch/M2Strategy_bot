"""
M2 Advisory · Global Macro — эталонный расчёт сигналов (4 ноги).
Зависимости: pandas, numpy. Вход: дневные ряды закрытий (pd.Series, DatetimeIndex, по возрастанию).
Логика 1-в-1 совпадает с бэктестами (PDF-отчёты 05–08.11.2026).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np, pandas as pd

# ----------------------------------------------------------------------------- конфигурация ног
@dataclass
class LegConfig:
    key: str                    # 'IMOEX' | 'USD' | 'GLD' | 'SPY'
    asset: str                  # имя ряда актива
    indicator: dict             # {имя ряда: вес} — доходность индикатора = Σ вес × доходность ряда
    window: int                 # длина окна
    unit: str                   # 'trading' (строки торгового календаря) | 'calendar' (календарные дни) | 'weeks'
    calendar: str               # 'US' — календарь ряда актива; 'MOEX' — будни, когда торговался IMOEX
    weight: float = 0.25        # доля ноги в портфеле
    trim: dict | None = None    # оверлей частичной фиксации 75% (опционально)
    tp: dict | None = None      # оверлей «выход при перегреве 2σ / возврат ниже 1σ» (опционально)

TP_DEFAULT   = dict(enabled=False, lookback=60, vol_days=63, exit_z=2.0, reentry_z=1.0)
TRIM_DEFAULT = dict(enabled=False, keep=0.25, anchor_days=252, threshold=0.26)

# Рабочая конфигурация = лучшие варианты бэктестов (08.11.2026)
LEGS = {
    'IMOEX': LegConfig('IMOEX', 'IMOEX', {'RVI': 1.0},             90,  'calendar', 'MOEX'),
    'USD':   LegConfig('USD',   'USDRUB', {'RUGBITR1Y': 1.0},      35,  'calendar', 'MOEX', trim=dict(TRIM_DEFAULT)),
    'GLD':   LegConfig('GLD',   'GLD',   {'YCS': 1.0},             130, 'trading',  'US',   tp=dict(TP_DEFAULT)),
    'SPY':   LegConfig('SPY',   'SPY',   {'IEF': 0.5, 'UUP': 0.5}, 120, 'trading',  'US',   tp=dict(TP_DEFAULT)),
}
# Прежние (живые) окна для сравнения: USD 21 'calendar'; GLD и SPY 21 'trading'; отслеживаемая пара 120/120 'trading'.
# Оверлеи выключены по умолчанию; включение — LEGS['USD'].trim['enabled']=True и/или LEGS['GLD'].tp['enabled']=True.
COST_PER_SIDE = 0.001          # издержки бэктеста, 0.1% от оборота (в сигнале не участвуют)

# ----------------------------------------------------------------------------- подготовка данных
def prep_usdrub_cbr(s: pd.Series) -> pd.Series:
    """Курс ЦБ на дату D публикуется накануне → сдвигаем индекс на −1 календарный день (дата публикации)."""
    s = s.dropna().copy(); s.index = s.index - pd.Timedelta(days=1); return s.sort_index()

def moex_calendar(imoex: pd.Series) -> pd.DatetimeIndex:
    """Торговый календарь MOEX: даты IMOEX, только будни (выходные сессии исключаются)."""
    ix = imoex.dropna().index; return ix[ix.dayofweek < 5]

def frame_for_leg(cfg: LegConfig, data: dict[str, pd.Series]) -> pd.DataFrame:
    """Таблица asset + индикаторы на календаре ноги, пропуски заполняются последним значением (ffill)."""
    cols = [cfg.asset] + list(cfg.indicator)
    if cfg.calendar == 'MOEX':
        cal = moex_calendar(data['IMOEX'])
        last = max(data[c].dropna().index.max() for c in cols)          # курс ЦБ может быть «на день вперёд»
        if last > cal[-1] and last.dayofweek < 5: cal = cal.append(pd.DatetimeIndex([last]))
        df = pd.concat({c: data[c] for c in cols}, axis=1).reindex(cal.union(pd.concat({c: data[c] for c in cols}, axis=1).index)).ffill().reindex(cal)
    else:  # US: календарь = даты актива
        cal = data[cfg.asset].dropna().index
        df = pd.concat({c: data[c] for c in cols}, axis=1).reindex(cal.union(pd.concat({c: data[c] for c in cols}, axis=1).index)).ffill().reindex(cal)
    return df.dropna()

def weekly_obs(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Даты наблюдения: последний торговый день каждой недели (неделя пн–пт, 'W-FRI')."""
    s = pd.Series(index, index=index); return pd.DatetimeIndex(s.groupby(index.to_period('W-FRI')).last().values)

# ----------------------------------------------------------------------------- база окна
def base_position(df: pd.DataFrame, obs: pd.Timestamp, cfg: LegConfig) -> int | None:
    i = df.index.get_loc(obs)
    if cfg.unit == 'trading':
        j = i - cfg.window
    elif cfg.unit == 'calendar':
        j = df.index.searchsorted(obs - pd.Timedelta(days=cfg.window), side='right') - 1
    elif cfg.unit == 'weeks':
        ob = weekly_obs(df.index); k = ob.get_loc(obs) - cfg.window
        return None if k < 0 else df.index.get_loc(ob[k])
    else:
        raise ValueError(cfg.unit)
    return None if j < 0 else int(j)

# ----------------------------------------------------------------------------- сигнал
def leg_signal(cfg: LegConfig, df: pd.DataFrame, obs: pd.Timestamp) -> dict | None:
    """Сигнал ноги на дату obs (обычно последний торговый день недели или последняя дата для предварительного сигнала)."""
    j = base_position(df, obs, cfg)
    if j is None: return None
    w, b = df.loc[obs], df.iloc[j]
    r_asset = w[cfg.asset] / b[cfg.asset] - 1
    r_ind = sum(wt * (w[c] / b[c] - 1) for c, wt in cfg.indicator.items())
    excess = r_asset - r_ind
    flip = b[cfg.asset] * (1 + r_ind)          # цена актива, при которой excess = 0 (при неизменном индикаторе)
    return dict(leg=cfg.key, obs=obs, base_date=df.index[j], asset=w[cfg.asset], asset_base=b[cfg.asset],
                r_asset=r_asset, r_indicator=r_ind, excess=excess, signal=int(excess > 0),
                flip_level=flip, to_flip=flip / w[cfg.asset] - 1)

def signal_history(cfg: LegConfig, df: pd.DataFrame) -> pd.DataFrame:
    rows = [leg_signal(cfg, df, o) for o in weekly_obs(df.index)]
    return pd.DataFrame([r for r in rows if r]).set_index('obs')

# ----------------------------------------------------------------------------- оверлей фиксации 75% (USD, опционально)
def overheat_z(P: pd.Series, lookback: int = 60, vol_days: int = 63) -> pd.Series:
    """Перегрев: рост от минимума за lookback строк в единицах σ, σ = дневная волатильность за vol_days × √lookback."""
    sd = P.pct_change().rolling(vol_days).std()
    mn = P.rolling(lookback, min_periods=lookback).min()
    return (P / mn - 1) / (sd * np.sqrt(lookback))

def target_weights(cfg: LegConfig, df: pd.DataFrame, hist: pd.DataFrame) -> pd.Series:
    """Целевая доля ноги (0 / keep / 1) по неделям.
    База: 1, если signal=1, иначе 0.
    tp (если включён): на дате наблюдения при доле 1 и z ≥ exit_z → доля 0 и блокировка; блокировка снимается на дате
        наблюдения, когда z < reentry_z; пока блок снят — доля снова равна сигналу.
    trim (если включён): при доле 1 и росте от минимума за anchor_days ≥ threshold (максимум с минимума обновлён за 5 строк,
        от минимума ≥ 5 строк) → доля keep; остаток держится до выхода по сигналу; новый вход — 100%."""
    t = cfg.trim if (cfg.trim and cfg.trim.get('enabled')) else None
    tp = cfg.tp if (cfg.tp and cfg.tp.get('enabled')) else None
    P = df[cfg.asset]; out = {}; cur = 0.0; trimmed = False; blocked = False
    Z = overheat_z(P, tp['lookback'], tp['vol_days']) if tp else None
    for obs, row in hist.iterrows():
        if tp:
            z = Z.loc[obs]
            if cur == 1.0 and np.isfinite(z) and z > tp['exit_z']: cur, blocked = 0.0, True
            elif blocked and np.isfinite(z) and z < tp['reentry_z']: blocked = False
            if not blocked: cur = float(row.signal)
            out[obs] = cur; continue
        if row.signal == 0: cur, trimmed = 0.0, False
        elif cur == 0.0: cur, trimmed = 1.0, False
        if t and cur == 1.0 and not trimmed:
            i = P.index.get_loc(obs); seg = P.iloc[max(0, i - t['anchor_days'] + 1): i + 1]
            jmin = int(np.argmin(seg.values)); mn = seg.iloc[jmin]; since = seg.iloc[jmin:]
            rise = P.iloc[i] / mn - 1; k = len(seg) - 1 - jmin
            new_high = P.iloc[max(0, i - 4): i + 1].max() >= since.max() - 1e-12
            if rise >= t['threshold'] and new_high and k >= 5: cur, trimmed = t['keep'], True
        out[obs] = cur
    return pd.Series(out, name=cfg.key)

# ----------------------------------------------------------------------------- бэктест-доходность ноги (для сверки)
def leg_returns(cfg: LegConfig, df: pd.DataFrame, weights: pd.Series, daily: bool = True) -> pd.Series:
    """Доля, решённая на закрытии недели obs, действует со следующего торгового дня."""
    w = weights.reindex(df.index).ffill().fillna(0).shift(1)
    r = df[cfg.asset].pct_change()
    return (w * r - w.diff().abs().fillna(0) * COST_PER_SIDE).fillna(0)

def compute_all(data: dict[str, pd.Series], legs: dict[str, LegConfig] = LEGS, asof: pd.Timestamp | None = None) -> dict:
    """Главная точка входа для терминала.
    data: {'IMOEX','RVI','USDRUB_CBR' (курс ЦБ по дате установления) или 'USDRUB' (уже сдвинутый),'RUGBITR1Y','GLD','YCS','SPY','IEF','UUP'}.
    Возвращает по каждой ноге:
      current      — сигнал по последней доступной дате (на неделе — предварительный; в пятницу после закрытия — итоговый);
      held_weight  — доля, которая держится на этой неделе (решена на закрытии прошлой недели);
      target_weight— доля по текущему сигналу (вступит в силу со следующего торгового дня после закрытия недели);
      week_closed  — True, если последняя дата — пятница (итог недели); праздничную пятницу терминал должен определить по календарю биржи."""
    data = dict(data)
    if 'USDRUB_CBR' in data: data['USDRUB'] = prep_usdrub_cbr(data['USDRUB_CBR'])
    res = {}
    for k, cfg in legs.items():
        df = frame_for_leg(cfg, data)
        if asof is not None: df = df.loc[:asof]
        hist = signal_history(cfg, df); tw = target_weights(cfg, df, hist)
        res[k] = dict(current=hist.iloc[-1].to_dict() | {'obs': hist.index[-1]}, target_weight=float(tw.iloc[-1]),
                      held_weight=float(tw.iloc[-2]) if len(tw) > 1 else 0.0, week_closed=bool(df.index[-1].dayofweek == 4),
                      weeks_in_state=int((hist.signal[::-1] == hist.signal.iloc[-1]).cumprod().sum()),
                      history=hist, weights=tw, frame=df)
    return res
