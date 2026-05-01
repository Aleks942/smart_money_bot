import pandas as pd


def f(x, default=0.0):
    try:
        return float(x)
    except:
        return default


def normalize_candles(candles):
    if candles is None:
        return []

    if hasattr(candles, "empty"):
        if candles.empty:
            return []
        rows = []
        for _, r in candles.iterrows():
            rows.append([
                r.get("ts", None),
                r.get("open", 0),
                r.get("high", 0),
                r.get("low", 0),
                r.get("close", 0),
                r.get("volume", 0),
            ])
        return rows

    if isinstance(candles, list):
        return [x for x in candles if isinstance(x, (list, tuple)) and len(x) >= 5]

    return []


def o(x): return f(x[1])
def h(x): return f(x[2])
def l(x): return f(x[3])
def c(x): return f(x[4])
def v(x): return f(x[5]) if len(x) > 5 else 0.0


def safe_last(candles):
    candles = normalize_candles(candles)
    if not candles:
        return None
    return candles[-1]


# =========================
# 1. УРОВНИ РАЗВОРОТА
# =========================

def find_reversal_levels(candles, lookback=120, tolerance_pct=1.0, min_touches=2):
    candles = normalize_candles(candles)

    if len(candles) < 20:
        return []

    candles = candles[-lookback:]
    raw = []

    for i in range(2, len(candles) - 2):
        high = h(candles[i])
        low = l(candles[i])

        if (
            high > h(candles[i - 1])
            and high > h(candles[i - 2])
            and high > h(candles[i + 1])
            and high > h(candles[i + 2])
        ):
            raw.append(high)

        if (
            low < l(candles[i - 1])
            and low < l(candles[i - 2])
            and low < l(candles[i + 1])
            and low < l(candles[i + 2])
        ):
            raw.append(low)

    levels = []

    for price in raw:
        if price <= 0:
            continue

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

    if price <= 0:
        return None

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
    candles = normalize_candles(candles)

    if len(candles) < max_bars:
        return None

    best = None

    for bars in range(min_bars, max_bars + 1):
        zone = candles[-bars:]

        if len(zone) < min_bars:
            continue

        try:
            high = max(h(x) for x in zone)
            low = min(l(x) for x in zone)
            close = c(zone[-1])
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
    candles = normalize_candles(candles)
    zone = candles[-lookback:]

    if len(zone) < 3:
        return {
            "buy": 0,
            "sell": 0,
            "bias": "NEUTRAL"
        }

    avg_vol = sum(v(x) for x in zone) / max(len(zone), 1)

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
    candles = normalize_candles(candles)

    if not candles or range_data is None:
        return None

    high = range_data.get("high")
    low = range_data.get("low")

    if high is None or low is None:
        return None

    try:
        last_close = c(candles[-1])
    except Exception as e:
        print(f"[BREAKOUT_ERROR] {type(e).__name__}: {e}", flush=True)
        return None

    if last_close > high * (1 + buffer_pct / 100):
        return "BREAKOUT_UP"

    if last_close < low * (1 - buffer_pct / 100):
        return "BREAKOUT_DOWN"

    return None


# =========================
# 5. СТОП ЗА ПРОТОРГОВКУ
# =========================

def build_entry_plan(symbol, side, price, range_data, max_stop_pct=3.5):
    if not range_data:
        return None

    high = range_data.get("high")
    low = range_data.get("low")

    if high is None or low is None:
        return None

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
        "range_bars": range_data.get("bars"),
        "range_width_pct": range_data.get("width_pct")
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
    candles_month = normalize_candles(candles_month)
    candles_day = normalize_candles(candles_day)
    candles_h1 = normalize_candles(candles_h1)
    candles_m15 = normalize_candles(candles_m15)

    if len(candles_m15) < 30:
        return None

    last = safe_last(candles_m15)

    if last is None:
        return None

    price = c(last)

    if price <= 0:
        return None

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

    range_data = find_range_m15(
        candles_m15,
        min_bars=5,
        max_bars=20,
        max_width_pct=2.5
    )

    if not range_data:
        return None

    breakout = detect_breakout(candles_m15, range_data)

    if not breakout:
        return None

    power = buyer_seller_power(candles_m15, lookback=8)

    side = None

    if breakout == "BREAKOUT_UP" and power["bias"] == "BUYER_STRONG":
        side = "LONG"

    if breakout == "BREAKOUT_DOWN" and power["bias"] == "SELLER_STRONG":
        side = "SHORT"

    if not side:
        return None

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
