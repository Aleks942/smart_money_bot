import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()
COINGLASS_API_KEY = (os.getenv("COINGLASS_API_KEY") or "").strip()

# =========================
# CONFIG
# =========================
POLL_SECONDS = 600
HEARTBEAT_SECONDS = 6 * 3600
STATE_FILE = "state.json"
TIMEOUT = 12

# Market config
OKX_INST = "BTC-USDT"          # OKX spot
CG_COIN = "bitcoin"
CG_VS = "usd"

# Thresholds (edge)
OI_SPIKE_MULT = 1.01           # +1% к прошлому OI
VOLUME_SPIKE_MULT = 1.8        # объёмный всплеск
COMPRESSION_MULT = 0.70        # диапазон сжался на 30%
FAKEDUMP_RECOVER = 0.55        # close выше 55% свечи
FAKEDUMP_WICK_MULT = 1.8       # нижняя тень длиннее тела * mult
ATR_EXPANSION_MULT = 1.3       # ATR сейчас > ATR раньше * 1.3

# Liquidity Pressure
PRESSURE_LOOKBACK = 20         # сколько свечей берём в диапазон
PRESSURE_ZONE = 0.15           # 15% верх/низ диапазона = зона давления
MIN_RANGE_PCT = 0.25           # если диапазон слишком узкий (<0.25% цены), pressure не считаем (шум)

# Breakout confirm
BREAKOUT_LOOKBACK = 12
BREAKOUT_CONFIRM_BARS = 2      # 2 свечи подряд держатся выше/ниже диапазона


# =========================
# TELEGRAM
# =========================
def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=TIMEOUT)
    except:
        pass


# =========================
# STATE
# =========================
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except:
        pass


# =========================
# COINGLASS (FAIL-SAFE)
# =========================
def _cg_get(url, params):
    if not COINGLASS_API_KEY:
        return None
    try:
        r = requests.get(
            url,
            headers={"coinglassSecret": COINGLASS_API_KEY},
            params=params,
            timeout=TIMEOUT
        )
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def get_funding_btc():
    data = _cg_get(
        "https://open-api.coinglass.com/public/v2/futures/funding_rates",
        {"symbol": "BTC"}
    )
    if not data:
        return None
    arr = data.get("data")
    if not isinstance(arr, list) or not arr:
        return None

    rates = []
    for x in arr:
        fr = x.get("fundingRate")
        if fr is not None:
            try:
                rates.append(float(fr))
            except:
                pass
    return (sum(rates) / len(rates)) if rates else None

def get_open_interest_btc():
    data = _cg_get(
        "https://open-api.coinglass.com/public/v2/futures/open_interest",
        {"symbol": "BTC"}
    )
    if not data:
        return None
    arr = data.get("data")
    if not isinstance(arr, list) or not arr:
        return None

    vals = []
    for x in arr:
        oi = x.get("openInterest")
        if oi is not None:
            try:
                vals.append(float(oi))
            except:
                pass
    return (sum(vals) / len(vals)) if vals else None


# =========================
# CANDLES SOURCES (OKX → CoinGecko)
# =========================
def get_okx_candles(bar: str, limit: int = 120):
    url = "https://www.okx.com/api/v5/market/candles"
    params = {"instId": OKX_INST, "bar": bar, "limit": str(limit)}
    r = requests.get(url, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"OKX HTTP {r.status_code}")

    data = r.json()
    if data.get("code") not in ("0", 0):
        raise RuntimeError(f"OKX bad response: {str(data)[:200]}")

    arr = data.get("data")
    if not isinstance(arr, list) or len(arr) < 20:
        raise RuntimeError("OKX not enough candles")

    arr.reverse()  # old->new

    candles = []
    for c in arr:
        try:
            candles.append([
                int(c[0]),      # ts
                float(c[1]),    # open
                float(c[2]),    # high
                float(c[3]),    # low
                float(c[4]),    # close
                float(c[5]),    # volume
            ])
        except:
            pass

    if len(candles) < 20:
        raise RuntimeError("OKX parse failed")

    return candles

