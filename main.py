import os
import time
import json
import math
import requests
from dotenv import load_dotenv
from priority_engine import find_global_priority, should_send_priority
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
# V2 ENV
# =========================
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC") or "1800")
ANTI_PUMP_PCT_5M = float(os.getenv("ANTI_PUMP_PCT_5M") or "6.0")

MANIP_ALERT_ENABLED = (os.getenv("MANIP_ALERT_ENABLED") or "1").strip() != "0"
MANIP_TOP_N = int(os.getenv("MANIP_TOP_N") or "6")
MANIP_DETAIL_TOP_K = int(os.getenv("MANIP_DETAIL_TOP_K") or "1")
MANIP_MIN_ACC_SCORE = int(os.getenv("MANIP_MIN_ACC_SCORE") or "3")
MANIP_COOLDOWN_SEC = int(os.getenv("MANIP_COOLDOWN_SEC") or "1800")

ACCUMULATION_MODE = (os.getenv("ACCUMULATION_MODE") or "0").strip() == "1"

# =========================
# V3 ENV (NEW — лучше профи)
# =========================
# Стакан (order book) — включить/выключить
ORDERBOOK_ENABLED = (os.getenv("ORDERBOOK_ENABLED") or "1").strip() != "0"
ORDERBOOK_SZ = int(os.getenv("ORDERBOOK_SZ") or "25")  # глубина стакана
ORDERBOOK_WALL_MULT = float(os.getenv("ORDERBOOK_WALL_MULT") or "2.2")  # "стена" в X раз больше среднего
ORDERBOOK_IMB_MIN = float(os.getenv("ORDERBOOK_IMB_MIN") or "0.18")  # минимальный дисбаланс (0..1)

# Sweep detector — снятие стопов (вверх/вниз) + возврат
SWEEP_LOOKBACK = int(os.getenv("SWEEP_LOOKBACK") or "20")
SWEEP_PIERCE_PCT = float(os.getenv("SWEEP_PIERCE_PCT") or "0.15")  # насколько прокол (в % от цены)
SWEEP_RECLAIM_ZONE = float(os.getenv("SWEEP_RECLAIM_ZONE") or "0.35")  # насколько закрылись обратно в диапазон

# Анти-шум пробоя: минимальная дистанция от уровня (в %)
MIN_BREAKOUT_DIST_PCT = float(os.getenv("MIN_BREAKOUT_DIST_PCT") or "0.10")
NEAR_BREAKOUT_PCT = float(os.getenv("NEAR_BREAKOUT_PCT") or "0.25")

# 3-уровневый триггер
TRIGGER_PRE_ACC = int(os.getenv("TRIGGER_PRE_ACC") or "3")
TRIGGER_PRE_COOLDOWN = int(os.getenv("TRIGGER_PRE_COOLDOWN") or "1800")

TRIGGER_START_COOLDOWN = int(os.getenv("TRIGGER_START_COOLDOWN") or "1800")
TRIGGER_CONFIRM_COOLDOWN = int(os.getenv("TRIGGER_CONFIRM_COOLDOWN") or "1800")

# =========================
# PRIORITY ALERT SYSTEM (ADDON — ничего не ломает)
# =========================
PRIORITY_ENABLED = (os.getenv("PRIORITY_ENABLED") or "1").strip() != "0"
PRIORITY_SCORE_MIN = int(os.getenv("PRIORITY_SCORE_MIN") or "7")
PRIORITY_ACC_MIN = int(os.getenv("PRIORITY_ACC_MIN") or "3")
PRIORITY_COOLDOWN_SEC = int(os.getenv("PRIORITY_COOLDOWN_SEC") or "2400")

