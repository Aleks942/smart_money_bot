import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# =========================
# ENV
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

MESSAGE_MODE = (os.getenv("MESSAGE_MODE") or "AUTO").upper()   # AUTO / SHORT / MEDIUM / FULL

EDGE_MID_SCORE = int(os.getenv("EDGE_MID_SCORE") or "4")
EDGE_HIGH_SCORE = int(os.getenv("EDGE_HIGH_SCORE") or "7")

POLL_SECONDS = int(os.getenv("POLL_SECONDS") or "600")
TIMEOUT = int(os.getenv("TIMEOUT") or "12")
STATE_FILE = os.getenv("STATE_FILE") or "state.json"

SCAN_TOP_N = int(os.getenv("SCAN_TOP_N") or "40")
SCAN_MIN_VOL_USDT = float(os.getenv("SCAN_MIN_VOL_USDT") or "2000000")
SCAN_MIN_PCT_24H = float(os.getenv("SCAN_MIN_PCT_24H") or "2")

ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE") or "4")
ALERT_TOP_M = int(os.getenv("ALERT_TOP_M") or "8")
DETAIL_TOP_K = int(os.getenv("DETAIL_TOP_K") or "1")

# =========================
# V2 ENV (NEW)
# =========================
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC") or "1800")  # 30 min default
ANTI_PUMP_PCT_5M = float(os.getenv("ANTI_PUMP_PCT_5M") or "6.0")     # abs move on last 5m bar

# "до движения" — отдельные манипуляции/накопление
MANIP_ALERT_ENABLED = (os.getenv("MANIP_ALERT_ENABLED") or "1").strip() != "0"
MANIP_TOP_N = int(os.getenv("MANIP_TOP_N") or "6")
MANIP_DETAIL_TOP_K = int(os.getenv("MANIP_DETAIL_TOP_K") or "1")
MANIP_MIN_ACC_SCORE = int(os.getenv("MANIP_MIN_ACC_SCORE") or "3")
MANIP_COOLDOWN_SEC = int(os.getenv("MANIP_COOLDOWN_SEC") or "1800")  # тоже 30 мин

# Optional: режим "видеть накопление раньше" (не обязателен)
# Если 1 — игнорим фильтр по %24h (SCAN_MIN_PCT_24H), но оставляем объём.
ACCUMULATION_MODE = (os.getenv("ACCUMULATION_MODE") or "0").strip() == "1"

# =========================
# TRIGGER ENV (NEW)
# =========================
# Анти-спам для триггера (чтобы не долбило по одной монете каждые 10 минут)
TRIGGER_COOLDOWN_SEC = int(os.getenv("TRIGGER_COOLDOWN_SEC") or "1800")  # 30 мин

# OKX
OKX_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"

# =========================
# HARD RULES / FILTERS
# =========================
EXCLUDE_TOKENS_CONTAINS = ["3L", "3S", "5L", "5S", "BULL", "BEAR", "UP", "DOWN"]  # левередж/мусор
QUOTE = "USDT"

# =========================
# HTTP SESSION
# =========================
S = requests.Session()
S.headers.update({"User-Agent": "smart-money-radar/2.1"})

# =========================
# TELEGRAM
# =========================
def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        S.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=TIMEOUT)
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
        return {"symbols": {}, "last_heartbeat": 0}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except:
        pass

# =========================
# OKX HELPERS
# =========================
def okx_get(url, params):
    r = S.get(url, params=params, timeout=TIMEOUT)
    if r.status_code == 429:
        time.sleep(2.5)
        r = S.get(url, params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"OKX HTTP {r.status_code}")
    data = r.json()
    if str(data.get("code")) != "0":
        raise RuntimeError(f"OKX bad response: {str(data)[:250]}")
    return data.get("data", [])

def get_okx_spot_usdt_tickers():
    return okx_get(OKX_TICKERS_URL, {"instType": "SPOT"})

