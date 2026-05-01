def retest_ok(sig: dict, m15: dict) -> dict:

    if not isinstance(sig, dict) or not isinstance(m15, dict):
        return {"ok": False, "reason": "bad_inputs"}
        
# =====================
# SIDE NORMALIZATION
# =====================
side_raw = str(sig.get("side") or "").upper()

# fallback если сигнала нет
if not side_raw:
    trigger_type = str(m15.get("trigger_type") or "").lower()

    if "long" in trigger_type or "up" in trigger_type:
        side_raw = "LONG"
    elif "short" in trigger_type or "down" in trigger_type:
        side_raw = "SHORT"

# нормализация
if "LONG" in side_raw or "BUY" in side_raw or "UP" in side_raw:
    side = "LONG"
elif "SHORT" in side_raw or "SELL" in side_raw or "DOWN" in side_raw:
    side = "SHORT"
else:
    return {"ok": False, "reason": f"bad_side_{side_raw}"}

    # =====================
    # M15 DATA
    # =====================
    close = m15.get("close")
    ema20 = m15.get("ema20")
    vwap = m15.get("vwap")
    atr = float(m15.get("atr") or 0)

    if close is None or ema20 is None or vwap is None or atr <= 0:
        return {"ok": False, "reason": "no_m15_data"}
    close = m15.get("close")
    micro = m15.get("micro_stop")
    
    if close is None or micro is None:
        return {"ok": False, "reason": "no_range"}
    
    # строим диапазон
    r_low = min(close, micro)
    r_high = max(close, micro)

    if r_low is None or r_high is None:
        return {"ok": False, "reason": "no_range"}

    tol = atr * 0.3

    # LONG
    if side in ("LONG", "BUY"):
        zone_ok = (r_high - tol) <= close <= (r_high + tol)
        structure_ok = close > ema20 and close > vwap

        if zone_ok and structure_ok:
            return {
                "ok": True,
                "entry": close,
                "stop": m15.get("micro_stop") or (r_low - tol),
                "reason": "retest_long"
            }

        return {"ok": False, "reason": "no_retest_long"}

    # SHORT
    if side in ("SHORT", "SELL"):
        zone_ok = (r_low - tol) <= close <= (r_low + tol)
        structure_ok = close < ema20 and close < vwap

        if zone_ok and structure_ok:
            return {
                "ok": True,
                "entry": close,
                "stop": m15.get("micro_stop") or (r_high + tol),
                "reason": "retest_short"
            }

        return {"ok": False, "reason": "no_retest_short"}

    return {"ok": False, "reason": "bad_side"}