# =========================
# PRO EDGE (NEW, умеренное усиление, без удаления логики)
# =========================
PRO_EDGE_ENABLED = (os.getenv("PRO_EDGE_ENABLED") or "1").strip() != "0"
PRO_EDGE_MIN_SCORE = int(os.getenv("PRO_EDGE_MIN_SCORE") or "6")           # сильнее чем ALERT_MIN_SCORE
PRO_EDGE_MAX_ALERTS_PER_CYCLE = int(os.getenv("PRO_EDGE_MAX_ALERTS") or "4")
PRO_EDGE_MIN_RANGE_PCT = float(os.getenv("PRO_EDGE_MIN_RANGE_PCT") or "0.40")  # отсекаем супер-флет
PRO_EDGE_REQUIRE_IMPULSE = (os.getenv("PRO_EDGE_REQUIRE_IMPULSE") or "1").strip() != "0"
PRO_EDGE_REJECT_BALANCE = (os.getenv("PRO_EDGE_REJECT_BALANCE") or "1").strip() != "0"

# OKX
OKX_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_BOOKS_URL = "https://www.okx.com/api/v5/market/books"  # NEW

# =========================
# HARD RULES / FILTERS
# =========================
EXCLUDE_TOKENS_CONTAINS = ["3L", "3S", "5L", "3M", "5M", "BULL", "BEAR", "UP", "DOWN"]
QUOTE = "USDT"

# =========================
# HTTP SESSION
# =========================
S = requests.Session()
S.headers.update({"User-Agent": "smart-money-radar/PRO-EDGE-4.0"})

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

def now_ts():
    return int(time.time())

# =========================
# OKX HELPERS
# =========================
def okx_get(url, params):
    r = S.get(url, params=params, timeout=TIMEOUT)
    if r.status_code == 429:
        time.sleep(2.3)
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
            candles.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
        except:
            pass
    if len(candles) < 30:
        raise RuntimeError(f"Candle parse failed {instId} {bar}")
    return candles

def get_okx_books(instId: str, sz: int = 25):
    arr = okx_get(OKX_BOOKS_URL, {"instId": instId, "sz": str(sz)})
    if not arr:
        return None
    ob = arr[0]
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    try:
        bids_pq = [(float(x[0]), float(x[1])) for x in bids]
        asks_pq = [(float(x[0]), float(x[1])) for x in asks]
    except:
        return None
    if not bids_pq or not asks_pq:
        return None
    return {"bids": bids_pq, "asks": asks_pq}

# =========================
# CANDLES FEATURES (PRO MAX)
# =========================
COMPRESSION_MULT = 0.82
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
    hi = max(highs)
    lo = min(lows)
    if last_close > hi * (1.0 + MIN_BREAKOUT_DIST_PCT / 100.0):
        return "UP"
    if last_close < lo * (1.0 - MIN_BREAKOUT_DIST_PCT / 100.0):
        return "DOWN"
    return None

def breakout_confirm_ok(candles, lookback=BREAKOUT_LOOKBACK, confirm_bars=BREAKOUT_CONFIRM_BARS):
    base = candles[-(lookback + confirm_bars + 1):-(confirm_bars + 1)]
    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)
    closes = [c[4] for c in candles[-confirm_bars:]]
    if all(cl > hi * (1.0 + MIN_BREAKOUT_DIST_PCT / 100.0) for cl in closes):
        return "UP"
    if all(cl < lo * (1.0 - MIN_BREAKOUT_DIST_PCT / 100.0) for cl in closes):
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
    def expected_move_pct(candles, pmeta, atr_period=14):
    """
    Оценка ожидаемого движения в %:
    - берём ширину диапазона range_pct (если есть)
    - и ATR% по 5m
    - возвращаем (min_move, max_move) в процентах
    """
    # range_pct
    range_pct = 0.0
    if pmeta and isinstance(pmeta, dict):
        try:
            range_pct = float(pmeta.get("range_pct") or 0.0)
        except:
            range_pct = 0.0

    # ATR% (примерно)
    try:
        trs = []
        for i in range(1, len(candles)):
            h = candles[i][2]
            l = candles[i][3]
            prev_close = candles[i-1][4]
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)

        if len(trs) >= atr_period and candles[-1][4] > 0:
            atr = sum(trs[-atr_period:]) / atr_period
            atr_pct = (atr / candles[-1][4]) * 100.0
        else:
            atr_pct = 0.0
    except:
        atr_pct = 0.0

    base = max(range_pct, atr_pct)

    # небольшая вилка
    min_move = max(0.5, base * 0.8)
    max_move = base * 1.6

    return round(min_move, 2), round(max_move, 2)

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