def get_coingecko_ohlc(days="1"):
    url = f"https://api.coingecko.com/api/v3/coins/{CG_COIN}/ohlc"
    params = {"vs_currency": CG_VS, "days": days}
    r = requests.get(url, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"CoinGecko HTTP {r.status_code}")
    data = r.json()
    if not isinstance(data, list) or len(data) < 20:
        raise RuntimeError("CoinGecko not enough candles")

    candles = []
    for c in data[-120:]:
        candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), None])
    return candles

def get_candles_with_fallback(bar: str, limit: int = 120):
    try:
        return get_okx_candles(bar=bar, limit=limit), "OKX"
    except:
        return get_coingecko_ohlc(days="1"), "CoinGecko"


# =========================
# DETECTORS
# =========================
def compression_ok(candles):
    if len(candles) < 20:
        return (False, False)

    highs = [x[2] for x in candles]
    lows = [x[3] for x in candles]
    ranges = [h - l for h, l in zip(highs, lows)]

    last_range = sum(ranges[-4:]) / 4.0
    prev_range = sum(ranges[-12:-4]) / 8.0
    comp = last_range < prev_range * COMPRESSION_MULT

    vols = [x[5] for x in candles]
    if any(v is None for v in vols):
        return (comp, False)

    avg_prev = sum(vols[-20:-4]) / 16.0
    avg_last = sum(vols[-4:]) / 4.0
    vol_ok = avg_last >= avg_prev * 0.90

    return (comp and vol_ok, True)

def volume_spike_ok(candles):
    if len(candles) < 25:
        return False
    vols = [x[5] for x in candles]
    if any(v is None for v in vols):
        return False
    last = vols[-1]
    avg = sum(vols[-21:-1]) / 20.0
    return last > avg * VOLUME_SPIKE_MULT

def fake_dump_ok(candles):
    if len(candles) < 10:
        return False

    _, o, h, l, c, _v = candles[-1]
    rng = h - l
    if rng <= 0:
        return False

    body = abs(c - o)
    lower_wick = min(o, c) - l

    prev_lows = [x[3] for x in candles[-10:-1]]
    prev_min = min(prev_lows)

    pierced = l < prev_min * 0.997  # 0.3% ниже минимума
    recovered = c > (l + rng * FAKEDUMP_RECOVER)

    wick_strong = (body > 0 and lower_wick > body * FAKEDUMP_WICK_MULT) or (body == 0 and lower_wick > rng * 0.4)

    return pierced and recovered and wick_strong

def breakout_ok(candles, lookback=BREAKOUT_LOOKBACK):
    if len(candles) < lookback + 2:
        return None

    highs = [c[2] for c in candles[-lookback-1:-1]]
    lows = [c[3] for c in candles[-lookback-1:-1]]
    last_close = candles[-1][4]

    if last_close > max(highs):
        return "UP"
    if last_close < min(lows):
        return "DOWN"
    return None

def breakout_confirm_ok(candles, lookback=BREAKOUT_LOOKBACK, confirm_bars=BREAKOUT_CONFIRM_BARS):
    """
    Подтверждение breakout: последние N свечей закрылись выше/ниже границ диапазона.
    Возвращает "UP"/"DOWN"/None
    """
    if len(candles) < lookback + confirm_bars + 2:
        return None

    base = candles[-(lookback + confirm_bars + 1):- (confirm_bars + 1)]
    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)

    last_closes = [c[4] for c in candles[-confirm_bars:]]
    if all(cl > hi for cl in last_closes):
        return "UP"
    if all(cl < lo for cl in last_closes):
        return "DOWN"
    return None

