import os
import time
import json
import math
import requests
import traceback
from dotenv import load_dotenv
from priority_engine import find_global_priority, should_send_priority
from wall_detector import WallTracker
from continuation_engine import continuation_engine
from signal_tier import get_signal_tier
from sniper_engine import sniper_signal
from signal_analyst import init_db, save_signal, get_open_signals, close_signal
from ai_scoring import get_ai_multiplier
from market_context import apply_market_context

# =========================
# SIGNAL COOLDOWN
# =========================
SIGNAL_COOLDOWN = 900   # 15 минут
last_signal_time = {}

load_dotenv()
wall_tracker = WallTracker()

# =========================
# ENV
# =========================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("CHAT_ID") or "").strip()

MESSAGE_MODE = (os.getenv("MESSAGE_MODE") or "AUTO").upper()   # AUTO / SHORT / MEDIUM / FULL

EDGE_MID_SCORE = int(os.getenv("EDGE_MID_SCORE") or "4")
EDGE_HIGH_SCORE = int(os.getenv("EDGE_HIGH_SCORE") or "7")

POLL_SECONDS = int(os.getenv("POLL_SECONDS") or "60")
TIMEOUT = int(os.getenv("TIMEOUT") or "12")
STATE_FILE = os.getenv("STATE_FILE") or "state.json"
RESULT_CHECK_SEC = int(os.getenv("RESULT_CHECK_SEC") or "900")

SCAN_TOP_N = int(os.getenv("SCAN_TOP_N") or "300")
SCAN_MIN_VOL_USDT = float(os.getenv("SCAN_MIN_VOL_USDT") or "800000")
SCAN_MIN_PCT_24H = float(os.getenv("SCAN_MIN_PCT_24H") or "2")

ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE") or "4")
ALERT_TOP_M = int(os.getenv("ALERT_TOP_M") or "8")
DETAIL_TOP_K = int(os.getenv("DETAIL_TOP_K") or "1")
MIN_SCORE = int(os.getenv("MIN_SCORE") or "4")

# =========================
# V2 ENV
# =========================
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC") or "1800")
ANTI_PUMP_PCT_5M = float(os.getenv("ANTI_PUMP_PCT_5M") or "9.0")

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
START_MAX_DIST_PCT = float(os.getenv("START_MAX_DIST_PCT") or "0.8")
PRE_MIN_EXPECTED_MOVE_PCT = float(os.getenv("PRE_MIN_EXPECTED_MOVE_PCT") or "2.3")
USE_RSI_FILTER = int(os.getenv("USE_RSI_FILTER", "1"))

# ==============================
# 🧨 LIQUIDITY VACUUM SETTINGS
# ==============================

VAC_LOOKBACK = 12
VAC_VOL_MULT = 2.2
VAC_RANGE_COMPRESSION = 0.9

RSI_FAST_LEN = int(os.getenv("RSI_FAST_LEN", "7"))
RSI_SLOW_LEN = int(os.getenv("RSI_SLOW_LEN", "14"))

RSI_OB_WARN = float(os.getenv("RSI_OB_WARN", "74"))
RSI_OB_BLOCK = float(os.getenv("RSI_OB_BLOCK", "80"))

RSI_OS_WARN = float(os.getenv("RSI_OS_WARN", "26"))
RSI_OS_BLOCK = float(os.getenv("RSI_OS_BLOCK", "20"))

BLOCK_AGGRESSIVE_ON_RSI_EXTREME = int(os.getenv("BLOCK_AGGRESSIVE_ON_RSI_EXTREME", "1"))

# 3-уровневый триггер
TRIGGER_PRE_ACC = int(os.getenv("TRIGGER_PRE_ACC") or "3")
TRIGGER_PRE_COOLDOWN = int(os.getenv("TRIGGER_PRE_COOLDOWN") or "1800")

TRIGGER_START_COOLDOWN = int(os.getenv("TRIGGER_START_COOLDOWN") or "1800")
TRIGGER_CONFIRM_COOLDOWN = int(os.getenv("TRIGGER_CONFIRM_COOLDOWN") or "1800")
SAFE_ENTRY_SUPPRESS_SEC = int(os.getenv("SAFE_ENTRY_SUPPRESS_SEC") or "3600")

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
PRO_EDGE_MIN_SCORE = int(os.getenv("PRO_EDGE_MIN_SCORE") or "5")           # сильнее чем ALERT_MIN_SCORE
PRO_EDGE_MAX_ALERTS_PER_CYCLE = int(os.getenv("PRO_EDGE_MAX_ALERTS") or "4")
PRO_EDGE_MIN_RANGE_PCT = float(os.getenv("PRO_EDGE_MIN_RANGE_PCT") or "0.40")  # отсекаем супер-флет
PRO_EDGE_REQUIRE_IMPULSE = (os.getenv("PRO_EDGE_REQUIRE_IMPULSE") or "0").strip() != "0"
PRO_EDGE_REJECT_BALANCE = (os.getenv("PRO_EDGE_REJECT_BALANCE") or "1").strip() != "0"

# OKX
OKX_TICKERS_URL = "https://www.okx.com/api/v5/market/tickers"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_BOOKS_URL = "https://www.okx.com/api/v5/market/books"  # NEW
# =========================
# EXCHANGE SWITCH (NEW)
# =========================
EXCHANGE = "BYBIT"
BYBIT_CATEGORY = "linear"

# BYBIT
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_ORDERBOOK_URL = "https://api.bybit.com/v5/market/orderbook"


import time
import requests

def bybit_get(url, params, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()

            if data.get("retCode") == 0:
                return data

            # RATE LIMIT
            if data.get("retCode") == 10006:
                print("⚠️ BYBIT RATE LIMIT — sleeping 1.5 sec")
                time.sleep(1.5)
                continue

            raise RuntimeError(f"BYBIT bad response: {str(data)[:250]}")

        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.0)

    raise RuntimeError("BYBIT failed after retries")


def get_bybit_tickers_linear():
    res = bybit_get(BYBIT_TICKERS_URL, {"category": "linear"})
    result = res.get("result") or {}
    lst = result.get("list") or []
    return lst


def get_bybit_candles(symbol: str, interval: str, limit: int = 200):
    try:
        res = bybit_get(
            BYBIT_KLINE_URL,
            {
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": str(limit)
            }
        )

        result = res.get("result") or {}
        lst = result.get("list") or []

        if not lst or len(lst) < 30:
            print(f"⚠️ Not enough bybit candles for {symbol} {interval}")
            return []

        # Bybit returns newest -> oldest
        lst.reverse()

        candles = []
        for c in lst:
            try:
                candles.append([
                    int(c[0]),
                    float(c[1]),
                    float(c[2]),
                    float(c[3]),
                    float(c[4]),
                    float(c[5])
                ])
            except:
                continue

        if len(candles) < 30:
            print(f"⚠️ Candle parse failed {symbol} {interval}")
            return []

        return candles

    except Exception as e:
        print(f"❌ get_bybit_candles error {symbol} {interval}: {e}")
        return []

def get_bybit_books(symbol: str, limit: int = 25):
    res = bybit_get(BYBIT_ORDERBOOK_URL, {"category": "linear", "symbol": symbol, "limit": str(limit)})
    result = res.get("result") or {}
    bids = result.get("b") or []
    asks = result.get("a") or []
    try:
        bids_pq = [(float(x[0]), float(x[1])) for x in bids]
        asks_pq = [(float(x[0]), float(x[1])) for x in asks]
    except:
        return None
    if not bids_pq or not asks_pq:
        return None
    return {"bids": bids_pq, "asks": asks_pq}

is_bybit = lambda: (EXCHANGE or "OKX").upper() == "BYBIT"


def btc_symbol():
    return "BTCUSDT" if is_bybit() else "BTC-USDT"


def normalize_symbol(instId: str) -> str:
    if is_bybit():
        return instId.replace("-", "")
    return instId


def fetch_candles(instId: str, bar: str, limit: int = 120):
    if is_bybit():
        sym = normalize_symbol(instId)
        if bar == "5m":
            return get_bybit_candles(sym, "5", max(200, limit))
        if bar == "15m":
            return get_bybit_candles(sym, "15", max(200, limit))
        raise RuntimeError(f"Bybit bar not supported: {bar}")
    return get_okx_candles(instId, bar, limit)