def get_okx_candles(instId: str, bar: str, limit: int = 120):
    arr = okx_get(OKX_CANDLES_URL, {"instId": instId, "bar": bar, "limit": str(limit)})
    if not isinstance(arr, list) or len(arr) < 30:
        raise RuntimeError(f"Not enough candles for {instId} {bar}")
    arr.reverse()  # old -> new
    candles = []
    for c in arr:
        try:
            # [ts, o, h, l, c, vol]
            candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        except:
            pass
    if len(candles) < 30:
        raise RuntimeError(f"Candle parse failed {instId} {bar}")
    return candles

# =========================
# CANDLES FEATURES (PRO MAX)
# =========================
COMPRESSION_MULT = 0.70
VOLUME_SPIKE_MULT = 1.8
FAKEDUMP_RECOVER = 0.55
FAKEDUMP_WICK_MULT = 1.8
ATR_EXPANSION_MULT = 1.3
PRESSURE_LOOKBACK = 20
PRESSURE_ZONE = 0.15
MIN_RANGE_PCT = 0.25
BREAKOUT_LOOKBACK = 12
BREAKOUT_CONFIRM_BARS = 2

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
    avg_prev = sum(vols[-20:-4]) / 16.0
    avg_last = sum(vols[-4:]) / 4.0
    vol_ok = avg_last >= avg_prev * 0.90
    return (comp and vol_ok, True)

def volume_spike_ok(candles):
    if len(candles) < 25:
        return False
    vols = [x[5] for x in candles]
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

    pierced = l < prev_min * 0.997
    recovered = c > (l + rng * FAKEDUMP_RECOVER)
    wick_strong = (body > 0 and lower_wick > body * FAKEDUMP_WICK_MULT) or (body == 0 and lower_wick > rng * 0.4)
    return pierced and recovered and wick_strong

def breakout_ok(candles, lookback=BREAKOUT_LOOKBACK):
    highs = [c[2] for c in candles[-lookback-1:-1]]
    lows = [c[3] for c in candles[-lookback-1:-1]]
    last_close = candles[-1][4]
    if last_close > max(highs):
        return "UP"
    if last_close < min(lows):
        return "DOWN"
    return None

def breakout_confirm_ok(candles, lookback=BREAKOUT_LOOKBACK, confirm_bars=BREAKOUT_CONFIRM_BARS):
    base = candles[-(lookback + confirm_bars + 1):-(confirm_bars + 1)]
    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)
    closes = [c[4] for c in candles[-confirm_bars:]]
    if all(cl > hi for cl in closes):
        return "UP"
    if all(cl < lo for cl in closes):
        return "DOWN"
    return None

def atr_expansion_ok(candles, period=14, compare_back=5):
    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        prev_close = candles[i-1][4]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
    if len(trs) < period + compare_back + 2:
        return False
    atr_now = sum(trs[-period:]) / period
    atr_prev = sum(trs[-period-compare_back:-compare_back]) / period
    return atr_now > atr_prev * ATR_EXPANSION_MULT

def liquidity_pressure(candles, lookback=PRESSURE_LOOKBACK, zone=PRESSURE_ZONE, min_range_pct=MIN_RANGE_PCT):
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
    pos = (close - lo) / rng
    if pos >= (1.0 - zone):
        return "UP", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}
    if pos <= zone:
        return "DOWN", {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}
    return None, {"range_hi": hi, "range_lo": lo, "range_pct": range_pct, "pos": pos}

# =========================
# V2: Anti-pump + Accumulation score
# =========================
def anti_pump_penalty(candles, threshold_pct):
    """
    Если последняя 5m свеча уже дала слишком большой ход,
    уменьшаем score (чтобы не прыгать в первый памп/дамп).
    """
    if len(candles) < 2:
        return 0
    prev_close = candles[-2][4]
    last_close = candles[-1][4]
    if prev_close <= 0:
        return 0
    pct = (last_close - prev_close) / prev_close * 100.0
    if abs(pct) >= threshold_pct:
        return -1
    return 0

