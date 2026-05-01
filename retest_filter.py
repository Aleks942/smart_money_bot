def retest_ok(sig: dict, m15: dict) -> dict:

    if not isinstance(sig, dict) or not isinstance(m15, dict):
        return {"ok": False, "reason": "bad_inputs"}

    # =====================
    # SIDE NORMALIZATION
    # =====================
    
    side_raw = (
        str(sig.get("side") or "")
        or str(sig.get("signal") or "")
        or str(sig.get("direction") or "")
    ).upper()
    
    # fallback 1
    if not side_raw:
        trigger_type = str(m15.get("trigger_type") or "").lower()
    
        if "momentum" in trigger_type:
            side_raw = "LONG"
        elif "down" in trigger_type:
            side_raw = "SHORT"
    
    # fallback 2 (EMA)
    if not side_raw:
        close = float(m15.get("close") or 0)
        ema20 = float(m15.get("ema20") or 0)
    
        if close > ema20:
            side_raw = "LONG"
        elif close < ema20:
            side_raw = "SHORT"
    
    # ❗ НОВОЕ
    if "БАЛАНС" in side_raw or "BALANCE" in side_raw or "NEUTRAL" in side_raw:
        return {"ok": False, "reason": "neutral_market"}
    
    # =====================
    # FINAL SIDE DETECTION (STRONG)
    # =====================
    
    if any(x in side_raw for x in ["LONG", "BUY", "UP", "BULL", "РОСТ"]):
        side = "LONG"
    
    elif any(x in side_raw for x in ["SHORT", "SELL", "DOWN", "BEAR", "ПАДЕНИЕ"]):
        side = "SHORT"
    
    else:
        print(f"[RETEST_DEBUG] bad side_raw={side_raw}", flush=True)
        return {"ok": False, "reason": f"bad_side_{side_raw}"}

    # =====================
    # BASIC DATA
    # =====================
    close = m15.get("close")
    ema20 = m15.get("ema20")
    vwap = m15.get("vwap")
    atr = float(m15.get("atr") or 0)

    if close is None or ema20 is None or vwap is None or atr <= 0:
        return {"ok": False, "reason": "no_m15_data"}

    r_low = m15.get("micro_stop")
    r_high = m15.get("close")

    if r_low is None or r_high is None:
        return {"ok": False, "reason": "no_range"}

    tol = atr * 0.3

    # =====================
    # LONG
    # =====================
    if side == "LONG":
        if close > ema20 and close > vwap:
            entry = close
            stop = r_low - tol
            return {"ok": True, "entry": entry, "stop": stop, "reason": "retest_long"}

    # =====================
    # SHORT
    # =====================
    if side == "SHORT":
        if close < ema20 and close < vwap:
            entry = close
            stop = r_high + tol
            return {"ok": True, "entry": entry, "stop": stop, "reason": "retest_short"}

    # =====================
    # FALLBACK RETEST (SWING SAVE)
    # =====================
    
    print(f"[RETEST_FALLBACK] using soft entry | side={side}", flush=True)
    
    if side == "LONG":
        entry = close
        stop = r_low - tol
        return {
            "ok": True,
            "entry": entry,
            "stop": stop,
            "reason": "fallback_long"
        }
    
    if side == "SHORT":
        entry = close
        stop = r_high + tol
        return {
            "ok": True,
            "entry": entry,
            "stop": stop,
            "reason": "fallback_short"
        }
    
    return {"ok": False, "reason": "no_retest"}