def fetch_books(instId: str, sz: int = 25):
    if is_bybit():
        sym = normalize_symbol(instId)
        return get_bybit_books(sym, limit=sz)
    return get_okx_books(instId, sz)

# ==============================
# ⚡ BYBIT LIQUIDATIONS API
# ==============================

def fetch_bybit_liquidations(instId: str):

    if not is_bybit():
        return []

    try:
        sym = normalize_symbol(instId)

        url = "https://api.bybit.com/v5/market/liquidation"

        params = {
            "category": "linear",
            "symbol": sym,
            "limit": "20"
        }

        res = bybit_get(url, params)

        return res.get("list") or []

    except Exception:
        return []

# ==============================
# 💥 LIQUIDATION RADAR
# ==============================

def liquidation_radar(liqs):

    if not liqs:
        return None

    long_liq = 0
    short_liq = 0

    for l in liqs:

        try:
            side = l.get("side")
            size = float(l.get("size", 0))

            if side == "Sell":
                long_liq += size

            if side == "Buy":
                short_liq += size

        except:
            continue

    if long_liq > short_liq * 1.8:
        return "LONG_LIQUIDATIONS"

    if short_liq > long_liq * 1.8:
        return "SHORT_LIQUIDATIONS"

    return None
# ==============================
# 📊 BYBIT OPEN INTEREST API
# ==============================

def fetch_bybit_open_interest(instId: str, limit: int = 20):

    if not is_bybit():
        return []

    try:
        sym = normalize_symbol(instId)

        url = "https://api.bybit.com/v5/market/open-interest"

        params = {
            "category": "linear",
            "symbol": sym,
            "intervalTime": "5min",
            "limit": str(limit)
        }

        res = bybit_get(url, params)

        return res.get("list") or []

    except Exception:
        return []

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

        # защита от слишком длинных сообщений
        text = str(text)[:4000]

        S.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=10
        )

    except Exception as e:
        print(f"TELEGRAM ERROR: {e}")

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

def get_last_price(symbol: str) -> float:
    candles = fetch_candles(symbol, "5m", 2)
    if not candles:
        raise RuntimeError(f"Нет свечей для {symbol}")
    return float(candles[-1][4])

def direction_code_from_text(direction_text: str) -> str:
    txt = str(direction_text)

    if "ВВЕРХ" in txt:
        return "UP"

    if "ВНИЗ" in txt:
        return "DOWN"

    return "FLAT"

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

    wick_strong = (
        (body > 0 and lower_wick > body * FAKEDUMP_WICK_MULT)
        or (body == 0 and lower_wick > rng * 0.4)
    )

    return pierced and recovered and wick_strong


# =========================
# BREAKOUT DETECTOR
# =========================

def breakout_ok(candles, lookback=BREAKOUT_LOOKBACK):

    if len(candles) < lookback + 1:
        return None

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





# =========================
# EARLY EDGE DETECTOR
# =========================

def early_edge_detector(candles, flags):

    if len(candles) < 20:
        return False

    highs = [c[2] for c in candles[-20:]]
    lows = [c[3] for c in candles[-20:]]
    closes = [c[4] for c in candles[-20:]]

    hi = max(highs)
    lo = min(lows)

    price = closes[-1]

    range_pct = (hi - lo) / price * 100

    compression = range_pct < 1.2

    flow_up = closes[-1] > closes[-2] > closes[-3]
    flow_down = closes[-1] < closes[-2] < closes[-3]

    pressure = "PRESSURE_UP" in flags or "PRESSURE_DOWN" in flags

    if compression and pressure and (flow_up or flow_down):
        return True

    return False

    # =========================
# LIQUIDITY SWEEP DETECTOR
# =========================

def liquidity_sweep_ok(candles, lookback=20):

    if len(candles) < lookback + 2:
        return None

    recent = candles[-lookback-1:-1]

    hi = max(c[2] for c in recent)
    lo = min(c[3] for c in recent)

    last = candles[-1]

    last_high = last[2]
    last_low = last[3]
    last_close = last[4]

    # сняли стопы сверху
    if last_high > hi and last_close < hi:
        return "SWEEP_UP"

    # сняли стопы снизу
    if last_low < lo and last_close > lo:
        return "SWEEP_DOWN"

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
    
def trap_detector(candles, lookback=12):

    if len(candles) < lookback + 2:
        return None

    base = candles[-lookback-1:-1]

    hi = max(x[2] for x in base)
    lo = min(x[3] for x in base)

    last = candles[-1]

    o = last[1]
    h = last[2]
    l = last[3]
    c = last[4]

    rng = h - l

    if rng <= 0:
        return None

    body = abs(c - o)

    # bull trap
    if h > hi and c < hi and body < rng * 0.6:
        return "BULL_TRAP"

    # bear trap
    if l < lo and c > lo and body < rng * 0.6:
        return "BEAR_TRAP"

    return None


# ==============================
# STOP HUNT DETECTOR
# ==============================

def stop_hunt_detector(candles, lookback=15):

    if len(candles) < lookback + 2:
        return None

    segment = candles[-lookback-1:-1]

    hi = max(c[2] for c in segment)
    lo = min(c[3] for c in segment)

    last = candles[-1]

    o = last[1]
    h = last[2]
    l = last[3]
    c = last[4]

    rng = h - l

    if rng <= 0:
        return None

    body = abs(c - o)

    if h > hi and c < hi and body < rng * 0.6:
        return "STOP_HUNT_UP"

    if l < lo and c > lo and body < rng * 0.6:
        return "STOP_HUNT_DOWN"

    return None

# ==============================
# 🧨 LIQUIDITY VACUUM DETECTOR
# ==============================

def liquidity_vacuum_ok(candles, orderbook=None):

    try:

        if len(candles) < VAC_LOOKBACK + 3:
            return False

        vols = [float(c[5]) for c in candles[-VAC_LOOKBACK:]]
        highs = [float(c[2]) for c in candles[-VAC_LOOKBACK:]]
        lows = [float(c[3]) for c in candles[-VAC_LOOKBACK:]]

        ranges = [h - l for h, l in zip(highs, lows)]

        avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
        last_vol = vols[-1]

        avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
        last_range = ranges[-1]

        vol_spike = last_vol > avg_vol * VAC_VOL_MULT
        compression = last_range < avg_range * VAC_RANGE_COMPRESSION

        # NEW: проверяем ликвидность стакана
        ob_thin = False

        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])

            bid_sum = sum(q for _, q in bids)
            ask_sum = sum(q for _, q in asks)

            total_liq = bid_sum + ask_sum

            # если ликвидность маленькая → vacuum
            if total_liq > 0 and total_liq < 50000:
                ob_thin = True

        if vol_spike and compression:
            if ob_thin:
                return True

    except Exception:
        return False

    return False
# ==============================
# 🐋 WHALE ACCUMULATION DETECTOR
# ==============================

def whale_accumulation_ok(candles):

    if not candles or len(candles) < 6:
        return False

    try:
        vols = [float(c[5]) for c in candles[-6:]]
        highs = [float(c[2]) for c in candles[-6:]]
        lows = [float(c[3]) for c in candles[-6:]]

        avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
        last_vol = vols[-1]

        ranges = [h - l for h, l in zip(highs, lows)]
        avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
        last_range = ranges[-1]

        if last_vol > avg_vol * 2 and last_range < avg_range * 0.8:
            return True

    except Exception:
        return False

    return False

# ==============================
# 🐋 WHALE FLOW RADAR
# ==============================

def whale_flow_radar(candles):

    if len(candles) < 8:
        return False

    vols = [float(c[5]) for c in candles[-8:]]
    highs = [float(c[2]) for c in candles[-8:]]
    lows = [float(c[3]) for c in candles[-8:]]

    ranges = [h - l for h, l in zip(highs, lows)]

    avg_vol = sum(vols[:-1]) / max(len(vols[:-1]), 1)
    last_vol = vols[-1]

    avg_range = sum(ranges[:-1]) / max(len(ranges[:-1]), 1)
    last_range = ranges[-1]

    volume_build = last_vol > avg_vol * 1.6
    price_hold = last_range < avg_range * 0.9

    if volume_build and price_hold:
        return True

    return False

