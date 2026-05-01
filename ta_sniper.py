import pandas as pd
def f(x, default=0.0):
    try:
        return float(x)
    except:
        return default


def o(c):
    if hasattr(c, "get"):
        return f(c.get("open", 0))
    return f(c[1])

def h(c):
    if hasattr(c, "get"):
        return f(c.get("high", 0))
    return f(c[2])

def l(c):
    if hasattr(c, "get"):
        return f(c.get("low", 0))
    return f(c[3])

def c(candle):
    if hasattr(candle, "get"):
        return f(candle.get("close", 0))
    return f(candle[4])

def v(candle):
    if hasattr(candle, "get"):
        return f(candle.get("volume", 0))
    return f(candle[5]) if len(candle) > 5 else 0

def safe_last(c):
    if c is None:
        return None

    if hasattr(c, "empty") and c.empty:
        return None

    if isinstance(c, list) and len(c) == 0:
        return None

    if hasattr(c, "iloc"):
        return c.iloc[-1]

    return c[-1]




# =========================
# 1. УРОВНИ РАЗВОРОТА
# =========================

def find_reversal_levels(candles, lookback=120, tolerance_pct=1.0, min_touches=2):
    """
    Ищет уровни, где цена разворачивалась.
    Подходит для MONTH / DAY / H1.
    """

    if candles is None or len(candles) < 20:
        return []

    candles = candles[-lookback:]
    raw = []

    for i in range(2, len(candles) - 2):
        row = candles.iloc[i] if hasattr(candles, "iloc") else candles[i]
    
        high = h(row)
        low = l(row)
    
        prev1 = candles.iloc[i-1] if hasattr(candles, "iloc") else candles[i-1]
        prev2 = candles.iloc[i-2] if hasattr(candles, "iloc") else candles[i-2]
        next1 = candles.iloc[i+1] if hasattr(candles, "iloc") else candles[i+1]
        next2 = candles.iloc[i+2] if hasattr(candles, "iloc") else candles[i+2]
    
        if high > h(prev1) and high > h(prev2) and high > h(next1) and high > h(next2):
            raw.append(high)
    
        if low < l(prev1) and low < l(prev2) and low < l(next1) and low < l(next2):
            raw.append(low)

    levels = []

    for price in raw:
        added = False

        for lvl in levels:
            diff = abs(price - lvl["price"]) / lvl["price"] * 100

            if diff <= tolerance_pct:
                lvl["touches"] += 1
                lvl["price"] = (lvl["price"] + price) / 2
                added = True
                break

        if not added:
            levels.append({
                "price": price,
                "touches": 1
            })

    levels = [x for x in levels if x["touches"] >= min_touches]
    levels.sort(key=lambda x: x["touches"], reverse=True)

    return levels


def nearest_level(price, levels, max_distance_pct=2.0):
    best = None

    for lvl in levels:
        dist = abs(price - lvl["price"]) / price * 100

        if dist <= max_distance_pct:
            item = dict(lvl)
            item["distance_pct"] = round(dist, 2)

            if best is None or item["distance_pct"] < best["distance_pct"]:
                best = item

    return best


# =========================
# 2. ПРОТОРГОВКА НА M15
# =========================

def find_range_m15(candles, min_bars=5, max_bars=20, max_width_pct=2.5):
    """
    Ищет диапазон / проторговку.
    """

    if candles is None or len(candles) < max_bars:
        return None

    best = None

    for bars in range(min_bars, max_bars + 1):
        zone = candles[-bars:]

        clean_zone = []
        
        for x in zone:
            # ✅ pandas строка (Series)
            if hasattr(x, "to_dict"):
                clean_zone.append(x)
                continue
        
            # ✅ норм свеча list/tuple
            if isinstance(x, (list, tuple)) and len(x) >= 5:
                clean_zone.append(x)
                continue
        
            # ❌ мусор (строки, None, dict без структуры)
            print(f"[BAD_CANDLE] {type(x)} -> {str(x)[:30]}", flush=True)
        
        # если данных мало — пропускаем
        if len(clean_zone) < min_bars:
            continue
        
        try:
            high = max(h(x) for x in clean_zone)
            low = min(l(x) for x in clean_zone)
        
            last = clean_zone[-1]
        
            # pandas
            if hasattr(last, "to_dict"):
                close = float(last["close"])
        
            # list
            else:
                close = c(last)
        
        except Exception as e:
            print(f"[RANGE_ERROR] {type(e).__name__}: {e}", flush=True)
            continue
        
        if close <= 0:
            continue

        width_pct = (high - low) / close * 100

        if width_pct <= max_width_pct:
            best = {
                "bars": bars,
                "high": high,
                "low": low,
                "width_pct": round(width_pct, 2),
                "zone": zone
            }

    return best


# =========================
# 3. СИЛА ПОКУПАТЕЛЯ / ПРОДАВЦА
# =========================