def atr_expansion_ok(candles, period=14, compare_back=5):
    if len(candles) < period + compare_back + 2:
        return False

    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        prev_close = candles[i-1][4]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)

    atr_now = sum(trs[-period:]) / period
    atr_prev = sum(trs[-period-compare_back:-compare_back]) / period

    return atr_now > atr_prev * ATR_EXPANSION_MULT

def oi_spike_ok(prev_oi, oi):
    if prev_oi is None or oi is None:
        return False
    return oi > prev_oi * OI_SPIKE_MULT

def liquidity_pressure(candles, lookback=20, zone=0.15, min_range_pct=0.25):
    if len(candles) < lookback + 2:
        return None, {}

    segment = candles[-lookback-1:-1]
    hi = max(x[2] for x in segment)
    lo = min(x[3] for x in segment)
    close = candles[-1][4]

    rng = hi - lo
    if rng <= 0:
        return None, {}

    range_pct = (rng / close) * 100.0
    if range_pct < min_range_pct:
        return None, {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": None}

    pos = (close - lo) / rng  # 0..1
    if pos >= (1.0 - zone):
        return "UP", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}
    if pos <= zone:
        return "DOWN", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}

    return None, {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}


# =========================
# RUSSIAN EXPLANATIONS
# =========================
EXPLAIN = {
    "FUNDING_NEG": "Funding < 0: толпа чаще в шорте. Это топливо для squeeze (вынос вверх).",
    "OI_SPIKE": "Open Interest растёт: заходят новые плечевые позиции, часто признак активности крупных.",
    "COMP_5M": "Compression 5m: волатильность упала, рынок сжался — ‘пружина’ копит энергию.",
    "COMP_5M+VOL": "Compression 5m + объём держится: похоже на тихий набор, это сильнее обычного сжатия.",
    "COMP_15M": "Compression 15m: сжатие на старшем ТФ — обычно даёт более сильные импульсы.",
    "COMP_15M+VOL": "Compression 15m + объём держится: рынок реально ‘зажат’, вероятность сильного движения выше.",
    "FAKE_DUMP": "Fake Dump: ложный слив — пробили низ, выбили стопы и быстро выкупили (ловушка).",
    "VOL_SPIKE": "Volume Spike: всплеск объёма — рынок реально ‘толкают’, подтверждение силы.",
    "BREAKOUT_UP": "Breakout UP: закрытие выше диапазона — не тень, а закрепление вверх.",
    "BREAKOUT_DOWN": "Breakout DOWN: закрытие ниже диапазона — закрепление вниз.",
    "BREAKOUT_CONFIRM_UP": "Confirm UP: 2 свечи подряд удержались выше диапазона — фейков меньше, движение надёжнее.",
    "BREAKOUT_CONFIRM_DOWN": "Confirm DOWN: 2 свечи подряд удержались ниже диапазона — подтверждение импульса вниз.",
    "ATR_EXPANSION": "ATR вырос: амплитуда движения увеличилась — импульс реально начался.",
    "PRESSURE_UP": "Давление вверх: цена в верхней зоне диапазона — ‘упёрлась’ и готова выстрелить вверх.",
    "PRESSURE_DOWN": "Давление вниз: цена в нижней зоне диапазона — ‘упёрлась’ и готова пролиться вниз.",
}

def explain_flags_ru(flags):
    lines = []
    for f in flags:
        txt = EXPLAIN.get(f)
        lines.append(f"• {f}: {txt if txt else '(нет описания)'}")
    return lines