def near_breakout(pmeta, price, near_pct):
    if not pmeta or not isinstance(pmeta, dict):
        return None

    hi = pmeta.get("range_hi")
    lo = pmeta.get("range_lo")
    if hi is None or lo is None:
        return None

    try:
        hi = float(hi)
        lo = float(lo)
        price = float(price)
        near_pct = float(near_pct)
    except Exception:
        return None

    if price <= 0:
        return None

    dist_up = abs(hi - price) / price * 100.0
    dist_dn = abs(price - lo) / price * 100.0

    if dist_up <= near_pct:
        return "UP"
    if dist_dn <= near_pct:
        return "DOWN"
    return None

# =========================
# V3: ORDERBOOK EDGE (NEW)
# =========================
def orderbook_edge(instId: str):
    if not ORDERBOOK_ENABLED:
        return None

    ob = None
    try:
        ob = get_okx_books(instId, ORDERBOOK_SZ)
    except:
        return None

    if not ob:
        return None

    bids = ob["bids"]
    asks = ob["asks"]

    bid_sum = sum(q for _p, q in bids)
    ask_sum = sum(q for _p, q in asks)
    total = bid_sum + ask_sum
    if total <= 0:
        return None

    imb = (bid_sum - ask_sum) / total
    imb_abs = abs(imb)

    bid_sizes = [q for _p, q in bids]
    ask_sizes = [q for _p, q in asks]
    bid_avg = sum(bid_sizes) / max(len(bid_sizes), 1)
    ask_avg = sum(ask_sizes) / max(len(ask_sizes), 1)

    bid_wall = max(bid_sizes) > bid_avg * ORDERBOOK_WALL_MULT if bid_avg > 0 else False
    ask_wall = max(ask_sizes) > ask_avg * ORDERBOOK_WALL_MULT if ask_avg > 0 else False

    if imb_abs < ORDERBOOK_IMB_MIN and not (bid_wall or ask_wall):
        return {"ob_bias": "NEUTRAL", "imb": imb, "bid_wall": bid_wall, "ask_wall": ask_wall}

    if imb > ORDERBOOK_IMB_MIN or bid_wall:
        return {"ob_bias": "BIDS", "imb": imb, "bid_wall": bid_wall, "ask_wall": ask_wall}
    if imb < -ORDERBOOK_IMB_MIN or ask_wall:
        return {"ob_bias": "ASKS", "imb": imb, "bid_wall": bid_wall, "ask_wall": ask_wall}

    return {"ob_bias": "NEUTRAL", "imb": imb, "bid_wall": bid_wall, "ask_wall": ask_wall}

# =========================
# V3: SWEEP DETECTOR (NEW)
# =========================
def liquidity_sweep(candles, lookback=SWEEP_LOOKBACK):
    if len(candles) < lookback + 2:
        return None, {}

    seg = candles[-lookback-1:-1]
    hi = max(x[2] for x in seg)
    lo = min(x[3] for x in seg)

    _ts, o, h, l, c, _v = candles[-1]
    pierce_up = h > hi * (1.0 + SWEEP_PIERCE_PCT / 100.0)
    pierce_dn = l < lo * (1.0 - SWEEP_PIERCE_PCT / 100.0)

    rng = hi - lo
    if rng <= 0:
        return None, {"hi": hi, "lo": lo}

    reclaim_up = c < (hi - rng * SWEEP_RECLAIM_ZONE)
    reclaim_dn = c > (lo + rng * SWEEP_RECLAIM_ZONE)

    if pierce_up and reclaim_up:
        return "SWEEP_UP", {"hi": hi, "lo": lo, "close": c}
    if pierce_dn and reclaim_dn:
        return "SWEEP_DOWN", {"hi": hi, "lo": lo, "close": c}

    return None, {"hi": hi, "lo": lo, "close": c}

