# continuation_engine.py

from typing import List, Optional, Any


def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return float(values[-1])

    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / float(period)

    for v in values[period:]:
        e = float(v) * k + e * (1.0 - k)

    return float(e)


def continuation_engine(candles_m15: List[Any]) -> Optional[str]:
    """
    candles_m15: свечи как у Bybit:
    [ts, open, high, low, close, volume, turnover]
    return: CONTINUATION_UP / CONTINUATION_DOWN / None
    """

    if not candles_m15 or len(candles_m15) < 210:
        return None

    closes = [float(c[4]) for c in candles_m15]
    highs = [float(c[2]) for c in candles_m15]
    lows  = [float(c[3]) for c in candles_m15]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    last_close = closes[-1]

    # 1) Тренд по M15 (без привязки к price>ema20 — так корректнее)
    up_trend = (ema20 > ema50 > ema200)
    dn_trend = (ema20 < ema50 < ema200)

    if not up_trend and not dn_trend:
        return None

    # 2) Был откат к EMA20 в последние N свечей
    LOOKBACK = 8
    pullback_margin = 0.002  # 0.2% допуск

    recent_lows = lows[-LOOKBACK:]
    recent_highs = highs[-LOOKBACK:]

    touched_ema20_up = (min(recent_lows) <= ema20 * (1.0 + pullback_margin))
    touched_ema20_dn = (max(recent_highs) >= ema20 * (1.0 - pullback_margin))

    # 3) Триггер продолжения: пробой локального high/low после отката
    TRIG = 3
    prev_swing_high = max(highs[-(TRIG+1):-1])
    prev_swing_low  = min(lows[-(TRIG+1):-1])

    if up_trend and touched_ema20_up:
        # подтверждение: цена снова выше EMA20 и пробила локальный high
        if (last_close > ema20) and (last_close > prev_swing_high):
            return "CONTINUATION_UP"

    if dn_trend and touched_ema20_dn:
        # подтверждение: цена снова ниже EMA20 и пробила локальный low
        if (last_close < ema20) and (last_close < prev_swing_low):
            return "CONTINUATION_DOWN"

    return None
