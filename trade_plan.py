"""Conservative trade-plan validation for Telegram presentation only.

This NEVER places orders, changes alert gates, or claims positive expectancy.
No invented stop or target. Models 0.5% account risk including round-trip
fee/slippage, without relying on a leverage assumption.
"""
import math
import os

_MIN_STOP_PCT = 0.35
_MAX_STOP_PCT = 3.5
_MIN_GROSS_RR = 1.8
_RISK_EQUITY_PCT = 0.5


def _valid_float(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _cost_bps(name, default):
    try:
        raw = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return raw if math.isfinite(raw) and 0 <= raw <= 100 else default


def classify_plan(signal):
    """Return deterministic labels and metrics, never a trading decision."""
    if not isinstance(signal, dict):
        return {"status": "WATCH", "reason": "NO_SIGNAL"}
    entry_name = str(
        signal.get("entry_type") or signal.get("entry") or ""
    ).upper()
    raw_dir = str(signal.get("direction_code") or signal.get("side") or "").upper()
    if "LONG" in entry_name or "BUY" in entry_name:
        direction = "UP"
    elif "SHORT" in entry_name or "SELL" in entry_name:
        direction = "DOWN"
    elif raw_dir in ("UP", "DOWN"):
        direction = raw_dir
    else:
        return {"status": "WATCH", "reason": "NO_DIRECTION"}

    if entry_name in ("NO_ENTRY", "WAIT", "NONE") or "NO_ENTRY" in entry_name:
        return {"status": "WATCH", "reason": "NO_ENTRY"}

    entry = _valid_float(signal.get("entry_price", signal.get("price")))
    stop = _valid_float(signal.get("stop"))
    tp_source = "TP1" if signal.get("tp1") is not None else "TARGET"
    tp = _valid_float(signal.get("tp1") if tp_source == "TP1" else signal.get("target"))
    if entry is None or stop is None or tp is None:
        return {"status": "WATCH", "reason": "MISSING_ENTRY_STOP_TARGET"}

    if not ((direction == "UP" and stop < entry < tp)
            or (direction == "DOWN" and tp < entry < stop)):
        return {"status": "WATCH", "reason": "WRONG_SIDE_LEVELS"}

    risk_pct = abs(entry - stop) / entry * 100.0
    reward_pct = abs(entry - tp) / entry * 100.0
    rr = reward_pct / risk_pct
    costs_pct = 2 * (
        _cost_bps("SHADOW_FEE_BPS_SIDE", 6.0)
        + _cost_bps("SHADOW_SLIPPAGE_BPS_SIDE", 5.0)
    ) / 100.0
    position_pct = _RISK_EQUITY_PCT / (risk_pct + costs_pct) * 100.0
    net_win_pct = reward_pct - costs_pct
    net_loss_pct = -risk_pct - costs_pct
    net_rr = net_win_pct / -net_loss_pct

    result = {
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "tp_source": tp_source,
        "stop_pct": round(risk_pct, 4),
        "reward_pct": round(reward_pct, 4),
        "gross_rr": round(rr, 3),
        "net_rr": round(net_rr, 3),
        "estimated_cost_pct": round(costs_pct, 4),
        "position_notional_pct_of_equity": round(position_pct, 2),
        "risk_pct_of_equity": _RISK_EQUITY_PCT,
    }
    if risk_pct < _MIN_STOP_PCT:
        return {**result, "status": "WATCH", "reason": "STOP_TOO_TIGHT"}
    if risk_pct > _MAX_STOP_PCT:
        return {**result, "status": "WATCH", "reason": "STOP_TOO_WIDE"}
    if rr < _MIN_GROSS_RR or net_win_pct <= 0 or net_rr < 1.2:
        return {**result, "status": "WATCH", "reason": "INSUFFICIENT_NET_REWARD"}
    return {**result, "status": "PLAN_VALID", "reason": "LEVELS_AND_RISK_VALID"}


def telegram_plan(sig):
    """One concise, consistent block for each scanner Telegram alert."""
    info = classify_plan(sig)
    if info["status"] != "PLAN_VALID":
        reason = str(info["reason"])
        note = (
            f"⚪ <b>НАБЛЮДЕНИЕ — НЕ ГОТОВЫЙ ВХОД</b> "
            f"(план: {reason})"
        )
        if "stop_pct" in info:
            note += (
                f"\nСтоп {info['stop_pct']:.2f}% | "
                f"RR {info['gross_rr']:.2f}"
            )
        return note

    return (
        f"📋 <b>ПЛАН УРОВНЕЙ (НЕ ТОРГОВЫЙ ОРДЕР)</b>\n"
        f"Направление: {info['direction']} | "
        f"Вход: {info['entry']:.8g}\n"
        f"SL: {info['stop']:.8g} ({info['stop_pct']:.2f}%) | "
        f"{info['tp_source']}: {info['tp']:.8g} "
        f"(+{info['reward_pct']:.2f}% по направлению)\n"
        f"RR: {info['gross_rr']:.2f} | "
        f"после условных расходов: {info['net_rr']:.2f}R\n"
        f"Для риска 0,5% капитала: "
        f"условный номинал {info['position_notional_pct_of_equity']:.1f}% капитала "
        f"(без учёта гэпов/ликвидаций)\n"
        f"⚠️ Уровни технически корректны; прибыльность не подтверждена."
    )