# ==============================
# 📈 OPEN INTEREST BUILDUP
# ==============================

def open_interest_buildup(oi_series, price_series):

    if not oi_series or len(oi_series) < 5:
        return False

    try:
        last_oi = float(oi_series[-1])
        prev_oi = sum(float(x) for x in oi_series[-5:-1]) / 4.0

        oi_growth = last_oi > prev_oi * 1.03

        last_price = float(price_series[-1])
        prev_price = float(price_series[-5])

        price_change = abs(last_price - prev_price) / prev_price * 100.0

        price_flat = price_change < 0.4

        if oi_growth and price_flat:
            return True

    except Exception:
        return False

    return False



# ==============================
# ⚡ EARLY PUMP DETECTOR
# ==============================

def pump_warning(flags, score):

    if score < 6:
        return False

    compression = ("COMP_5M" in flags or "COMP_15M" in flags)

    pressure = (
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags
    )

    volume = "VOL_SPIKE" in flags
    vacuum = "LIQUIDITY_VACUUM" in flags
    whale = "WHALE_ACC" in flags

    if compression and pressure and (volume or vacuum or whale):
        return True

    return False



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
    range_pct = 0.0
    if pmeta and isinstance(pmeta, dict):
        try:
            range_pct = float(pmeta.get("range_pct") or 0.0)
        except:
            range_pct = 0.0

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

def is_entry_signal(s):

    if s["score"] < 6:
        return False

    if "WAIT" in str(s.get("entry", "")):
        return False

    if "SAFE ENTRY" not in str(s.get("entry", "")):
        return False

    if "ACCUMULATION" in str(s.get("stage", "")):
        return False

    if abs(s.get("up_w", 0) - s.get("down_w", 0)) < 2:
        return False

    if s.get("exp_move_max", 0) < 0.8:
        return False

    # 👇 добавь это
    rsi_state = s.get("rsi_state")
    if rsi_state in ["EXTREME_OVERBOUGHT", "EXTREME_OVERSOLD"]:
        return False

    return True

def update_stats(result, move_pct, signal):

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        stats = {
            "total": 0,
            "resolved": 0,
            "hit": 0,
            "fail": 0,
            "neutral": 0,
            "sum_move": 0,
            "by_entry": {},
            "by_stage": {}
        }

    # защита для старого stats.json
    stats.setdefault("total", 0)
    stats.setdefault("resolved", 0)
    stats.setdefault("hit", 0)
    stats.setdefault("fail", 0)
    stats.setdefault("neutral", 0)
    stats.setdefault("sum_move", 0)
    stats.setdefault("by_entry", {})
    stats.setdefault("by_stage", {})

    stats["total"] += 1
    stats["sum_move"] += move_pct

    if result == "HIT":
        stats["hit"] += 1
        stats["resolved"] += 1
    elif result == "FAIL":
        stats["fail"] += 1
        stats["resolved"] += 1
    else:
        stats["neutral"] += 1

    # 📊 по ENTRY
    entry = signal.get("entry_type", signal.get("entry", "UNKNOWN"))
    stats["by_entry"].setdefault(entry, {"total": 0, "resolved": 0, "hit": 0, "fail": 0, "neutral": 0})

    stats["by_entry"][entry]["total"] += 1

    if result == "HIT":
        stats["by_entry"][entry]["hit"] += 1
        stats["by_entry"][entry]["resolved"] += 1
    elif result == "FAIL":
        stats["by_entry"][entry]["fail"] += 1
        stats["by_entry"][entry]["resolved"] += 1
    else:
        stats["by_entry"][entry]["neutral"] += 1

    # 📊 по STAGE
    stage = signal.get("stage", "UNKNOWN")
    stats["by_stage"].setdefault(stage, {"total": 0, "resolved": 0, "hit": 0, "fail": 0, "neutral": 0})

    stats["by_stage"][stage]["total"] += 1

    if result == "HIT":
        stats["by_stage"][stage]["hit"] += 1
        stats["by_stage"][stage]["resolved"] += 1
    elif result == "FAIL":
        stats["by_stage"][stage]["fail"] += 1
        stats["by_stage"][stage]["resolved"] += 1
    else:
        stats["by_stage"][stage]["neutral"] += 1

    with open("stats.json", "w") as f:
        json.dump(stats, f, indent=2)
def show_stats():

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        return "Нет данных"

    total = stats.get("total", 0)
    hit = stats.get("hit", 0)
    fail = stats.get("fail", 0)
    neutral = stats.get("neutral", max(total - hit - fail, 0))
    resolved = stats.get("resolved", hit + fail)
    avg_move = stats.get("sum_move", 0) / total if total > 0 else 0

    winrate = (hit / resolved * 100) if resolved > 0 else 0

    text = f"📊 STATS\n"
    text += f"Всего проверено: {total}\n"
    text += f"Resolved: {resolved} | Neutral: {neutral}\n"
    text += f"HIT: {hit} | FAIL: {fail}\n"
    text += f"Winrate: {round(winrate,1)}%\n"
    text += f"Avg move: {round(avg_move,2)}%\n\n"

    text += "📊 ENTRY:\n"
    for k, v in stats.get("by_entry", {}).items():
        resolved_e = v.get("resolved", v.get("hit", 0) + v.get("fail", 0))
        wr = (v.get("hit", 0) / resolved_e * 100) if resolved_e > 0 else 0
        text += f"{k}: total={v.get('total',0)} | resolved={resolved_e} | neutral={v.get('neutral',0)} | WR={round(wr,1)}%\n"

    text += "\n📊 STAGE:\n"
    for k, v in stats.get("by_stage", {}).items():
        resolved_s = v.get("resolved", v.get("hit", 0) + v.get("fail", 0))
        wr = (v.get("hit", 0) / resolved_s * 100) if resolved_s > 0 else 0
        text += f"{k}: total={v.get('total',0)} | resolved={resolved_s} | neutral={v.get('neutral',0)} | WR={round(wr,1)}%\n"

    return text

def is_profitable(signal):

    try:
        with open("stats.json", "r") as f:
            stats = json.load(f)
    except:
        return True  # если нет данных — не блокируем

    entry = signal.get("entry_type", signal.get("entry", "UNKNOWN"))
    stage = signal.get("stage", "UNKNOWN")

    # проверка ENTRY
    if entry in stats.get("by_entry", {}):
        data = stats["by_entry"][entry]
        resolved = data.get("resolved", data.get("hit", 0) + data.get("fail", 0))

        if resolved >= 5:
            wr = data.get("hit", 0) / resolved
            if wr < 0.7:
                return False

    # проверка STAGE
    if stage in stats.get("by_stage", {}):
        data = stats["by_stage"][stage]
        resolved = data.get("resolved", data.get("hit", 0) + data.get("fail", 0))

        if resolved >= 5:
            wr = data.get("hit", 0) / resolved
            if wr < 0.4:
                return False

    return True

# ==============================
# NEAR BREAKOUT DETECTOR
# ==============================

def near_breakout(pmeta, price, near_pct):

    if not pmeta or not isinstance(pmeta, dict):
        return None

    hi = pmeta.get("range_hi")
    lo = pmeta.get("range_lo")

    if hi is None or lo is None:
        return None

    # расстояние до верхней границы
    dist_hi = abs(price - hi) / hi * 100

    # расстояние до нижней границы
    dist_lo = abs(price - lo) / lo * 100

    # почти пробой вверх
    if price < hi and dist_hi <= near_pct:
        return "NEAR_BREAKOUT_UP"

    # почти пробой вниз
    if price > lo and dist_lo <= near_pct:
        return "NEAR_BREAKOUT_DOWN"

    return None

# ==============================
# 📈 CVD (Cumulative Volume Delta)
# ==============================