def accumulation_bias(flags):
    """
    Накопление "до выстрела":
    - compression на 5m/15m
    - давление у края диапазона
    - при этом нет ATR_EXPANSION (движение ещё не началось)
    """
    s = 0
    if "COMP_5M" in flags:
        s += 1
    if "COMP_15M" in flags:
        s += 1
    if "PRESSURE_UP" in flags or "PRESSURE_DOWN" in flags:
        s += 1
    if "ATR_EXPANSION" not in flags:
        s += 1
    return s

# =========================
# DIRECTION / ENTRY / STAGE / TARGET
# =========================
def direction_hint(flags):
    up = 0
    down = 0
    reasons = []

    if "BREAKOUT_CONFIRM_UP" in flags:
        up += 3; reasons.append("Confirm UP (+3)")
    if "BREAKOUT_CONFIRM_DOWN" in flags:
        down += 3; reasons.append("Confirm DOWN (+3)")

    if "BREAKOUT_UP" in flags:
        up += 2; reasons.append("Breakout UP (+2)")
    if "BREAKOUT_DOWN" in flags:
        down += 2; reasons.append("Breakout DOWN (+2)")

    if "PRESSURE_UP" in flags:
        up += 1; reasons.append("Pressure UP (+1)")
    if "PRESSURE_DOWN" in flags:
        down += 1; reasons.append("Pressure DOWN (+1)")

    if "FAKE_DUMP" in flags:
        up += 1; reasons.append("Fake dump (+1)")

    if "VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags):
        up += 1; reasons.append("Volume→UP (+1)")
    if "VOL_SPIKE" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags):
        down += 1; reasons.append("Volume→DOWN (+1)")

    if "ATR_EXPANSION" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags):
        up += 1; reasons.append("ATR→UP (+1)")
    if "ATR_EXPANSION" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags):
        down += 1; reasons.append("ATR→DOWN (+1)")

    if up >= down + 2:
        return "⬆️ ВВЕРХ", reasons, up, down
    if down >= up + 2:
        return "⬇️ ВНИЗ", reasons, up, down
    return "⚖️ БАЛАНС", reasons, up, down

def entry_engine(score, flags, direction_text, up_w, down_w):
    if "БАЛАНС" in direction_text:
        return "🔴 WAIT", "Нет явного направления"
    safe_cond = (
        score >= EDGE_HIGH_SCORE and
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and
        "ATR_EXPANSION" in flags and
        "VOL_SPIKE" in flags and
        (up_w >= 3 or down_w >= 3)
    )
    if safe_cond:
        return "🟢 SAFE ENTRY", "Подтверждение + импульс"
    if score >= EDGE_MID_SCORE and any(f.startswith("BREAKOUT") or f.startswith("PRESSURE") for f in flags):
        return "🟡 AGGRESSIVE", "Ранний вход по структуре"
    return "🔴 WAIT", "Недостаточно факторов"