def buyer_seller_power(candles, lookback=8):
    """
    Считает силу стороны по телам свечей и объёму.
    """

    zone = candles[-lookback:]

    if len(zone) < 3:
        return {
            "buy": 0,
            "sell": 0,
            "bias": "NEUTRAL"
        }

    avg_vol = sum(v(x) for x in zone) / len(zone)

    buy = 0.0
    sell = 0.0

    for candle in zone:
        open_ = o(candle)
        close = c(candle)
        high = h(candle)
        low = l(candle)
        vol = v(candle)

        full = max(high - low, 0.00000001)
        body = abs(close - open_)
        body_power = body / full

        vol_power = vol / avg_vol if avg_vol > 0 else 1
        power = body_power * vol_power

        if close > open_:
            buy += power
        elif close < open_:
            sell += power

    if buy > sell * 1.25:
        bias = "BUYER_STRONG"
    elif sell > buy * 1.25:
        bias = "SELLER_STRONG"
    else:
        bias = "NEUTRAL"

    return {
        "buy": round(buy, 2),
        "sell": round(sell, 2),
        "bias": bias
    }


# =========================
# 4. ВЫХОД ИЗ ДИАПАЗОНА
# =========================

def detect_breakout(candles, range_data, buffer_pct=0.12):
    if not candles or not range_data:
        return None

    last_close = c(candles[-1])

    high = range_data["high"]
    low = range_data["low"]

    if last_close > high * (1 + buffer_pct / 100):
        return "BREAKOUT_UP"

    if last_close < low * (1 - buffer_pct / 100):
        return "BREAKOUT_DOWN"

    return None


# =========================
# 5. СТОП ЗА ПРОТОРГОВКУ
# =========================

def build_entry_plan(symbol, side, price, range_data, max_stop_pct=3.5):
    high = range_data["high"]
    low = range_data["low"]

    if side == "LONG":
        entry = price
        stop = low * 0.998
        risk = entry - stop

        if risk <= 0:
            return None

        tp1 = entry + risk * 2
        tp2 = entry + risk * 3

    elif side == "SHORT":
        entry = price
        stop = high * 1.002
        risk = stop - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * 2
        tp2 = entry - risk * 3

    else:
        return None

    stop_pct = abs(entry - stop) / entry * 100

    if stop_pct > max_stop_pct:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "stop_pct": round(stop_pct, 2),
        "range_high": round(high, 8),
        "range_low": round(low, 8),
        "range_bars": range_data["bars"],
        "range_width_pct": range_data["width_pct"]
    }


# =========================
# 6. ГЛАВНЫЙ АНАЛИЗ
# =========================

def analyze_ta_sniper(
    symbol,
    candles_month,
    candles_day,
    candles_h1,
    candles_m15,
    max_stop_pct=3.5
):
    """
    Главный движок:
    история → уровни → M15 проторговка → сила → выход → вход/стоп.
    """

    if candles_m15 is None or candles_m15.empty or len(candles_m15) < 30:
        return None

    last = safe_last(candles_m15)

    if last is None:
        return None
    
    price = c(last)

    # 1. уровни с истории
    month_levels = find_reversal_levels(
        candles_month,
        lookback=120,
        tolerance_pct=1.2,
        min_touches=2
    )

    day_levels = find_reversal_levels(
        candles_day,
        lookback=180,
        tolerance_pct=0.8,
        min_touches=2
    )

    levels = month_levels + day_levels

    near = nearest_level(price, levels, max_distance_pct=2.5)

    if not near:
        return None

    # 2. проторговка M15
    range_data = find_range_m15(
        candles_m15,
        min_bars=5,
        max_bars=20,
        max_width_pct=2.5
    )

    if not range_data:
        return None

    # 3. выход из диапазона
    breakout = detect_breakout(candles_m15, range_data)

    if not breakout:
        return None

    # 4. сила стороны
    power = buyer_seller_power(candles_m15, lookback=8)

    side = None

    if breakout == "BREAKOUT_UP" and power["bias"] == "BUYER_STRONG":
        side = "LONG"

    if breakout == "BREAKOUT_DOWN" and power["bias"] == "SELLER_STRONG":
        side = "SHORT"

    if not side:
        return None

    # 5. план сделки
    plan = build_entry_plan(
        symbol=symbol,
        side=side,
        price=price,
        range_data=range_data,
        max_stop_pct=max_stop_pct
    )

    if not plan:
        return None

    plan["level_price"] = round(near["price"], 8)
    plan["level_distance_pct"] = near["distance_pct"]
    plan["buyer_power"] = power["buy"]
    plan["seller_power"] = power["sell"]
    plan["power_bias"] = power["bias"]
    plan["breakout"] = breakout
    plan["setup"] = "TA_SNIPER_LEVEL_RANGE_BREAKOUT"

    return plan