# =========================
# DIRECTION HINT ENGINE
# =========================
def direction_hint(flags):
    """
    Возвращает (hint_text, reasons_list)
    hint_text: "⬆️ вероятнее вверх" / "⬇️ вероятнее вниз" / "⚖️ баланс"
    """
    up = 0
    down = 0
    reasons = []

    # Сильные направленные флаги
    if "BREAKOUT_CONFIRM_UP" in flags:
        up += 3; reasons.append("Confirm breakout UP (+3)")
    if "BREAKOUT_CONFIRM_DOWN" in flags:
        down += 3; reasons.append("Confirm breakout DOWN (+3)")

    if "BREAKOUT_UP" in flags:
        up += 2; reasons.append("Breakout UP (+2)")
    if "BREAKOUT_DOWN" in flags:
        down += 2; reasons.append("Breakout DOWN (+2)")

    if "PRESSURE_UP" in flags:
        up += 1; reasons.append("Pressure UP (+1)")
    if "PRESSURE_DOWN" in flags:
        down += 1; reasons.append("Pressure DOWN (+1)")

    # Fake dump чаще даёт уклон вверх (но не всегда)
    if "FAKE_DUMP" in flags:
        up += 1; reasons.append("Fake dump чаще перед ростом (+1)")

    # VOL_SPIKE и ATR_EXPANSION — подтверждают импульс в сторону breakout/pressure
    if "VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags):
        up += 1; reasons.append("Volume подтверждает UP (+1)")
    if "VOL_SPIKE" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags):
        down += 1; reasons.append("Volume подтверждает DOWN (+1)")

    if "ATR_EXPANSION" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags):
        up += 1; reasons.append("ATR подтверждает UP (+1)")
    if "ATR_EXPANSION" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags):
        down += 1; reasons.append("ATR подтверждает DOWN (+1)")

    # Итог
    if up >= down + 2:
        return "⬆️ Вероятнее ВВЕРХ", reasons
    if down >= up + 2:
        return "⬇️ Вероятнее ВНИЗ", reasons
    return "⚖️ Баланс / нет явного направления", reasons


# =========================
# SCORING / MESSAGE
# =========================
def build_signal(state):
    c5, src5 = get_candles_with_fallback(bar="5m", limit=120)
    c15, src15 = get_candles_with_fallback(bar="15m", limit=120)

    funding = get_funding_btc()
    oi = get_open_interest_btc()
    prev_oi = state.get("prev_oi")

    flags = []
    score = 0

    # 1) Funding contrarian
    if funding is not None and funding < 0:
        score += 1
        flags.append("FUNDING_NEG")

    # 2) OI spike
    if oi_spike_ok(prev_oi, oi):
        score += 1
        flags.append("OI_SPIKE")

    # 3) Compression 5m
    comp5, used_vol5 = compression_ok(c5)
    if comp5:
        score += 1
        flags.append("COMP_5M" + ("+VOL" if used_vol5 else ""))

    # 4) Compression 15m
    comp15, used_vol15 = compression_ok(c15)
    if comp15:
        score += 1
        flags.append("COMP_15M" + ("+VOL" if used_vol15 else ""))

    # 5) Fake dump trap
    if fake_dump_ok(c5):
        score += 1
        flags.append("FAKE_DUMP")

    # 6) Volume spike
    if volume_spike_ok(c5):
        score += 1
        flags.append("VOL_SPIKE")

    # 7) Breakout
    direction = breakout_ok(c5)
    if direction:
        score += 1
        flags.append(f"BREAKOUT_{direction}")

    # 8) ATR expansion
    if atr_expansion_ok(c5):
        score += 1
        flags.append("ATR_EXPANSION")

    # 9) Liquidity Pressure
    pres, pres_meta = liquidity_pressure(
        c5, lookback=PRESSURE_LOOKBACK, zone=PRESSURE_ZONE, min_range_pct=MIN_RANGE_PCT
    )
    if pres == "UP":
        score += 1
        flags.append("PRESSURE_UP")
    elif pres == "DOWN":
        score += 1
        flags.append("PRESSURE_DOWN")

    # 10) Breakout confirmation (2-bar)
    conf = breakout_confirm_ok(c5, lookback=BREAKOUT_LOOKBACK, confirm_bars=BREAKOUT_CONFIRM_BARS)
    if conf == "UP":
        score += 1
        flags.append("BREAKOUT_CONFIRM_UP")
    elif conf == "DOWN":
        score += 1
        flags.append("BREAKOUT_CONFIRM_DOWN")

    price = c5[-1][4]

    hint, hint_reasons = direction_hint(flags)

    return {
        "price": price,
        "funding": funding,
        "oi": oi,
        "score": score,
        "flags": flags,
        "src_5m": src5,
        "src_15m": src15,
        "pressure_meta": pres_meta,
        "direction_hint": hint,
        "direction_reasons": hint_reasons,
        "ts": int(time.time()),
    }