def smart_money_stage(score, flags):
    if score < 2:
        return "⚪ NEUTRAL", "Структуры почти нет"
    if (("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and
        "ATR_EXPANSION" in flags and "VOL_SPIKE" in flags):
        return "🟢 EXPANSION", "Реальное движение"
    if ("FAKE_DUMP" in flags or
        ("PRESSURE_DOWN" in flags and "BREAKOUT_DOWN" in flags) or
        ("PRESSURE_UP" in flags and "BREAKOUT_UP" in flags)):
        return "🟡 MANIPULATION", "Вероятен сбор ликвидности"
    if any(f.startswith("COMP_") for f in flags):
        return "🟣 ACCUMULATION", "Накопление/сжатие"
    return "⚪ NEUTRAL", "Смешанные признаки"

def liquidity_target(pmeta, flags):
    if not pmeta:
        return None
    lo = pmeta.get("range_lo")
    hi = pmeta.get("range_hi")
    if lo is None or hi is None:
        return None
    if "BREAKOUT_UP" in flags or "PRESSURE_UP" in flags:
        return float(hi)
    if "BREAKOUT_DOWN" in flags or "PRESSURE_DOWN" in flags:
        return float(lo)
    return None

# =========================
# BUILD SIGNAL FOR SYMBOL
# =========================
def build_signal(instId: str):
    c5 = get_okx_candles(instId, "5m", 120)
    c15 = get_okx_candles(instId, "15m", 120)

    price = c5[-1][4]
    flags = []
    score = 0

    comp5, _ = compression_ok(c5)
    if comp5:
        score += 1
        flags.append("COMP_5M")

    comp15, _ = compression_ok(c15)
    if comp15:
        score += 1
        flags.append("COMP_15M")

    if fake_dump_ok(c5):
        score += 1
        flags.append("FAKE_DUMP")

    if volume_spike_ok(c5):
        score += 1
        flags.append("VOL_SPIKE")

    br = breakout_ok(c5)
    if br:
        score += 1
        flags.append(f"BREAKOUT_{br}")

    conf = breakout_confirm_ok(c5)
    if conf:
        score += 1
        flags.append(f"BREAKOUT_CONFIRM_{conf}")

    if atr_expansion_ok(c5):
        score += 1
        flags.append("ATR_EXPANSION")

    pres, pmeta = liquidity_pressure(c5)
    if pres:
        score += 1
        flags.append(f"PRESSURE_{pres}")

    # V2: anti-pump shield (не ломает логику — просто штраф к score)
    score += anti_pump_penalty(c5, ANTI_PUMP_PCT_5M)

    direction_text, reasons, up_w, down_w = direction_hint(flags)
    entry, entry_reason = entry_engine(score, flags, direction_text, up_w, down_w)
    stage, stage_reason = smart_money_stage(score, flags)
    tgt = liquidity_target(pmeta, flags)

    acc_score = accumulation_bias(flags)

    return {
        "instId": instId,
        "price": price,
        "score": score,
        "acc_score": acc_score,
        "flags": flags,
        "direction": direction_text,
        "dir_reasons": reasons,
        "up_w": up_w,
        "down_w": down_w,
        "entry": entry,
        "entry_reason": entry_reason,
        "stage": stage,
        "stage_reason": stage_reason,
        "target": tgt,
        "pmeta": pmeta,
        "ts": int(time.time()),
    }

# =========================
# SCANNER (LEVEL 1 FAST FILTER)
# =========================
def is_bad_symbol(instId: str) -> bool:
    base = instId.replace(f"-{QUOTE}", "")
    for s in EXCLUDE_TOKENS_CONTAINS:
        if s in base:
            return True
    return False

def get_market_candidates():
    tickers = get_okx_spot_usdt_tickers()
    cands = []
    for t in tickers:
        instId = t.get("instId", "")
        if not instId.endswith(f"-{QUOTE}"):
            continue
        if is_bad_symbol(instId):
            continue

        try:
            vol_usdt = float(t.get("volCcy24h") or 0.0)
        except:
            vol_usdt = 0.0

        try:
            last = float(t.get("last") or 0.0)
            open24 = float(t.get("open24h") or 0.0)
            if open24 > 0:
                pct = (last - open24) / open24 * 100.0
            else:
                pct = 0.0
        except:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        # В режиме накопления: не режем по % движения (чтобы увидеть "тишину")
        if not ACCUMULATION_MODE:
            if abs(pct) < SCAN_MIN_PCT_24H:
                continue

        cands.append((instId, vol_usdt, pct))

    cands.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)
    return cands[:SCAN_TOP_N]

# =========================
# BTC MARKET REGIME (V2)
# =========================
def btc_regime():
    """
    Контекст рынка (risk-on / risk-off / neutral):
    если BTC летит вниз импульсом — лонги альтов опаснее.
    """
    try:
        sig = build_signal("BTC-USDT")
    except:
        return ("NEUTRAL", None)

    flags = set(sig.get("flags", []))
    if ("BREAKOUT_CONFIRM_DOWN" in flags and "ATR_EXPANSION" in flags and "VOL_SPIKE" in flags):
        return ("RISK_OFF", sig)
    if ("BREAKOUT_CONFIRM_UP" in flags and "ATR_EXPANSION" in flags and "VOL_SPIKE" in flags):
        return ("RISK_ON", sig)
    return ("NEUTRAL", sig)

