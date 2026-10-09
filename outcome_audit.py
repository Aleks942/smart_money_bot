"""Non-trading, read-only 1-minute candle audit of legacy SmartMoney outcomes.

The entry minute is excluded because a candle cannot prove whether a level
was touched before or after the signal. Both levels within one candle are
always ambiguous; no assumed intraminute price path.
"""
import math
import time

import requests


def first_touch_from_1m(candles, entry_price, direction, created_at, checked_at):
    """Return TP_FIRST / SL_FIRST / SAME_MINUTE / NO_TOUCH or uncertain status."""
    base = {"status": "INVALID_INPUT", "bars": 0, "first_ts": None,
            "mfe_pct": None, "mae_pct": None}
    try:
        price = float(entry_price)
        created = int(created_at)
        checked = int(checked_at)
        side = str(direction).upper()
        if not math.isfinite(price) or price <= 0 or side not in ("UP", "DOWN"):
            return base
        if checked <= created:
            return {**base, "status": "NOT_READY"}
        # Only complete candles *after* the signal minute can be trusted.
        entry_ms = (created // 60) * 60000
        first_ms = (created // 60 + 1) * 60000
        last_ms = (checked // 60 - 1) * 60000
        if last_ms < first_ms:
            return {**base, "status": "NOT_READY"}

        if side == "UP":
            tp, sl = price * 1.01, price * 0.99
        else:
            tp, sl = price * 0.99, price * 1.01

        entry_bar = None
        ordered = []
        for raw in candles or ():
            try:
                ts = int(raw[0])
                hi, lo = float(raw[2]), float(raw[3])
                if not (math.isfinite(hi) and math.isfinite(lo) and 0 < lo <= hi):
                    continue
            except (TypeError, ValueError, IndexError):
                continue
            if ts == entry_ms:
                entry_bar = (hi, lo)
            if first_ms <= ts <= last_ms:
                ordered.append((ts, hi, lo))

        # The entry-minute OHLC includes trades before and after the
        # alert timestamp. A touched boundary cannot be ordered reliably.
        if entry_bar is None:
            return {**base, "status": "INCOMPLETE_HISTORY"}
        entry_high, entry_low = entry_bar
        if side == "UP":
            entry_may_touch = entry_high >= tp or entry_low <= sl
        else:
            entry_may_touch = entry_low <= tp or entry_high >= sl
        if entry_may_touch:
            return {**base, "status": "ENTRY_MINUTE_AMBIGUOUS",
                    "first_ts": entry_ms // 1000}

        ordered.sort(key=lambda x: x[0])
        if not ordered or ordered[0][0] != first_ms:
            return {**base, "status": "INCOMPLETE_HISTORY"}

        next_ms = first_ms
        mfe = mae = 0.0
        bars = 0
        for ts, high, low in ordered:
            if ts < next_ms:
                continue  # duplicate candle
            if ts != next_ms:
                return {**base, "status": "DATA_GAP", "bars": bars}
            next_ms += 60000
            bars += 1

            if side == "UP":
                mfe = max(mfe, (high / price - 1.0) * 100.0)
                mae = min(mae, (low / price - 1.0) * 100.0)
                tp_hit, sl_hit = high >= tp, low <= sl
            else:
                mfe = max(mfe, (1.0 - low / price) * 100.0)
                mae = min(mae, (1.0 - high / price) * 100.0)
                tp_hit, sl_hit = low <= tp, high >= sl

            if tp_hit or sl_hit:
                status = ("SAME_MINUTE" if tp_hit and sl_hit
                          else "TP_FIRST" if tp_hit else "SL_FIRST")
                return {"status": status, "bars": bars, "first_ts": ts // 1000,
                        "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4)}

        if next_ms <= last_ms:
            return {**base, "status": "INCOMPLETE_HISTORY", "bars": bars}
        return {"status": "NO_TOUCH", "bars": bars, "first_ts": None,
                "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4)}
    except (ValueError, TypeError, OverflowError):
        return base


def audit_bybit_1m(symbol, entry_price, direction, created_at,
                   checked_at=None, timeout=4):
    """One bounded best-effort request; NEVER controls dispatch or legacy stats."""
    checked = int(checked_at if checked_at is not None else time.time())
    created = int(created_at)
    age_minutes = max(0, (checked - created) // 60)
    if age_minutes > 980:
        return {"status": "HISTORY_WINDOW_EXCEEDED"}
    try:
        response = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "1",
                    "limit": str(min(1000, max(60, age_minutes + 5)))},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") != 0:
            return {"status": "FETCH_ERROR", "reason": str(payload.get("retMsg", ""))[:100]}
        rows = (payload.get("result") or {}).get("list") or []
        return first_touch_from_1m(rows, entry_price, direction, created, checked)
    except Exception as exc:
        return {"status": "FETCH_ERROR", "reason": f"{type(exc).__name__}: {str(exc)[:100]}"}