def format_message(sig):
    funding = sig["funding"]
    oi = sig["oi"]
    flags = sig["flags"]

    lines = []
    lines.append("🧠 SMART MONEY RADAR — PRO MAX + DIRECTION")
    lines.append(f"💵 BTC: {sig['price']:.2f}")
    lines.append(f"🧷 Sources: 5m={sig['src_5m']} | 15m={sig['src_15m']}")
    lines.append(f"📊 Score: {sig['score']}/10")

    if funding is None:
        lines.append("💰 Funding(avg): N/A")
    else:
        lines.append(f"💰 Funding(avg): {funding:.6f}")

    if oi is None:
        lines.append("📈 OI(avg): N/A")
    else:
        lines.append(f"📈 OI(avg): {int(oi)}")

    pm = sig.get("pressure_meta") or {}
    if pm.get("range_hi") is not None and pm.get("range_lo") is not None and pm.get("range_pct") is not None:
        try:
            lines.append(f"🧲 Range(lookback): {pm['range_lo']:.2f} → {pm['range_hi']:.2f} | width≈{pm['range_pct']:.2f}%")
        except:
            pass

    # Direction hint
    lines.append("")
    lines.append(f"🎯 Direction hint: {sig['direction_hint']}")
    if sig.get("direction_reasons"):
        lines.append("Причины направления:")
        for r in sig["direction_reasons"][:6]:
            lines.append(f"• {r}")

    if flags:
        lines.append("")
        lines.append("Флаги (что увидел бот):")
        lines.extend([f"• {x}" for x in flags])

        lines.append("")
        lines.append("Расшифровка (по-русски):")
        lines.extend(explain_flags_ru(flags))

    # Levels
    if sig["score"] >= 7:
        lines.append("")
        lines.append("🔥 EDGE: рынок реально толкают. Движение вероятнее и обычно резче.")
    elif sig["score"] >= 5:
        lines.append("")
        lines.append("🚀 STRONG: структура готова + есть подтверждения. Вероятность импульса выше нормы.")
    elif sig["score"] >= 3:
        lines.append("")
        lines.append("⚡ PRE-PUMP: подготовка есть, но пока не максимум подтверждений.")

    return "\n".join(lines)

def should_send(state, sig):
    prev_score = state.get("prev_score")
    prev_flags = state.get("prev_flags", [])
    last_hb = state.get("last_hb", 0)
    now = sig["ts"]

    changed = (sig["score"] != prev_score) or (sig["flags"] != prev_flags)
    heartbeat = (now - last_hb) >= HEARTBEAT_SECONDS

    return changed or heartbeat, heartbeat


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing env vars: BOT_TOKEN / CHAT_ID")

    state = load_state()
    send_telegram("🚀 SMART MONEY RADAR — PRO MAX + DIRECTION started")

    while True:
        try:
            sig = build_signal(state)

            send_it, hb = should_send(state, sig)

            # update state always
            state["prev_oi"] = sig["oi"]
            state["prev_score"] = sig["score"]
            state["prev_flags"] = sig["flags"]

            if send_it:
                send_telegram(format_message(sig))
                if hb:
                    state["last_hb"] = sig["ts"]

            save_state(state)

        except Exception as e:
            send_telegram(f"❌ Error:\n{str(e)}")

        time.sleep(POLL_SECONDS)

