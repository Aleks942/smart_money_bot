# priority_engine.py

from typing import Dict, List, Optional
import time


LAST_PRIORITY_SYMBOL = None
LAST_PRIORITY_TS = 0

PRIORITY_COOLDOWN_SEC = 60 * 15  # 15 минут


def compute_priority(coin: Dict) -> int:
    """
    coin пример:
    {
        "symbol": "FIL",
        "score": 7,
        "stage": "EXPANSION",
        "flags": ["VOL_SPIKE", "ATR_EXPANSION", "PRESSURE_UP"]
    }
    """

    priority = coin.get("score", 0)

    stage = coin.get("stage", "")
    flags = coin.get("flags", [])

    # stage
    if stage == "EXPANSION":
        priority += 3
    elif stage == "MANIPULATION":
        priority += 2
    elif stage == "ACCUMULATION":
        priority += 1

    # структура движения
    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        priority += 2

    if "VOL_SPIKE" in flags:
        priority += 2

    if "ATR_EXPANSION" in flags:
        priority += 1

    # давление
    if "PRESSURE_UP" in flags or "PRESSURE_DOWN" in flags:
        priority += 1

    return priority


def find_global_priority(coins: List[Dict]) -> Optional[Dict]:

    if not coins:
        return None

    best = None
    best_score = -1

    for coin in coins:

        priority = compute_priority(coin)
        coin["priority"] = priority

        if priority > best_score:
            best = coin
            best_score = priority

    return best


def should_send_priority(symbol: str) -> bool:

    global LAST_PRIORITY_SYMBOL
    global LAST_PRIORITY_TS

    now = time.time()

    if symbol == LAST_PRIORITY_SYMBOL:
        return False

    if now - LAST_PRIORITY_TS < PRIORITY_COOLDOWN_SEC:
        return False

    LAST_PRIORITY_SYMBOL = symbol
    LAST_PRIORITY_TS = now

    return True
