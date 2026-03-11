# continuation_engine.py

from typing import List, Dict, Optional


def ema(values: List[float], period: int) -> float:
    if not values or len(values) < period:
        return values[-1] if values else 0.0

    k = 2 / (period + 1)
    ema_val = sum(values[:period]) / period

    for v in values[period:]:
        ema_val = v * k + ema_val * (1 - k)

    return ema_val


def trend_m15(ema20: float, ema50: float, ema200: float) -> str:

    if ema20 > ema50 > ema200:
        return "UP"

    if ema20 < ema50 < ema200:
        return "DOWN"

    return "RANGE"


def pullback_detect(price: float, ema20: float, ema50: float, trend: str) -> bool:

    if trend == "UP":
        if price <= ema20 or price <= ema50:
            return True

    if trend == "DOWN":
        if price >= ema20 or price >= ema50:
            return True

    return False


def continuation_trigger(last_close: float,
                         prev_high: float,
                         prev_low: float,
                         trend: str) -> bool:

    if trend == "UP":
        if last_close > prev_high:
            return True

    if trend == "DOWN":
        if last_close < prev_low:
            return True

    return False


def continuation_engine(candles_m15: List[Dict]) -> Optional[str]:
    """
    candles_m15: список свечей M15
    формат как у Bybit:
    {
        "open": ...,
        "high": ...,
        "low": ...,
        "close": ...
    }
    """

    if len(candles_m15) < 220:
        return None

    closes = [float(c["close"]) for c in candles_m15]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    trend = trend_m15(ema20, ema50, ema200)

    if trend == "RANGE":
        return None

    price = closes[-1]

    pullback = pullback_detect(price, ema20, ema50, trend)

    if not pullback:
        return None

    last_close = closes[-1]
    prev_high = float(candles_m15[-2]["high"])
    prev_low = float(candles_m15[-2]["low"])

    trigger = continuation_trigger(last_close, prev_high, prev_low, trend)

    if trigger:

        if trend == "UP":
            return "CONTINUATION_UP"

        if trend == "DOWN":
            return "CONTINUATION_DOWN"

    return None