def cvd_detector(candles, lookback=12):

    if not candles or len(candles) < lookback + 2:
        return None

    delta = 0.0
    deltas = []

    segment = candles[-lookback:]

    for c in segment:

        try:
            o = float(c[1])
            cl = float(c[4])
            vol = float(c[5])
        except:
            continue

        if cl > o:
            d = vol
        elif cl < o:
            d = -vol
        else:
            d = 0.0

        delta += d
        deltas.append(delta)

    if len(deltas) < 4:
        return None

    first = deltas[0]
    last = deltas[-1]

    change = last - first

    if abs(first) < 1e-9:
        return None

    change_pct = change / abs(first) * 100.0

    if change_pct > 25:
        return "CVD_ACCUMULATION"

    if change_pct < -25:
        return "CVD_DISTRIBUTION"

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

# ==============================
# 🎯 LIQUIDITY MAP
# ==============================

def liquidity_map(candles, lookback=40):

    if len(candles) < lookback:
        return None

    highs = [c[2] for c in candles[-lookback:]]
    lows = [c[3] for c in candles[-lookback:]]

    hi = max(highs)
    lo = min(lows)

    last = candles[-1]
    price = last[4]

    dist_up = abs(hi - price) / price * 100
    dist_down = abs(price - lo) / price * 100

    if dist_up < 0.35:
        return "STOP_CLUSTER_UP"

    if dist_down < 0.35:
        return "STOP_CLUSTER_DOWN"

    return None


def _close_from_candle(c):
    try:
        return float(c[4])
    except Exception:
        return None


def extract_closes(candles):
    closes = []
    for c in candles or []:
        v = _close_from_candle(c)
        if v is not None:
            closes.append(v)
    return closes


