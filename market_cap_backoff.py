"""Small bounded HTTP-429 cooldown for CoinGecko market-cap lookups.

Only request pacing is affected. Missing market caps remain missing: the
existing market-cap safety filter must NOT be bypassed by this module.
"""
import math
import time
from datetime import datetime
from email.utils import parsedate_to_datetime


def remaining_seconds(cache, now=None):
    now = time.time() if now is None else float(now)
    try:
        return max(0.0, float(cache.get("cg_cooldown_until", 0)) - now)
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _retry_after_seconds(response, now):
    try:
        raw = (getattr(response, "headers", None) or {}).get("Retry-After")
        if not raw:
            return None
        try:
            seconds = float(raw)
        except (ValueError, TypeError):
            seconds = parsedate_to_datetime(raw).timestamp() - now
        return seconds if math.isfinite(seconds) and seconds > 0 else None
    except (TypeError, OverflowError, ValueError, IndexError):
        return None


def register_429(cache, response, now=None):
    now = time.time() if now is None else float(now)
    last = float(cache.get("cg_last_429", 0) or 0)
    strikes = int(cache.get("cg_429_strikes", 0) or 0)
    strikes = min(4, strikes + 1) if now - last < 600 else 1
    exponential = min(300, 60 * (2 ** (strikes - 1)))
    header_seconds = _retry_after_seconds(response, now)
    delay = min(300, max(60, exponential, header_seconds or 0))
    cache["cg_last_429"] = now
    cache["cg_429_strikes"] = strikes
    cache["cg_cooldown_until"] = now + delay
    cache["last_fail_ts"] = now
    return delay