def apply_regime_bias(sig, regime):
    # мягкий bias: не ломает логику, только ранжирование
    if regime == "RISK_OFF":
        if "ВВЕРХ" in sig["direction"]:
            sig["score"] -= 1
    elif regime == "RISK_ON":
        if "ВНИЗ" in sig["direction"]:
            sig["score"] -= 1
    return sig

# =========================
# TRADER INTERPRETATION (NEW)
# =========================
def interpret_combo(sig):
    """
    Подсказки-трактовки комбинаций, чтобы ты запоминал и видел смысл.
    НЕ меняет сигналы, только добавляет объяснение.
    """
    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))
    direction = sig.get("direction", "")
    entry = sig.get("entry", "")

    notes = []

    # 1) Накопление + давление у края = вероятная ликвидность рядом
    if acc >= 3 and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags):
        if "PRESSURE_DOWN" in flags:
            notes.append("🟣 COMP+PRESSURE_DOWN: цена у низа диапазона — стопы/ликвидность чаще снизу. Возможен ложный пролив вниз и возврат.")
        if "PRESSURE_UP" in flags:
            notes.append("🟣 COMP+PRESSURE_UP: цена у верха диапазона — ликвидность чаще сверху. Возможен ложный прокол вверх и откат.")

    # 2) Fake dump = снятие стопов (часто)
    if "FAKE_DUMP" in flags:
        notes.append("🟡 FAKE_DUMP: был прокол вниз и быстрый возврат — похоже на снятие стопов, возможен разворот/импульс.")

    # 3) Breakout без подтверждения = может быть ловушка
    if ("BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags) and ("BREAKOUT_CONFIRM_UP" not in flags and "BREAKOUT_CONFIRM_DOWN" not in flags):
        notes.append("🟠 BREAKOUT без CONFIRM: пробили уровень, но ещё не закрепились — часто бывает ловушка/тряска.")

    # 4) Confirm + ATR + Vol = реальный импульс
    if ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and ("ATR_EXPANSION" in flags) and ("VOL_SPIKE" in flags):
        notes.append("🟢 CONFIRM + ATR + VOL: движение подтверждено, импульс реальный (шанс продолжения выше).")

    # 5) ATR_EXPANSION = рынок выходит из тишины
    if "ATR_EXPANSION" in flags and acc >= 3:
        notes.append("🟢 ATR после накопления: рынок выходит из сжатия — часто начало 'выстрела'.")

    # 6) Stage подсказки
    if "🟣 ACCUMULATION" in stage:
        notes.append("🟣 STAGE=ACCUMULATION: идёт сжатие/накопление. Это 'до движения' — ждём триггер.")
    if "🟡 MANIPULATION" in stage:
        notes.append("🟡 STAGE=MANIPULATION: вероятен сбор ликвидности (тряска) перед настоящим импульсом.")
    if "🟢 EXPANSION" in stage:
        notes.append("🟢 STAGE=EXPANSION: движение уже пошло. Входы позднее — осторожнее, лучше ждать откат/структуру.")

    # 7) Баланс при сильном накоплении
    if acc >= 3 and "БАЛАНС" in direction:
        notes.append("⚖️ BALANCE при acc≥3: рынок прячет сторону. Часто потом выстрел резкий — держи уровни диапазона.")

    # 8) Entry подсказки (как себя вести)
    if "SAFE" in entry:
        notes.append("✅ SAFE: структура+подтверждение+импульс — самый чистый сценарий.")
    elif "AGGRESSIVE" in entry:
        notes.append("⚠️ AGGRESSIVE: ранний вход. Лучше маленький риск и подтверждать глазами на графике.")
    else:
        notes.append("⏳ WAIT: пока лучше наблюдать. Ждём CONFIRM/объём/ATR или fake move + возврат.")

    return notes

# =========================
# TRIGGER ENGINE (NEW)
# =========================
def is_trigger_event(state, sig):
    """
    TRIGGER = ранний старт после накопления.
    Условие:
      - ранее acc_score >= 3
      - сейчас появился импульс (CONFIRM) или (VOL + ATR)
    Плюс cooldown по монете, чтобы не спамить.
    """
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})
    now = int(time.time())

    # cooldown
    last_tr = int(ss.get("last_trigger_ts", 0) or 0)
    if now - last_tr < TRIGGER_COOLDOWN_SEC:
        return False

    prev_acc = int(ss.get("prev_acc_score", 0) or 0)
    flags = set(sig.get("flags", []))

    impulse_now = (
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) or
        ("VOL_SPIKE" in flags and "ATR_EXPANSION" in flags)
    )

    return (prev_acc >= 3) and impulse_now

