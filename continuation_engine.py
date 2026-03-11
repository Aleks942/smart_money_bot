# continuation_engine.py

from typing import List, Dict, Optional, Any

def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return float(values[-1])

    k = 2.0 / (period + 1.0)
    ema_val = sum(values[:period]) / float(period)

    for v in values[period:]:
        ema_val = float(v) * k + ema_val * (1.0 - k)

    return float(ema_val)


def _get_close(c: Any) -> float:
    # dict: {"close": "..."}
    if isinstance(c, dict):
        return float(c["close"])
    # list/tuple: [ts, open, high, low, close, ...]
    return float(c[4])


def _get_high(c: Any) -> float:
    if isinstance(c, dict):
        return float(c["high"])
    return float(c[2])


def _get_low(c: Any) -> float:
    if isinstance(c, dict):
        return float(c["low"])
    return float(c[3])


def continuation_engine(candles_m15: List[Any]) -> Optional[str]:
    """
    Возвращает:
      CONTINUATION_UP / CONTINUATION_DOWN / None
    Логика:
      1) тренд по EMA20/50/200 на M15
      2) откат к EMA20 (цена вернулась к средней)
      3) триггер продолжения: close пробивает high/low предыдущей свечи
    """

    if not candles_m15 or len(candles_m15) < 210:
        return None

    closes = [_get_close(c) for c in candles_m15]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    price = closes[-1]

    # тренд
    up_trend = (price > ema20) and (ema20 > ema50) and (ema50 > ema200)
    dn_trend = (price < ema20) and (ema20 < ema50) and (ema50 < ema200)

    if not up_trend and not dn_trend:
        return None

    # откат: цена должна "потрогать" EMA20 (упрощённо — быть рядом/ниже/выше)
    # UP: откат = цена опускалась к EMA20 (сейчас допускаем price <= ema20)
    if up_trend and price > ema20:
        # если хочешь строже — оставь так, но тогда ловить будет реже
        return None

    # DOWN: откат = цена поднималась к EMA20 (price >= ema20)
    if dn_trend and price < ema20:
        return None

    prev = candles_m15[-2]
    prev_high = _get_high(prev)
    prev_low = _get_low(prev)
    last_close = closes[-1]

    # триггер продолжения
    if up_trend and (last_close > prev_high):
        return "CONTINUATION_UP"

    if dn_trend and (last_close < prev_low):
        return "CONTINUATION_DOWN"

    return None