def calc_rsi(closes, period=14):
    if not closes or len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(abs(min(ch, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = max(ch, 0.0)
        loss = abs(min(ch, 0.0))

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def get_rsi_state(candles):

    closes = extract_closes(candles)

    if not closes or len(closes) < max(RSI_FAST_LEN, RSI_SLOW_LEN) + 5:
        return {
            "rsi7": None,
            "rsi14": None,
            "state": "UNKNOWN",
        }

    try:
        rsi7 = calc_rsi(closes, RSI_FAST_LEN)
        rsi14 = calc_rsi(closes, RSI_SLOW_LEN)
    except Exception:
        return {
            "rsi7": None,
            "rsi14": None,
            "state": "UNKNOWN",
        }

    state = "NORMAL"

    if rsi7 is not None and rsi14 is not None:

        if rsi7 >= RSI_OB_BLOCK and rsi14 >= RSI_OB_WARN:
            state = "EXTREME_OVERBOUGHT"

        elif rsi7 <= RSI_OS_BLOCK and rsi14 <= RSI_OS_WARN:
            state = "EXTREME_OVERSOLD"

        elif rsi7 >= RSI_OB_WARN:
            state = "OVERBOUGHT"

        elif rsi7 <= RSI_OS_WARN:
            state = "OVERSOLD"

    return {
        "rsi7": rsi7,
        "rsi14": rsi14,
        "state": state,
    }


def rsi_blocks_aggressive_entry(direction, rsi_state):

    if not rsi_state:
        return False

    direction = str(direction).upper()

    state = rsi_state.get("state", "UNKNOWN")

    if direction == "UP" and state == "EXTREME_OVERBOUGHT":
        return True

    if direction == "DOWN" and state == "EXTREME_OVERSOLD":
        return True

    return False


def rsi_warns_direction(direction, rsi_state):

    if not rsi_state:
        return False

    direction = str(direction).upper()

    state = rsi_state.get("state", "UNKNOWN")

    if direction == "UP" and state in ("OVERBOUGHT", "EXTREME_OVERBOUGHT"):
        return True

    if direction == "DOWN" and state in ("OVERSOLD", "EXTREME_OVERSOLD"):
        return True

    return False

def too_late_from_range(price, pmeta, max_dist_pct=0.8):

    if not pmeta or price <= 0:
        return False

    low = pmeta.get("range_low")
    high = pmeta.get("range_high")

    if low is None or high is None:
        return False

    try:
        dist_pct = abs(price - high) / price * 100
    except Exception:
        return False

    if dist_pct > max_dist_pct:
        return True

    return False

    dist_to_hi = abs(hi - price) / price * 100.0
    dist_to_lo = abs(price - lo) / price * 100.0
    nearest = min(dist_to_hi, dist_to_lo)

    return nearest > max_dist_pct


def too_close_to_target(price, target, min_room_pct=0.35):
    if target is None or price <= 0:
        return False
    try:
        dist_pct = abs(float(target) - float(price)) / float(price) * 100.0
        return dist_pct < float(min_room_pct)
    except Exception:
        return False


def has_counter_book_or_trap(direction_text, flags):
    fs = set(flags)

    if "ВВЕРХ" in direction_text:
        if "OB_ASKS" in fs or "OB_WALL_ASK" in fs:
            return True
        if "SWEEP_UP" in fs:
            return True

    if "ВНИЗ" in direction_text:
        if "OB_BIDS" in fs or "OB_WALL_BID" in fs:
            return True
        if "FAKE_DUMP" in fs or "SWEEP_DOWN" in fs:
            return True

    return False


# =========================
# V3: ORDERBOOK EDGE (NEW)
# =========================
def orderbook_edge(instId: str):
    if not ORDERBOOK_ENABLED:
        return None

    ob = None
    try:
        ob = fetch_books(instId, ORDERBOOK_SZ)
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

    ask_wall_size = max(ask_sizes) if ask_sizes else 0
    wall_state = wall_tracker.check_wall(ask_wall_size)

    wall_removed = wall_state.get("wall_removed", False)

    bid_wall = max(bid_sizes) > bid_avg * ORDERBOOK_WALL_MULT if bid_avg > 0 else False
    ask_wall = max(ask_sizes) > ask_avg * ORDERBOOK_WALL_MULT if ask_avg > 0 else False

    if imb_abs < ORDERBOOK_IMB_MIN and not (bid_wall or ask_wall):

        return {
            "ob_bias": "NEUTRAL",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    if imb > ORDERBOOK_IMB_MIN or bid_wall:

        return {
            "ob_bias": "BIDS",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    if imb < -ORDERBOOK_IMB_MIN or ask_wall:

        return {
            "ob_bias": "ASKS",
            "imb": imb,
            "bid_wall": bid_wall,
            "ask_wall": ask_wall,
            "wall_removed": wall_removed
        }

    return {
        "ob_bias": "NEUTRAL",
        "imb": imb,
        "bid_wall": bid_wall,
        "ask_wall": ask_wall,
        "wall_removed": wall_removed
    }
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

def entry_engine(score, flags, direction_text, up_w, down_w, rsi7):

    if "БАЛАНС" in direction_text:
        return "🔴 WAIT", "Нет явного направления"

    # =========================
    # RSI SAFETY FILTER
    # =========================
    if direction_text == "⬆️ ВВЕРХ" and rsi7 is not None and rsi7 >= RSI_OB_BLOCK:
        return "🔴 WAIT", "RSI перегрет — возможен ложный пробой"

    if direction_text == "⬇️ ВНИЗ" and rsi7 is not None and rsi7 <= RSI_OS_BLOCK:
        return "🔴 WAIT", "RSI перепродан — возможен ложный пролив"

    # =========================
    # SAFE ENTRY
    # =========================
    
    confirmed = (
    "BREAKOUT_CONFIRM_UP" in flags or
    "BREAKOUT_CONFIRM_DOWN" in flags
    )
    
    early_breakout = (
        "BREAKOUT_UP" in flags or
        "BREAKOUT_DOWN" in flags
    )
        
    impulse_ok = (
        "VOL_SPIKE" in flags or
        "ATR_EXPANSION" in flags
    )
    
    flow_ok = False
    
    if direction_text == "⬆️ ВВЕРХ":
        if "PRESSURE_UP" in flags or "CONTINUATION_UP" in flags:
            flow_ok = True
    
    if direction_text == "⬇️ ВНИЗ":
        if "PRESSURE_DOWN" in flags or "CONTINUATION_DOWN" in flags:
            flow_ok = True
    
    safe_cond = (
        score >= 5.75 and
        impulse_ok and
        flow_ok and
        (up_w >= 3 or down_w >= 3) and
        (
            confirmed or
            (early_breakout and (up_w >= 4 or down_w >= 4))
        )
    )
    
    if safe_cond:
        return "🟢 SAFE ENTRY", "Подтверждение + импульс по направлению"

    # =========================
    # SAFE ENTRY
    # =========================
    safe_cond = (
        score >= EDGE_HIGH_SCORE and
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags) and
        "ATR_EXPANSION" in flags and
        "VOL_SPIKE" in flags and
        (up_w >= 3 or down_w >= 3)
    )

    if safe_cond:
        return "🟢 SAFE ENTRY", "Подтверждение + импульс"

    # =========================
    # AGGRESSIVE ENTRY
    # =========================
    if score >= EDGE_MID_SCORE and any(
        f.startswith("BREAKOUT") or f.startswith("PRESSURE")
        for f in flags
    ):
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

def liquidity_target(pmeta, flags, price=None):

    if not pmeta:
        return None

    lo = pmeta.get("range_lo")
    hi = pmeta.get("range_hi")

    if lo is None or hi is None:
        return None

    try:
        lo = float(lo)
        hi = float(hi)
        price = float(price) if price is not None else None
    except:
        return None

    rng = hi - lo

    if rng <= 0:
        return None

    if "BREAKOUT_UP" in flags or "BREAKOUT_CONFIRM_UP" in flags or "PRESSURE_UP" in flags:

        if price is not None and price >= hi:
            return round(hi + rng * 0.35, 6)

        return round(hi, 6)

    if "BREAKOUT_DOWN" in flags or "BREAKOUT_CONFIRM_DOWN" in flags or "PRESSURE_DOWN" in flags:

        if price is not None and price <= lo:
            return round(lo - rng * 0.35, 6)

        return round(lo, 6)

    return None

def calc_entry_zone(price, pmeta, flags, direction_code):
    if not pmeta:
        return None

    hi = pmeta.get("range_hi")
    lo = pmeta.get("range_lo")

    if hi is None or lo is None:
        return None

    try:
        hi = float(hi)
        lo = float(lo)
        price = float(price)
    except Exception:
        return None

    rng = hi - lo
    if rng <= 0:
        return None

    flags = set(flags)

    if direction_code == "UP":
        if "BREAKOUT_CONFIRM_UP" in flags:
            return {
                "zone_type": "RETEST_LONG",
                "low": round(hi - rng * 0.05, 6),
                "high": round(hi + rng * 0.10, 6),
                "stop": round(hi - rng * 0.20, 6),
            }

        if "SWEEP_DOWN" in flags or "FAKE_DUMP" in flags:
            return {
                "zone_type": "RECLAIM_LONG",
                "low": round(lo + rng * 0.10, 6),
                "high": round(lo + rng * 0.30, 6),
                "stop": round(lo - rng * 0.12, 6),
            }

    if direction_code == "DOWN":
        if "BREAKOUT_CONFIRM_DOWN" in flags:
            return {
                "zone_type": "RETEST_SHORT",
                "low": round(lo - rng * 0.10, 6),
                "high": round(lo + rng * 0.05, 6),
                "stop": round(lo + rng * 0.20, 6),
            }

        if "SWEEP_UP" in flags:
            return {
                "zone_type": "RECLAIM_SHORT",
                "low": round(hi - rng * 0.30, 6),
                "high": round(hi - rng * 0.10, 6),
                "stop": round(hi + rng * 0.12, 6),
            }

    return None
# =========================
# BUILD SIGNAL FOR SYMBOL
# =========================
def build_signal(instId):

    if isinstance(instId, tuple):
        instId = instId[0]

    flags = set()
    score = 0

    ob_meta = None
    pmeta = None
    tgt = None
    strong_setup = False

    # =========================
    # ORDERBOOK
    # =========================
    if ORDERBOOK_ENABLED:
        try:
            ob_meta = orderbook_edge(instId)
        except:
            ob_meta = None

    if ob_meta:
        if ob_meta.get("ob_bias") == "BIDS":
            flags.add("OB_BIDS")
            score += 1
        elif ob_meta.get("ob_bias") == "ASKS":
            flags.add("OB_ASKS")
            score += 1

        if ob_meta.get("bid_wall"):
            flags.add("OB_WALL_BID")

        if ob_meta.get("ask_wall"):
            flags.add("OB_WALL_ASK")

        if ob_meta.get("wall_removed"):
            flags.add("WALL_REMOVED")

    # =========================
    # CANDLES
    # =========================
    c5 = fetch_candles(instId, "5m", 120)
    c15 = fetch_candles(instId, "15m", 240)

    if not c5 or len(c5) < 20:
        return None
    if not c15 or len(c15) < 20:
        return None

    price = float(c5[-1][4])

    # =========================
    # PRESSURE
    # =========================
    pressure, pmeta = liquidity_pressure(c5)

    if pressure == "UP":
        flags.add("PRESSURE_UP")
        score += 1
    elif pressure == "DOWN":
        flags.add("PRESSURE_DOWN")
        score += 1

    # =========================
    # CONTINUATION
    # =========================
    try:
        cont = continuation_engine(c15)
    except:
        cont = None

    if cont:
        flags.add(cont)
        score += 2

    # =========================
    # COMPRESSION
    # =========================
    comp5, _ = compression_ok(c5)
    if comp5:
        flags.add("COMP_5M")
        score += 1

    comp15, _ = compression_ok(c15)
    if comp15:
        flags.add("COMP_15M")
        score += 1

    # =========================
    # FAKE DUMP
    # =========================
    if fake_dump_ok(c5):
        flags.add("FAKE_DUMP")
        score += 1

    # =========================
    # VOLUME SPIKE
    # =========================
    if volume_spike_ok(c5):
        flags.add("VOL_SPIKE")
        score += 1

    # =========================
    # LIQUIDITY SWEEP
    # =========================
    sweep, _meta = liquidity_sweep(c5)
    if sweep:
        flags.add(sweep)
        score += 1

    # =========================
    # ATR EXPANSION
    # =========================
    if atr_expansion_ok(c5):
        flags.add("ATR_EXPANSION")
        score += 1

    # =========================
    # BREAKOUT
    # =========================
    br = breakout_ok(c5)
    if br == "UP":
        flags.add("BREAKOUT_UP")
        score += 1
    elif br == "DOWN":
        flags.add("BREAKOUT_DOWN")
        score += 1

    br_confirm = breakout_confirm_ok(c5)
    if br_confirm == "UP":
        flags.add("BREAKOUT_CONFIRM_UP")
        score += 2
    elif br_confirm == "DOWN":
        flags.add("BREAKOUT_CONFIRM_DOWN")
        score += 2

    # =========================
    # ACCUMULATION
    # =========================
    acc_score = accumulation_bias(flags)

    # =========================
    # MARKET ANALYSIS
    # =========================
    strong_setup = score >= PRO_EDGE_MIN_SCORE

    rsi_state = get_rsi_state(c5) or {}
    rsi7 = rsi_state.get("rsi7")
    rsi14 = rsi_state.get("rsi14")

    direction_text, reasons, up_w, down_w = direction_hint(flags)
    direction_code = direction_code_from_text(direction_text)

    entry, entry_reason = entry_engine(
        score, flags, direction_text, up_w, down_w, rsi7
    )
    entry_zone = calc_entry_zone(price, pmeta, flags, direction_code)

    stage, stage_reason = smart_money_stage(score, flags)

    tgt = liquidity_target(pmeta, flags, price)

    # =========================
    # EXPECTED MOVE
    # =========================
    exp_min, exp_max = expected_move_pct(c5, pmeta)

    # =========================
    # RESULT FILTER
    # =========================
    if score < MIN_SCORE:
        return None

   # =========================
# SIGNAL OBJECT
# =========================

    tier = get_signal_tier(score, acc_score)

    signal = {
        "instId": instId,
        "symbol": instId,
        "price": price,
        "score": score,
        "tier": tier,
        "flags": list(flags),
        "pmeta": pmeta,
        "acc_score": acc_score,
        "strong_setup": strong_setup,
        "direction": direction_text,
        "direction_code": direction_code,
        "dir_reasons": reasons,
        "up_w": up_w,
        "down_w": down_w,
        "entry": entry,
        "entry_type": entry,
        "entry_price": price,
        "entry_zone": entry_zone,
        "entry_reason": entry_reason,
        "stage": stage,
        "stage_reason": stage_reason,
        "target": tgt,
        "exp_move_min": exp_min,
        "exp_move_max": exp_max,
        "rsi7": rsi7,
        "rsi14": rsi14,
        "rsi_state": rsi_state.get("state"),
        "ts": now_ts(),
        "created_at": time.time(),
    }

    signal["sniper"] = sniper_signal(signal)

    return signal


# ==============================
# 🎯 SNIPER SIGNAL ENGINE
# ==============================

def sniper_signal(sig):

    if not isinstance(sig, dict):
        return False

    flags = set(sig.get("flags") or [])
    score = int(sig.get("score", 0))

    breakout = (
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )

    impulse = (
        "VOL_SPIKE" in flags and
        "ATR_EXPANSION" in flags
    )

    liquidity = (
        "SWEEP_UP" in flags or
        "SWEEP_DOWN" in flags or
        "STOP_HUNT_UP" in flags or
        "STOP_HUNT_DOWN" in flags
    )

    orderbook = (
        "OB_BIDS" in flags or
        "OB_ASKS" in flags
    )

    whale = (
        "WHALE_FLOW" in flags or
        "WHALE_ACC" in flags
    )

    if breakout and impulse and liquidity and orderbook and whale and score >= 7:
        return True

    return False
# =========================
# SCANNER (LEVEL 1 FAST FILTER)
# =========================
def is_bad_symbol(instId: str) -> bool:
    base = instId.replace(f"-{QUOTE}", "")
    for s in EXCLUDE_TOKENS_CONTAINS:
        if s in base:
            return True
    return False
def get_market_candidates_bybit():
    tickers = get_bybit_tickers_linear()
    print("BYBIT TICKERS COUNT:", len(tickers))
    cands = []

    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        try:
            vol_usdt = float(t.get("turnover24h") or 0.0)
        except:
            vol_usdt = 0.0

        try:
            last = float(t.get("lastPrice") or 0.0)
            prev = float(t.get("prevPrice24h") or 0.0)
            pct = ((last - prev) / prev * 100.0) if prev > 0 else 0.0
        except:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        if not ACCUMULATION_MODE:
            if abs(pct) < SCAN_MIN_PCT_24H:
                continue

        instId = sym  # BYBIT symbol format, e.g. BTCUSDT
        cands.append((instId, vol_usdt, pct))

    cands.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)
    return cands[:SCAN_TOP_N]

def get_market_candidates():
    if is_bybit():
        return get_market_candidates_bybit()


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
        sig = build_signal(btc_symbol())
    except:
        return ("NEUTRAL", None)

    # защита от None
    if not isinstance(sig, dict):
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

    if not PRO_EDGE_ENABLED:
        return True

    flags = set(sig.get("flags", []))
    score = int(sig.get("score", 0))
    direction = sig.get("direction", "")
    acc = int(sig.get("acc_score", 0))

    # 🔥 фильтр реального импульса
    real_impulse = (
        "ATR_EXPANSION" in flags or
        "VOL_SPIKE" in flags or
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )

    if not real_impulse and score < EDGE_HIGH_SCORE:
        return False

    # Expected move filter (NEW)
    exp_max = float(sig.get("exp_move_max") or 0.0)
    
    if exp_max < PRE_MIN_EXPECTED_MOVE_PCT and score < EDGE_MID_SCORE:
        return False

    if score < PRO_EDGE_MIN_SCORE and score < EDGE_HIGH_SCORE:
        return False

    if PRO_EDGE_REJECT_BALANCE and ("БАЛАНС" in direction) and score < EDGE_HIGH_SCORE:
        return False

    pm = sig.get("pmeta") or {}
    range_pct = pm.get("range_pct")

    if range_pct is not None:
        if float(range_pct) < float(PRO_EDGE_MIN_RANGE_PCT) and score < EDGE_MID_SCORE:
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

    if "BULL_TRAP" in flags:
        notes.append("⚠️ BULL TRAP: пробой вверх оказался ложным — часто после этого цена идёт вниз.")

    if "BEAR_TRAP" in flags:
        notes.append("⚠️ BEAR TRAP: пробой вниз оказался ложным — часто после этого цена разворачивается вверх.")

    if "LIQUIDITY_MAGNET_UP" in flags:
        notes.append("🧲 Сверху ликвидность — цена может тянуться к стопам шортов.")

    if "LIQUIDITY_MAGNET_DOWN" in flags:
        notes.append("🧲 Снизу ликвидность — цена может тянуться к стопам лонгов.")

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

    if "ACCUMULATION" in stage:
        notes.append("🟣 STAGE=ACCUMULATION: идёт сжатие. Это зона ДО движения — ждём триггер.")

    if "MANIPULATION" in stage:
        notes.append("🟡 STAGE=MANIPULATION: вероятен сбор ликвидности перед импульсом.")

    if "EXPANSION" in stage:
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

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    direction = sig.get("direction", "⚖️ БАЛАНС")
    acc = sig.get("acc_score", 0)
    entry = sig.get("entry", "WAIT")
    stage = sig.get("stage", "UNKNOWN")
    target = sig.get("target")

    # ✅ БЕРЁМ ГОТОВЫЙ tier
    tier = sig.get("tier", "SIGNAL")

    lines.append(f"{tier} — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"📊 {score}/10 | {direction} | acc={acc}")
    lines.append(f"🎯 ENTRY: {entry}")
    lines.append(f"🧬 STAGE: {stage}")

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    return "\n".join(lines)


def msg_medium(sig):

    lines = []

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    tier = sig.get("tier", "SIGNAL")
    direction = sig.get("direction", "⚖️ БАЛАНС")
    up_w = sig.get("up_w", 0)
    down_w = sig.get("down_w", 0)
    acc = sig.get("acc_score", 0)
    entry = sig.get("entry", "WAIT")
    entry_reason = sig.get("entry_reason", "")
    stage = sig.get("stage", "UNKNOWN")
    stage_reason = sig.get("stage_reason", "")
    target = sig.get("target")
    flags = sig.get("flags", [])

    # ✅ добавили tier в начало
    lines.append(f"{tier}")
    lines.append(f"🧠 RADAR MEDIUM — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"🎯 Expected move: {sig.get('exp_move_min',0)}–{sig.get('exp_move_max',0)}%")

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(
            f"📍 RSI7={sig['rsi7']:.1f} | RSI14={sig['rsi14']:.1f} | {sig.get('rsi_state', 'UNKNOWN')}"
        )

    lines.append(f"📊 {score}/10 | {direction} (up={up_w}, down={down_w}) | acc={acc}")
    lines.append(f"🎯 ENTRY: {entry} — {entry_reason}")
    lines.append(f"🧬 STAGE: {stage} — {stage_reason}")

    pm = sig.get("pmeta") or {}
    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range: {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | {pm['range_pct']:.2f}%"
        )

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | {ez.get('low'):.6g} → {ez.get('high'):.6g} | stop {ez.get('stop'):.6g}"
        )
    
    if flags:
        lines.append("Flags:")
        for f in flags[:14]:
            lines.append(f"• {f}")

    interp = interpret_combo(sig)
    if interp:
        lines.append("")
        lines.append("🧠 Как читать ситуацию:")
        for n in interp[:12]:
            lines.append(f"• {n}")

    return "\n".join(lines)

def msg_watch(sig):

    lines = []

    inst = sig.get("instId", "?")
    price = sig.get("price", 0)
    score = sig.get("score", 0)
    direction = sig.get("direction", "⚖️ БАЛАНС")
    acc = sig.get("acc_score", 0)
    stage = sig.get("stage", "UNKNOWN")
    target = sig.get("target")
    flags = sig.get("flags", [])

    lines.append(f"🟡 WATCH — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"📊 {score}/10 | {direction} | acc={acc}")
    lines.append(f"🧬 STAGE: {stage}")
    lines.append("Смысл: это ранняя зона наблюдения, а не готовый вход.")

    pm = sig.get("pmeta") or {}
    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range: {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | {pm['range_pct']:.2f}%"
        )

    if target is not None:
        lines.append(f"🎯 Target: {target:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | {ez.get('low'):.6g} → {ez.get('high'):.6g} | stop {ez.get('stop'):.6g}"
        )

    if flags:
        lines.append("Flags:")
        for f in flags[:10]:
            lines.append(f"• {f}")

    return "\n".join(lines)


def msg_full(sig):

    lines = []

    tier = sig.get("tier", "SIGNAL")

    lines.append(f"🚨 {tier} — {fmt_symbol(sig['instId'])}")
    lines.append(f"💵 {sig['price']:.6g}")

    if sig.get("sniper"):
        lines.append("🔥 SNIPER ENTRY — сильный импульс, можно искать точку входа")

    lines.append(f"🎯 Expected move: {sig.get('exp_move_min',0)}–{sig.get('exp_move_max',0)}%")

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(f"📍 RSI7={sig['rsi7']:.1f} | RSI14={sig['rsi14']:.1f} | {sig.get('rsi_state', 'UNKNOWN')}")

    lines.append(f"📊 Score: {sig['score']}/10 | acc={sig.get('acc_score', 0)}")
    lines.append(f"🎯 Direction: {sig['direction']} (up={sig['up_w']}, down={sig['down_w']})")
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")

    pm = sig.get("pmeta") or {}
    if pm.get("range_lo") is not None and pm.get("range_hi") is not None and pm.get("range_pct") is not None:
        lines.append(f"🧲 Range(lookback): {pm['range_lo']:.6g} → {pm['range_hi']:.6g} | width≈{pm['range_pct']:.2f}%")

    if sig["target"] is not None:
        lines.append(f"🎯 Liquidity target: {sig['target']:.6g}")
    
    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | {ez.get('low'):.6g} → {ez.get('high'):.6g} | stop {ez.get('stop'):.6g}"
        )

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
        return None

    top_n = min(len(alerts), 3)
    lines.append(f"Top {top_n}:")

    for sig in alerts[:top_n]:

        sym = sig.get("instId") or sig.get("symbol") or sig.get("sym") or "?"
        score = sig.get("score", 0)
        acc = sig.get("acc_score", 0)
        direction = sig.get("direction", "")
        entry = sig.get("entry", "")
        stage = sig.get("stage", "")
        tgt = sig.get("target", None)

        if tgt is not None:
            lines.append(f"• {sym}: {score}/10 acc={acc} {direction} | {entry} | {stage} | tgt {tgt}")
        else:
            lines.append(f"• {sym}: {score}/10 acc={acc} {direction} | {entry} | {stage}")

    return "\n".join(lines)

# =========================
# PRE-MOVE MANIPULATION WATCH (V2)
# =========================
def is_pre_move_manip(sig):
    flags = set(sig.get("flags", []))
    stage = sig.get("stage", "")
    acc = int(sig.get("acc_score", 0))
    score = float(sig.get("score", 0))
    if score < 5:
        return False

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

    # ✅ если пусто — НИЧЕГО не отправляем
    if not watch:
        return None

    # показываем топ
    top_n = min(len(watch), 3)
    lines.append(f"Top {top_n}:")

    for sig in watch[:top_n]:
        sym = sig.get("instId") or sig.get("symbol") or sig.get("sym") or "?"
        acc = sig.get("acc_score", 0)
        stage = sig.get("stage", "")
        direction = sig.get("direction", "")
        score = sig.get("score", 0)

        lines.append(f"• {sym}: acc={acc} | {stage} | {direction} | score={score}/10")

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
    
def safe_entry_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_safe_entry_ts", 0) or 0)
    return (now_ts() - last) < SAFE_ENTRY_SUPPRESS_SEC


def mark_safe_entry(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_safe_entry_ts"] = now_ts()

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
    exp_max = float(sig.get("exp_move_max") or 0.0)
    if exp_max < PRE_MIN_EXPECTED_MOVE_PCT:
        return False

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

    score = sig.get("score", 0)
    strong = sig.get("strong_setup", False)
    pump = sig.get("pump_warning", False)

    # уровни сигнала
    if pump and strong and score >= 9:
        icon = "🚀🚀🚀"
        title = "ELITE PUMP"

    elif pump and strong:
        icon = "🚀🚀"
        title = "PUMP WARNING"

    elif score >= 9 and strong:
        icon = "🟢🟢🟢"
        title = "ELITE SETUP"

    elif strong:
        icon = "🟢🟢"
        title = "STRONG SETUP"

    else:
        icon = "⭐"
        title = "PRIORITY ALERT"

    lines = []
    lines.append(f"{icon} {title} — {sym}")
        # ✅ Continuation highlight (чтобы сразу видно было)
    if "CONTINUATION_UP" in sig.get("flags", []):
        lines.append("📈 M15 CONTINUATION: рост после коррекции")
    elif "CONTINUATION_DOWN" in sig.get("flags", []):
        lines.append("📉 M15 CONTINUATION: падение после коррекции")
    lines.append(f"💵 {sig['price']:.6g} | score={sig['score']}/10 | acc={sig.get('acc_score',0)}")
    lines.append(f"🧭 {sig['direction']} | {sig['entry']} | {sig['stage']}")

    if pump:
        lines.append("⚠️ Возможен ранний памп")

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
    score = float(sig.get("score", 0))
    if score < 5:
        return False
    

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
    score = float(sig.get("score", 0))

    if score < 5:
        return False

    if acc < TRIGGER_PRE_ACC:
        return False

    # Контекст накопления
    context_ok = (
        ("COMP_5M" in flags) or
        ("COMP_15M" in flags)
    )

    # Цена у границы диапазона
    near_level = (
        ("NEAR_BREAKOUT_UP" in flags) or
        ("NEAR_BREAKOUT_DOWN" in flags)
    )

    # Давление в сторону
    pressure = (
        ("PRESSURE_UP" in flags) or
        ("PRESSURE_DOWN" in flags)
    )

    # Ранний старт — ДО пробоя
    early_start = context_ok and near_level and pressure

    # Классический старт — уже с импульсом
    impulse_ok = ("ATR_EXPANSION" in flags) or ("VOL_SPIKE" in flags)
    breakout_ok = ("BREAKOUT_UP" in flags) or ("BREAKOUT_DOWN" in flags)

    classic_start = context_ok and impulse_ok and breakout_ok

    return early_start or classic_start
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

def check_signal_results():

    open_signals = get_open_signals()

    if not open_signals:
        return

    for s in open_signals:

        symbol = s["symbol"]
        entry = s["entry"]
        direction = s["direction"]
        direction_code = direction_code_from_text(direction)
        signal_id = s["id"]
        created_at = s.get("created_at")

        if not created_at:
            continue

        # ⏱ ждём минимум 5 минут
        if time.time() - created_at < 300:
            continue

        try:
            price = get_last_price(symbol)
        except:
            continue

        move_pct = (price - entry) / entry * 100

        if direction_code == "DOWN":
            move_pct = -move_pct

        if move_pct >= 1.0:
            result = "HIT"
        elif move_pct <= -1.0:
            result = "FAIL"
        else:
            result = "NEUTRAL"

        close_signal(signal_id, move_pct, result)
        update_stats(result, move_pct, s)

        try:
            with open("stats.json", "r") as f:
                stats = json.load(f)

            if stats["total"] % 5 == 0:
                send_telegram(show_stats())

        except:
            pass

        if s["id"] % 10 == 0:
            send_telegram(show_stats())

        print(f"[ANALYST] {symbol} result={result} move={round(move_pct,2)}%")

        send_telegram(
            f"📊 RESULT {symbol}\n"
            f"{result} | {round(move_pct,2)}%"
        )
# =========================
# MAIN LOOP (STABLE VERSION)
# =========================
if __name__ == "__main__":

    init_db()

    print("PROGRAM STARTED")

    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Missing BOT_TOKEN / CHAT_ID")

    print("TOKENS OK")

    state = load_state()
    print("STATE LOADED")

    # =====================
    # SCAN SETTINGS
    # =====================

    SCAN_BATCH = 20
    scan_index = 0

    try:
        send_telegram(f"🚀 SMART MONEY SCANNER — PRO EDGE v4 started ({EXCHANGE} market scan)")
    except Exception as e:
        print("START TELEGRAM ERROR:", e)

    while True:

        check_signal_results()
        t0 = time.time()

        try:

            regime, _btc = btc_regime()

            alerts = []
            manip_watch = []

            # =====================
            # SCAN MONETS
            # =====================

            all_candidates = get_market_candidates()

            if not all_candidates:
                print("NO CANDIDATES FOUND")
                time.sleep(10)
                continue

            total_symbols = len(all_candidates)

            if scan_index >= total_symbols:
                scan_index = 0

            candidates = all_candidates[scan_index:scan_index + SCAN_BATCH]
            scan_index += SCAN_BATCH

            print(f"Scanning {len(candidates)} symbols this cycle | index={scan_index}/{total_symbols}")

            # =====================
            # SCAN LOOP
            # =====================

            for instId, vol_usdt, pct in candidates:

                time.sleep(0.35)

                try:

                    sig = build_signal(instId)

                    if not isinstance(sig, dict):
                        continue

                    # =====================
                    # DEFINE SETUP TYPE
                    # =====================

                    sig["setup"] = get_signal_tier(sig["score"], sig["acc_score"])

                    # =====================
                    # AI SCORE MULTIPLIER
                    # =====================

                    setup = sig.get("setup", "UNKNOWN")
                    mult = get_ai_multiplier(setup)
                    sig["score"] = round(sig["score"] * mult, 2)

                    # =====================
                    # MARKET CONTEXT
                    # =====================

                    sig = apply_market_context(sig)

                    # =====================
                    # REGIME BIAS
                    # =====================

                    sig = apply_regime_bias(sig, regime)

                    # =====================
                    # SAVE SIGNAL
                    # =====================

                    print(
                        f"[SCAN] {instId} "
                        f"price={sig.get('price')} "
                        f"score={sig.get('score')} "
                        f"acc={sig.get('acc_score')} "
                        f"flags={sig.get('flags')}"
                    )

                    if is_entry_signal(sig):
                        save_signal(sig)

                    # =====================
                    # TIER + SEND LOGIC
                    # =====================
                    
                    score = sig.get("score", 0)
                    
                    # DEFINE TIER
                    if sig.get("sniper"):
                        sig["tier"] = "🟢🟢 СИЛЬНЫЙ ВХОД"
                    
                    elif score >= 7:
                        sig["tier"] = "🟢 СИЛЬНЫЙ СИГНАЛ"
                    
                    elif score >= 5:
                        sig["tier"] = "🟡 СИГНАЛ"
                    
                    elif score >= 4:
                        sig["tier"] = "🟠 РАННИЙ"
                    
                    else:
                        sig["tier"] = "🔴 СЛАБЫЙ"

                    safe_entry_now = (
                        "SAFE ENTRY" in str(sig.get("entry", ""))
                        and is_entry_signal(sig)
                    )

                    recent_safe_lock = safe_entry_recent(state, instId)

                    if safe_entry_now:
                        mark_safe_entry(state, instId)
                        recent_safe_lock = True

                    
                    tier = sig.get("tier")

                    entry_ok = is_entry_signal(sig)
                    profit_ok = is_profitable(sig)
                    can_alert_now = should_alert_symbol(state, sig)
                    sent_main_now = False
                    sent_pre_now = False
                    sent_start_now = False
                    
                    # SEND
                    
                    if tier == "🟢🟢 СИЛЬНЫЙ ВХОД":
                        if entry_ok and can_alert_now:
                            send_telegram(msg_full(sig))
                            sent_main_now = True
                            mark_alert_sent(state, sig)
                    
                    elif tier == "🟢 СИЛЬНЫЙ СИГНАЛ":
                        if entry_ok and can_alert_now:
                            send_telegram(msg_full(sig))
                            sent_main_now = True
                            mark_alert_sent(state, sig)
                    
                    elif tier == "🟡 СИГНАЛ":
                        if entry_ok and can_alert_now:
                            send_telegram(msg_medium(sig))
                            sent_main_now = True
                            mark_alert_sent(state, sig)
                    
                    elif tier == "🟠 РАННИЙ":
                        print(f"[EARLY] {instId} score={score}")
                    
                    # =====================
                    # ADD TO ALERTS (для summary)
                    # =====================

                    if sig.get("score", 0) >= 7:
                        print(
                            f"[CHECK] {instId} "
                            f"score={sig.get('score')} "
                            f"entry={sig.get('entry')} "
                            f"stage={sig.get('stage')} "
                            f"dir={sig.get('direction')} "
                            f"rsi={sig.get('rsi_state')} "
                            f"entry_ok={entry_ok} "
                            f"profit_ok={profit_ok} "
                            f"can_alert_now={can_alert_now} "
                            f"sent_main_now={sent_main_now}"
                        )

                    if entry_ok and profit_ok and can_alert_now and (not sent_main_now):
                        if not any(a.get("instId") == sig.get("instId") for a in alerts):
                            alerts.append(sig)

                    # =====================
                    # V3 TRIGGERS
                    # =====================

                    if (not recent_safe_lock) and is_start_trigger(sig) and trigger_allowed(state, instId, "last_start_trigger_ts", TRIGGER_START_COOLDOWN):
                        send_telegram(msg_start_trigger(sig))
                        trigger_mark(state, instId, "last_start_trigger_ts")
                        sent_start_now = True

                    elif (not recent_safe_lock) and is_pre_trigger(sig) and trigger_allowed(state, instId, "last_pre_trigger_ts", TRIGGER_PRE_COOLDOWN):
                        send_telegram(msg_pre_trigger(sig))
                        trigger_mark(state, instId, "last_pre_trigger_ts")
                        sent_pre_now = True

                    if (
                        (not sent_main_now)
                        and is_confirm_trigger(sig)
                        and entry_ok
                        and trigger_allowed(state, instId, "last_confirm_trigger_ts", TRIGGER_CONFIRM_COOLDOWN)
                    ):
                        send_telegram(msg_confirm_trigger(sig))
                        trigger_mark(state, instId, "last_confirm_trigger_ts")
              
                    
                    # =====================
                    # PRIORITY ALERT
                    # =====================
                    
                    if (not sent_main_now) and is_priority_signal(sig) and priority_allowed(state, instId):
                        if pro_edge_filter(sig, regime) and entry_ok:
                            send_telegram(msg_priority(sig))
                            mark_priority(state, instId)
                    
                    
                    # =====================
                    # PRE-MOVE WATCH
                    # =====================
                    
                    if (
                        MANIP_ALERT_ENABLED
                        and (not recent_safe_lock)
                        and (not sent_pre_now)
                        and (not sent_start_now)
                        and is_pre_move_manip(sig)
                    ):
                        if should_manip_alert(state, sig):
                            manip_watch.append(sig)
                            mark_manip_sent(state, sig)
                                        
                    update_symbol_state(state, sig) 

                except Exception as e:
                    print("SCAN ERROR:", e)

            # =====================
            # AFTER SCAN
            # =====================

            alerts.sort(key=lambda s: s.get("score", 0), reverse=True)
            manip_watch.sort(key=lambda s: s.get("acc_score", 0), reverse=True)

            cycle_info = time.strftime("%Y-%m-%d %H:%M:%S")

            print("ALERTS FOUND:", len(alerts))

            msg = summary_message(alerts, cycle_info, regime)
            if msg:
                send_telegram(msg)

            for sig in alerts[:DETAIL_TOP_K]:
                send_telegram(choose_detail_message(sig))

            if MANIP_ALERT_ENABLED:

                msg2 = manip_summary_message(manip_watch, cycle_info, regime)

                if msg2:
                    send_telegram(msg2)

                for sig in manip_watch[:MANIP_DETAIL_TOP_K]:
                    send_telegram(msg_watch(sig))

            save_state(state)

        except Exception as e:

            err = traceback.format_exc()
            send_telegram(f"❌ Scan Error:\n{err}")

        time.sleep(POLL_SECONDS)