def trigger_message(sig, regime):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🔥 TRIGGER — {sym} стартует после накопления")
    lines.append(f"⏱ Cycle: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"🧭 BTC regime: {regime}")
    lines.append(f"💵 {sig['price']:.6g}")
    lines.append(f"📊 {sig['score']}/10 | {sig['direction']} | acc={sig.get('acc_score', 0)}")
    lines.append(f"🎯 ENTRY: {sig['entry']}")
    lines.append(f"🧬 STAGE: {sig['stage']}")
    if sig.get("target") is not None:
        lines.append(f"🎯 Target: {sig['target']:.6g}")
    lines.append("")
    lines.append("⚡ Это момент старта. Проверь график и уровни диапазона.")
    return "\n".join(lines)

# =========================
# MESSAGE FORMATS
# =========================
def fmt_symbol(instId: str) -> str:
    return instId.replace("-USDT", "")

def msg_short(sig):
    lines = []
    lines.append(f"🧠 RADAR SHORT — {fmt_symbol(sig['instId'])}")
    lines.append(f"💵 {sig['price']:.6g}")
    lines.append(f"📊 {sig['score']}/10 | {sig['direction']} | acc={sig.get('acc_score', 0)}")
    lines.append(f"🎯 ENTRY: {sig['entry']}")
    lines.append(f"🧬 STAGE: {sig['stage']}")
    if sig["target"] is not None:
        lines.append(f"🎯 Target: {sig['target']:.6g}")
    return "\n".join(lines)

def msg_medium(sig):
    lines = []
    lines.append(f"🧠 RADAR MEDIUM — {fmt_symbol(sig['instId'])}")
    lines.append(f"💵 {sig['price']:.6g}")
    lines.append(f"📊 {sig['score']}/10 | {sig['direction']} (up={sig['up_w']}, down={sig['down_w']}) | acc={sig.get('acc_score', 0)}")
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")
    pm = sig.get("pmeta") or {}
    if pm.get("range_lo") is not None and pm.get("range_hi") is not None and pm.get("range_pct") is not None:
        lines.append(f"🧲 Range: {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | {pm['range_pct']:.2f}%")
    if sig["target"] is not None:
        lines.append(f"🎯 Target: {sig['target']:.6g}")
    if sig["flags"]:
        lines.append("Flags:")
        for f in sig["flags"][:12]:
            lines.append(f"• {f}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:10]:
            lines.append(f"• {n}")

    return "\n".join(lines)

def msg_full(sig):
    lines = []
    lines.append(f"🧠 RADAR FULL — {fmt_symbol(sig['instId'])}")
    lines.append(f"💵 {sig['price']:.6g}")
    lines.append(f"📊 Score: {sig['score']}/10 | acc={sig.get('acc_score', 0)}")
    lines.append(f"🎯 Direction: {sig['direction']} (up={sig['up_w']}, down={sig['down_w']})")
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")
    pm = sig.get("pmeta") or {}
    if pm.get("range_lo") is not None and pm.get("range_hi") is not None and pm.get("range_pct") is not None:
        lines.append(f"🧲 Range(lookback): {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | width≈{pm['range_pct']:.2f}%")
    if sig["target"] is not None:
        lines.append(f"🎯 Liquidity target: {sig['target']:.6g}")

    if sig["flags"]:
        lines.append("")
        lines.append("Флаги (что увидел бот):")
        for f in sig["flags"]:
            lines.append(f"• {f}")

    if sig.get("dir_reasons"):
        lines.append("")
        lines.append("Причины направления:")
        for r in sig["dir_reasons"][:12]:
            lines.append(f"• {r}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:12]:
            lines.append(f"• {n}")

    return "\n".join(lines)

def choose_detail_message(sig):
    if MESSAGE_MODE == "SHORT":
        return msg_short(sig)
    if MESSAGE_MODE == "MEDIUM":
        return msg_medium(sig)
    if MESSAGE_MODE == "FULL":
        return msg_full(sig)
    # AUTO:
    if sig["score"] >= EDGE_HIGH_SCORE:
        return msg_full(sig)
    if sig["score"] >= EDGE_MID_SCORE:
        return msg_medium(sig)
    return msg_short(sig)

def summary_message(alerts, cycle_info, regime):
    lines = []
    lines.append("🚨 SMART MONEY SCAN — MARKET SUMMARY")
    lines.append(f"⏱ Cycle: {cycle_info}")
    lines.append(f"🧭 BTC regime: {regime}")
    if not alerts:
        lines.append("Нет монет с edge по фильтрам (пока тихо).")
        return "\n".join(lines)

    lines.append(f"Top {min(ALERT_TOP_M, len(alerts))}:")
    for sig in alerts[:ALERT_TOP_M]:
        sym = fmt_symbol(sig["instId"])
        tgt = f" | tgt {sig['target']:.6g}" if sig["target"] is not None else ""
        acc = sig.get("acc_score", 0)
        lines.append(f"• {sym}: {sig['score']}/10 acc={acc} {sig['direction']} | {sig['entry']} | {sig['stage']}{tgt}")
    return "\n".join(lines)

# =========================
# PRE-MOVE MANIPULATION WATCH (V2)
# =========================
def is_pre_move_manip(sig):
    """
    Предупреждать ДО движения:
    - stage = MANIPULATION или ACCUMULATION
    - acc_score достаточно высокий
    - при этом нет явного "движ уже пошёл" (ATR+Confirm)
    """
    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))

    already_moving = ("ATR_EXPANSION" in flags) and ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags)
    if already_moving:
        return False

    if acc < MANIP_MIN_ACC_SCORE:
        return False

    if "🟡 MANIPULATION" in stage:
        return True
    if "🟣 ACCUMULATION" in stage and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags or "FAKE_DUMP" in flags):
        return True

    if ("FAKE_DUMP" in flags) and ("COMP_5M" in flags or "COMP_15M" in flags):
        return True

    return False