# =========================
# V2: Anti-pump + Accumulation score
# =========================
def anti_pump_penalty(candles, threshold_pct):
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
        up += 3; reasons.append("Закрепление ВВЕРХ (+3)")
    if "BREAKOUT_CONFIRM_DOWN" in flags:
        down += 3; reasons.append("Закрепление ВНИЗ (+3)")

    if "BREAKOUT_UP" in flags:
        up += 2; reasons.append("Пробой ВВЕРХ (+2)")
    if "BREAKOUT_DOWN" in flags:
        down += 2; reasons.append("Пробой ВНИЗ (+2)")

    if "PRESSURE_UP" in flags:
        up += 1; reasons.append("Давление к верху (+1)")
    if "PRESSURE_DOWN" in flags:
        down += 1; reasons.append("Давление к низу (+1)")

    if "FAKE_DUMP" in flags:
        up += 1; reasons.append("Снятие стопов вниз (+1)")

    if "SWEEP_UP" in flags:
        down += 1; reasons.append("Снятие стопов вверх (часто разворот вниз) (+1)")
    if "SWEEP_DOWN" in flags:
        up += 1; reasons.append("Снятие стопов вниз (часто разворот вверх) (+1)")

    if "VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags):
        up += 1; reasons.append("Объём поддержал ВВЕРХ (+1)")
    if "VOL_SPIKE" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags):
        down += 1; reasons.append("Объём поддержал ВНИЗ (+1)")

    if "ATR_EXPANSION" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags):
        up += 1; reasons.append("ATR ускорил ВВЕРХ (+1)")
    if "ATR_EXPANSION" in flags and ("BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags):
        down += 1; reasons.append("ATR ускорил ВНИЗ (+1)")

    if "OB_BIDS" in flags:
        up += 1; reasons.append("Стакан: перевес BID (+1)")
    if "OB_ASKS" in flags:
        down += 1; reasons.append("Стакан: перевес ASK (+1)")
    if "OB_WALL_BID" in flags:
        up += 1; reasons.append("Стена BID (+1)")
    if "OB_WALL_ASK" in flags:
        down += 1; reasons.append("Стена ASK (+1)")

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
    if ("FAKE_DUMP" in flags or "SWEEP_UP" in flags or "SWEEP_DOWN" in flags or
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

    # BIG MOVE FILTER
    if pmeta and pmeta.get("range_pct", 0) > 2.0:
        score += 1
        flags.append("BIG_RANGE")

    sw, sw_meta = liquidity_sweep(c5)
    if sw:
        score += 1
        flags.append(sw)

    ob_meta = None
    if ORDERBOOK_ENABLED:
        ob_meta = orderbook_edge(instId)
        if ob_meta:
            bias = ob_meta.get("ob_bias")

            if bias == "BIDS":
                score += 1
                flags.append("OB_BIDS")

            elif bias == "ASKS":
                score += 1
                flags.append("OB_ASKS")

            if ob_meta.get("bid_wall"):
                score += 1
                flags.append("OB_WALL_BID")

            if ob_meta.get("ask_wall"):
                score += 1
                flags.append("OB_WALL_ASK")

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
        "obmeta": ob_meta,
        "swmeta": sw_meta,
        "ts": now_ts(),
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
    if regime == "RISK_OFF":
        if "ВВЕРХ" in sig["direction"]:
            sig["score"] -= 1
    elif regime == "RISK_ON":
        if "ВНИЗ" in sig["direction"]:
            sig["score"] -= 1
    return sig

# =========================
# PRO EDGE FILTER (NEW)
# =========================
def pro_edge_filter(sig, regime):
    """
    Умеренное усиление без удаления логики:
    - меньше шума
    - оставляем ранние (AGGRESSIVE) но только если есть импульс
    - не трогаем triggers / watch / summary
    """
    if not PRO_EDGE_ENABLED:
        return True

    flags = set(sig.get("flags", []))
    score = int(sig.get("score", 0))
    direction = sig.get("direction", "")
    acc = int(sig.get("acc_score", 0))

    if score < PRO_EDGE_MIN_SCORE:
        return False

    if PRO_EDGE_REJECT_BALANCE and ("БАЛАНС" in direction):
        return False

    pm = sig.get("pmeta") or {}
    range_pct = pm.get("range_pct")
    if range_pct is not None and float(range_pct) < float(PRO_EDGE_MIN_RANGE_PCT):
        return False

    if PRO_EDGE_REQUIRE_IMPULSE:
        strong_impulse = (
            ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) or
            ("ATR_EXPANSION" in flags and "VOL_SPIKE" in flags) or
            ("VOL_SPIKE" in flags and ("BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags))
        )
        if not strong_impulse:
            return False

    # BTC bias (чуть сильнее)
    if regime == "RISK_OFF" and "ВВЕРХ" in direction:
        return False
    if regime == "RISK_ON" and "ВНИЗ" in direction:
        return False

    return True

# =========================
# TRADER INTERPRETATION
# =========================
def interpret_combo(sig):
    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))
    direction = sig.get("direction", "")
    entry = sig.get("entry", "")

    notes = []

    if acc >= 3 and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags):
        if "PRESSURE_DOWN" in flags:
            notes.append("🟣 Накопление + цена у низа диапазона: снизу часто стопы лонгов. Возможен ложный пролив и возврат.")
        if "PRESSURE_UP" in flags:
            notes.append("🟣 Накопление + цена у верха диапазона: сверху часто стопы шортов. Возможен ложный прокол и откат.")

    if "FAKE_DUMP" in flags:
        notes.append("🟡 FAKE_DUMP: прокол вниз и быстрый возврат — похоже на снятие стопов снизу.")

    if "SWEEP_UP" in flags:
        notes.append("💣 SWEEP_UP: прокол верхов + возврат внутрь — сняли стопы шортов сверху, часто потом идут вниз.")
    if "SWEEP_DOWN" in flags:
        notes.append("💣 SWEEP_DOWN: прокол низов + возврат внутрь — сняли стопы лонгов снизу, часто потом идут вверх.")

    if ("BREAKOUT_UP" in flags or "BREAKOUT_DOWN" in flags) and ("BREAKOUT_CONFIRM_UP" not in flags and "BREAKOUT_CONFIRM_DOWN" not in flags):
        notes.append("🟠 Пробой без закрепления: возможна ловушка/вытряхивание.")

    if ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and ("ATR_EXPANSION" in flags) and ("VOL_SPIKE" in flags):
        notes.append("🟢 Закрепление + ATR + объём: движение подтверждено, шанс продолжения выше.")

    if "OB_BIDS" in flags:
        notes.append("📘 Стакан: перевес покупателей (BID). Это усиливает лонг-сценарий.")
    if "OB_ASKS" in flags:
        notes.append("📘 Стакан: перевес продавцов (ASK). Это усиливает шорт-сценарий.")
    if "OB_WALL_BID" in flags:
        notes.append("🧱 Стена BID: рядом крупная заявка — часто поддержка.")
    if "OB_WALL_ASK" in flags:
        notes.append("🧱 Стена ASK: рядом крупная заявка — часто сопротивление.")

    if "🟣 ACCUMULATION" in stage:
        notes.append("🟣 STAGE=ACCUMULATION: идёт сжатие. Это зона ДО движения — ждём триггер.")
    if "🟡 MANIPULATION" in stage:
        notes.append("🟡 STAGE=MANIPULATION: вероятен сбор ликвидности перед импульсом.")
    if "🟢 EXPANSION" in stage:
        notes.append("🟢 STAGE=EXPANSION: движение уже пошло. Лучше входить по откату/структуре.")

    if acc >= 3 and "БАЛАНС" in direction:
        notes.append("⚖️ Баланс при сильном накоплении: рынок прячет сторону. Часто потом резкий выстрел.")

    if "SAFE" in entry:
        notes.append("✅ SAFE: самый чистый сценарий по структуре.")
    elif "AGGRESSIVE" in entry:
        notes.append("⚠️ AGGRESSIVE: ранний вход — лучше маленький риск.")
    else:
        notes.append("⏳ WAIT: пока наблюдаем — ждём подтверждение/объём/ATR/свип.")

    return notes

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
        for f in sig["flags"][:14]:
            lines.append(f"• {f}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:12]:
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
        for r in sig["dir_reasons"][:14]:
            lines.append(f"• {r}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:16]:
            lines.append(f"• {n}")

    return "\n".join(lines)

def choose_detail_message(sig):
    if MESSAGE_MODE == "SHORT":
        return msg_short(sig)
    if MESSAGE_MODE == "MEDIUM":
        return msg_medium(sig)
    if MESSAGE_MODE == "FULL":
        return msg_full(sig)
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
    if "🟣 ACCUMULATION" in stage and ("PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags or "FAKE_DUMP" in flags or "SWEEP_UP" in flags or "SWEEP_DOWN" in flags):
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
# SPAM CONTROL (cooldowns)
# =========================
def should_alert_symbol(state, sig):
    sym = sig["instId"]
    ss = state["symbols"].get(sym, {})
    prev_score = ss.get("prev_score")
    prev_flags = ss.get("prev_flags", [])
    last_alert_ts = ss.get("last_alert_ts", 0)
    now = now_ts()

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
    now = now_ts()

    if now - int(last_ts or 0) < MANIP_COOLDOWN_SEC:
        return False

    cur_flags = sig.get("flags", [])
    changed = (cur_flags != prev_m_flags)
    return changed or (last_ts == 0)

def mark_alert_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_alert_ts"] = sig["ts"]

def mark_manip_sent(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["last_manip_alert_ts"] = sig["ts"]
    state["symbols"][sym]["prev_manip_flags"] = sig.get("flags", [])

def update_symbol_state(state, sig):
    sym = sig["instId"]
    state["symbols"].setdefault(sym, {})
    state["symbols"][sym]["prev_score"] = sig["score"]
    state["symbols"][sym]["prev_flags"] = sig["flags"]
    state["symbols"][sym]["last_ts"] = sig["ts"]

# =========================
# PRIORITY ALERT SYSTEM (ADDON — слой сверху)
# =========================
def priority_allowed(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_priority_ts", 0) or 0)
    return (now_ts() - last) >= PRIORITY_COOLDOWN_SEC

def mark_priority(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_priority_ts"] = now_ts()

def is_priority_signal(sig):
    if not PRIORITY_ENABLED:
        return False

    score = int(sig.get("score", 0))
    acc = int(sig.get("acc_score", 0))
    flags = set(sig.get("flags", []))

    # фильтр микро-флета (чтобы не спамило в супер-узком диапазоне)
    pm = sig.get("pmeta") or {}
    range_pct = pm.get("range_pct")
    if range_pct is not None and float(range_pct) < 0.35:
        return False

    if score < PRIORITY_SCORE_MIN:
        return False
    if acc < PRIORITY_ACC_MIN:
        return False

    strong_confirm = (
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and
        ("ATR_EXPANSION" in flags) and
        ("VOL_SPIKE" in flags)
    )

    smart_money_extra = (
        ("SWEEP_UP" in flags or "SWEEP_DOWN" in flags) or
        ("FAKE_DUMP" in flags) or
        ("OB_BIDS" in flags or "OB_ASKS" in flags) or
        ("OB_WALL_BID" in flags or "OB_WALL_ASK" in flags)
    )

    return strong_confirm or smart_money_extra

def msg_priority(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"⭐ PRIORITY ALERT — {sym}")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | acc={sig.get('acc_score',0)}")
    lines.append(f"🧭 {sig['direction']} | {sig['entry']} | {sig['stage']}")
    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")
    if sig.get("flags"):
        fl = ", ".join(sig["flags"][:10])
        lines.append(f"Flags: {fl}")
    return "\n".join(lines)

# =========================
# V3: 3-LEVEL TRIGGER (NEW)
# =========================
def trigger_allowed(state, instId, key, cooldown_sec):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get(key, 0) or 0)
    return (now_ts() - last) >= cooldown_sec

def trigger_mark(state, instId, key):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId][key] = now_ts()

def is_pre_trigger(sig):

    flags = set(sig.get("flags", []))
    acc = int(sig.get("acc_score", 0))

    if acc < TRIGGER_PRE_ACC:
        return False

    # если уже есть импульс — это уже не PRE
    if "ATR_EXPANSION" in flags:
        return False

    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return False

    accumulation = (
        "COMP_5M" in flags or
        "COMP_15M" in flags
    )

    liquidity = (
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags or
        "FAKE_DUMP" in flags or
        "SWEEP_UP" in flags or
        "SWEEP_DOWN" in flags
    )

    near = ("NEAR_BREAKOUT_UP" in flags) or ("NEAR_BREAKOUT_DOWN" in flags)
    return (accumulation and liquidity) or (near and liquidity)
def is_start_trigger(sig):
    flags = set(sig.get("flags", []))
    acc = int(sig.get("acc_score", 0))
    if acc < TRIGGER_PRE_ACC:
        return False
    impulse = ("ATR_EXPANSION" in flags) or ("VOL_SPIKE" in flags) or ("BREAKOUT_UP" in flags) or ("BREAKOUT_DOWN" in flags)
    return impulse

def is_confirm_trigger(sig):
    flags = set(sig.get("flags", []))
    return (("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and ("ATR_EXPANSION" in flags) and ("VOL_SPIKE" in flags))

def msg_pre_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🟡 PRE-TRIGGER — зона перед выстрелом: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | acc={sig.get('acc_score',0)} | {sig['direction']}")
    lines.append("Смысл: здесь вероятно собирают ликвидность. Готовь уровни диапазона.")
    return "\n".join(lines)

def msg_start_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🔥 TRIGGER START — старт из накопления: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | acc={sig.get('acc_score',0)} | {sig['direction']}")
    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")
    lines.append("Действие: открыть график и искать вход по структуре (малый риск).")
    return "\n".join(lines)

def msg_confirm_trigger(sig):
    sym = fmt_symbol(sig["instId"])
    lines = []
    lines.append(f"🚀 CONFIRM TRIGGER — самый чистый импульс: {sym}")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | {sig['direction']}")
    lines.append("Условия: CONFIRM + ATR + VOL (шанс продолжения выше).")
    return "\n".join(lines)

# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN / CHAT_ID")

    state = load_state()
    send_telegram("🚀 SMART MONEY SCANNER — PRO EDGE v4 started (OKX market scan)")

    while True:
        t0 = time.time()
        try:
            regime, _btc = btc_regime()
            candidates = get_market_candidates()

            alerts = []
            manip_watch = []

            for (instId, vol_usdt, pct) in candidates:
                try:
                    sig = build_signal(instId)
                    sig["vol_usdt"] = vol_usdt
                    sig["pct_24h"] = pct

                    sig = apply_regime_bias(sig, regime)

                    # =====================
                    # PRIORITY ALERT (оставляем, но усиливаем качеством)
                    # =====================
                    if is_priority_signal(sig) and priority_allowed(state, instId):
                        if pro_edge_filter(sig, regime):
                            send_telegram(msg_priority(sig))
                            mark_priority(state, instId)

                    # =====================
                    # ОБЫЧНЫЕ ALERTS (теперь через PRO EDGE)
                    # =====================
                    if pro_edge_filter(sig, regime):
                        if sig["score"] >= ALERT_MIN_SCORE and should_alert_symbol(state, sig):
                            alerts.append(sig)
                            mark_alert_sent(state, sig)

                    # =====================
                    # PRE-MOVE WATCH (оставляем как есть)
                    # =====================
                    if MANIP_ALERT_ENABLED and is_pre_move_manip(sig):
                        if should_manip_alert(state, sig):
                            manip_watch.append(sig)
                            mark_manip_sent(state, sig)

                    # =====================
                    # V3 triggers (оставляем как есть)
                    # =====================
                    if is_pre_trigger(sig) and trigger_allowed(state, instId, "last_pre_trigger_ts", TRIGGER_PRE_COOLDOWN):
                        send_telegram(msg_pre_trigger(sig))
                        trigger_mark(state, instId, "last_pre_trigger_ts")

                    if is_start_trigger(sig) and trigger_allowed(state, instId, "last_start_trigger_ts", TRIGGER_START_COOLDOWN):
                        send_telegram(msg_start_trigger(sig))
                        trigger_mark(state, instId, "last_start_trigger_ts")

                    if is_confirm_trigger(sig) and trigger_allowed(state, instId, "last_confirm_trigger_ts", TRIGGER_CONFIRM_COOLDOWN):
                        send_telegram(msg_confirm_trigger(sig))
                        trigger_mark(state, instId, "last_confirm_trigger_ts")

                    update_symbol_state(state, sig)
                    time.sleep(0.14)
                except:
                    continue

                    # =====================
            # сортировка + ограничение шума
            # =====================
            alerts.sort(key=lambda s: (s["score"], abs(s.get("pct_24h", 0.0))), reverse=True)
            alerts = alerts[:PRO_EDGE_MAX_ALERTS_PER_CYCLE]
            manip_watch.sort(key=lambda s: (int(s.get("acc_score", 0)), s.get("score", 0)), reverse=True)

            # =====================
            # GLOBAL PRIORITY ENGINE
            # =====================
            if PRIORITY_ENABLED:
                try:
                    priority_list = find_global_priority(alerts)

                    for sig in priority_list:
                        if should_send_priority(state, sig["instId"]):
                            send_telegram(msg_priority(sig))
                            mark_priority(state, sig["instId"])

                except Exception:
                    pass

            # PRO EDGE ограничитель: максимум N алертов за цикл
            if PRO_EDGE_ENABLED and PRO_EDGE_MAX_ALERTS_PER_CYCLE > 0:
                alerts = alerts[:PRO_EDGE_MAX_ALERTS_PER_CYCLE]

            cycle_info = time.strftime("%Y-%m-%d %H:%M:%S")

            # summary всегда
            send_telegram(summary_message(alerts, cycle_info, regime))

            # детали по лучшим
            for sig in alerts[:DETAIL_TOP_K]:
                send_telegram(choose_detail_message(sig))

            # pre-move watch
            if MANIP_ALERT_ENABLED:
                send_telegram(manip_summary_message(manip_watch, cycle_info, regime))
                for sig in manip_watch[:MANIP_DETAIL_TOP_K]:
                    send_telegram(msg_medium(sig))

            save_state(state)   

        except Exception as e:
            send_telegram(f"❌ Scan Error:\n{str(e)}")

        dt = time.time() - t0
        sleep_for = max(1, POLL_SECONDS - int(dt))
        time.sleep(sleep_for)
