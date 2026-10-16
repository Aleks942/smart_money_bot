"""Ready technical trade plans. No performance claims and no order placement."""
import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_LOCK = threading.Lock()

def _num(x):
    try:
        n = float(x)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None

def assess(sig, trend):
    """Reject missing prices, contradictory direction, weak structure or risk."""
    if not isinstance(sig, dict):
        return None
    symbol = str(sig.get("instId") or sig.get("symbol") or "")
    side = str(sig.get("direction_code") or "")
    if not symbol.endswith("USDT") or side not in ("UP", "DOWN"):
        return None
    if trend not in (side, side + "_H4_FLAT"):
        return None
    entry_name = str(sig.get("entry_type") or sig.get("entry") or "").upper()
    direction_word = "LONG" if side == "UP" else "SHORT"
    if direction_word not in entry_name:
        return None
    score = _num(sig.get("score"))
    acc = _num(sig.get("acc_score"))
    ep = _num(sig.get("early_pressure_score"))
    if (score is None or score < 18 or acc is None or ep is None
            or (acc < 2 and ep < 10)):
        return None
    if trend.endswith("H4_FLAT") and score < 22:
        return None
    if sig.get("late") is True:
        return None
    if sig.get("signal_mode") not in ("PREMOVE", "TRANSITION", "EXPANSION"):
        return None
    flags = set(sig.get("flags") or [])
    if side == "UP":
        pressure = {"PRESSURE_UP", "BUYER_ABSORPTION"}
        structure = {"STRUCTURE_HH_HL", "MTF_LONG_ALIGN",
                     "BREAKOUT_CONFIRM_UP", "CONTINUATION_UP"}
        contrary = {"PRESSURE_DOWN", "BREAKOUT_CONFIRM_DOWN"}
    else:
        pressure = {"PRESSURE_DOWN", "SELLER_ABSORPTION"}
        structure = {"STRUCTURE_LH_LL", "MTF_SHORT_ALIGN",
                     "BREAKOUT_CONFIRM_DOWN", "CONTINUATION_DOWN"}
        contrary = {"PRESSURE_UP", "BREAKOUT_CONFIRM_UP"}
    if (not pressure.intersection(flags) or not structure.intersection(flags)
            or contrary.intersection(flags)):
        return None
    entry = _num(sig.get("entry_price"))
    stop = _num(sig.get("stop"))
    price = _num(sig.get("price"))
    oi = _num(sig.get("oi_change"))
    if any(v is None or v <= 0 for v in (entry, stop, price)) or oi is None:
        return None
    if oi < -0.3 or abs(price/entry-1) > .008:
        return None
    sign = 1 if side == "UP" else -1
    risk = sign * (entry - stop)
    if risk <= 0 or not .35 <= 100*risk/entry <= 2.5:
        return None
    # Targets are explicitly calculated at 2R/3R, not claimed as
    # independently verified liquidity or structure levels.
    return {"symbol": symbol, "side": direction_word,
            "entry": entry, "stop": stop, "tp1": entry+sign*2*risk,
            "tp2": entry+sign*3*risk, "score": score,
            "risk_pct": 100*risk/entry, "oi": oi, "trend": trend}

def message(p):
    return "\n".join((
        "SMART MONEY | ТЕХНИЧЕСКИЙ ПЛАН " + p["side"],
        p["symbol"] + " | Bybit USDT perpetual",
        "Вход: {:.9g} | Stop: {:.9g}".format(p["entry"], p["stop"]),
        "TP1 (2R): {:.9g} | TP2 (3R): {:.9g}".format(p["tp1"], p["tp2"]),
        "Риск до стопа: {:.2f}% | Score: {:.0f}".format(p["risk_pct"], p["score"]),
        "OI 5m: {:+.3f}% | Тренд: {}".format(p["oi"], p["trend"]),
        "Технические условия проверены. Положительное",
        "статистическое преимущество пока НЕ подтверждено.",
        "Не автоматическая сделка. Проверяй цену и ликвидность."
    ))

def dispatch(p, send, now=None):
    """At most five per Warsaw day, 45m global gap, 12h per symbol."""
    now = time.time() if now is None else float(now)
    if not callable(send):
        return False
    path = Path(os.getenv("SMARTMONEY_READY_STATE_FILE",
                           "/data/smartmoney_ready_state.json"))
    with _LOCK:
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(state, dict):
                return False
        except (ValueError, OSError):
            return False
        day = datetime.fromtimestamp(now, ZoneInfo("Europe/Warsaw")).date().isoformat()
        if state.get("day") != day:
            state = {"day": day, "count": 0, "last": 0, "symbols": {}}
        if state["count"] >= 5 or now-state["last"] < 2700:
            return False
        if now - state["symbols"].get(p["symbol"], 0) < 43200:
            return False
        try:
            accepted = send(message(p))
        except Exception:
            return False
        if accepted is not True:
            return False
        state["count"] += 1
        state["last"] = now
        state["symbols"][p["symbol"]] = now
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, path)
        except OSError as exc:
            print("[SM_READY_PERSIST_ERROR]", type(exc).__name__, flush=True)
        print("[SM_READY_SENT]", p["symbol"], p["side"],
              "day_count=", state["count"], "edge_proven=false", flush=True)
        return True