def manip_summary_message(watch, cycle_info, regime):
    lines = []
    lines.append("🟡 PRE-MOVE WATCH — MANIPULATION / ACCUMULATION")
    lines.append(f"⏱ Cycle: {cycle_info}")
    lines.append(f"🧭 BTC regime: {regime}")
    if not watch:
        lines.append("Тихо: явных pre-move зон нет.")
        return "\n".join(lines)

    lines.append(f"Top {min(MANIP_TOP_N, len(watch))}:")
    for sig in watch[:MANIP_TOP_N]:
        sym = fmt_symbol(sig["instId"])
        acc = sig.get("acc_score", 0)
        lines.append(f"• {sym}: acc={acc} | {sig['stage']} | {sig['direction']} | score={sig['score']}/10")
    lines.append("")
    lines.append("🎯 Идея: ловим выстрел после манипуляции (не прыгаем в первый памп).")
    return "\n".join(lines)

# =========================
# SPAM CONTROL (V2 cooldown)
# =========================
def should_alert_symbol(state, sig):
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})
    prev_score = ss.get("prev_score")
    prev_flags = ss.get("prev_flags", [])
    last_alert_ts = ss.get("last_alert_ts", 0)
    now = int(time.time())

    if now - int(last_alert_ts or 0) < ALERT_COOLDOWN_SEC:
        return False

    changed = (prev_score is None) or (sig["score"] != prev_score) or (sig["flags"] != prev_flags)
    crossed = (prev_score or 0) < ALERT_MIN_SCORE and sig["score"] >= ALERT_MIN_SCORE
    return changed or crossed

def should_manip_alert(state, sig):
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})
    last_ts = ss.get("last_manip_alert_ts", 0)
    prev_m_flags = ss.get("prev_manip_flags", [])
    now = int(time.time())

    if now - int(last_ts or 0) < MANIP_COOLDOWN_SEC:
        return False

    cur_flags = sig.get("flags", [])
    changed = (cur_flags != prev_m_flags)

    return changed or (last_ts == 0)

def update_symbol_state(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["prev_score"] = sig["score"]
    state["symbols"][sym]["prev_flags"] = sig["flags"]
    state["symbols"][sym]["last_ts"] = sig["ts"]
    # NEW: сохраняем накопление для TRIGGER
    state["symbols"][sym]["prev_acc_score"] = sig.get("acc_score", 0)

def mark_alert_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_alert_ts"] = sig["ts"]

def mark_manip_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_manip_alert_ts"] = sig["ts"]
    state["symbols"][sym]["prev_manip_flags"] = sig.get("flags", [])

def mark_trigger_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_trigger_ts"] = int(time.time())

# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN / CHAT_ID")

    state = load_state()
    send_telegram("🚀 SMART MONEY SCANNER — PRO MAX FINAL + V2 + INTERPRETER + TRIGGER started (OKX market scan)")

    while True:
        t0 = time.time()
        try:
            # BTC контекст рынка (V2)
            regime, btc_sig = btc_regime()

            candidates = get_market_candidates()

            alerts = []
            manip_watch = []

            for (instId, vol_usdt, pct) in candidates:
                try:
                    sig = build_signal(instId)
                    sig["vol_usdt"] = vol_usdt
                    sig["pct_24h"] = pct

                    # V2: применяем контекст BTC к score (мягко)
                    sig = apply_regime_bias(sig, regime)

                    # 🔥 TRIGGER (ранний старт) — ДО обычных алертов
                    if is_trigger_event(state, sig):
                        send_telegram(trigger_message(sig, regime))
                        mark_trigger_sent(state, sig)

                    # обычные алерты по edge
                    if sig["score"] >= ALERT_MIN_SCORE and should_alert_symbol(state, sig):
                        alerts.append(sig)
                        mark_alert_sent(state, sig)

                    # pre-move manipulation watch (отдельно)
                    if MANIP_ALERT_ENABLED and is_pre_move_manip(sig):
                        if should_manip_alert(state, sig):
                            manip_watch.append(sig)
                            mark_manip_sent(state, sig)

                    update_symbol_state(state, sig)

                    # gentle with OKX
                    time.sleep(0.15)
                except:
                    continue

            # сортировки
            alerts.sort(key=lambda s: (s["score"], abs(s.get("pct_24h", 0.0))), reverse=True)
            manip_watch.sort(key=lambda s: (int(s.get("acc_score", 0)), s.get("score", 0)), reverse=True)

            cycle_info = time.strftime("%Y-%m-%d %H:%M:%S")

            # 1) Summary по edge
            send_telegram(summary_message(alerts, cycle_info, regime))

            # 2) Detail по топовым edge
            for sig in alerts[:DETAIL_TOP_K]:
                send_telegram(choose_detail_message(sig))

            # 3) Отдельный блок: предупреждение о манипуляции/накоплении до движения
            if MANIP_ALERT_ENABLED:
                send_telegram(manip_summary_message(manip_watch, cycle_info, regime))
                for sig in manip_watch[:MANIP_DETAIL_TOP_K]:
                    # для pre-move — MEDIUM (чтобы видеть объяснения и привыкать)
                    send_telegram(msg_medium(sig))

            save_state(state)

        except Exception as e:
            send_telegram(f"❌ Scan Error:\n{str(e)}")

        dt = time.time() - t0
        sleep_for = max(1, POLL_SECONDS - int(dt))
        time.sleep(sleep_for)

