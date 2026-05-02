import os
import time
import json
import math
import requests
import traceback
import pandas as pd

from dotenv import load_dotenv
from priority_engine import find_global_priority, should_send_priority
from wall_detector import WallTracker
from continuation_engine import continuation_engine
from signal_tier import get_signal_tier
from sniper_engine import sniper_signal
from signal_analyst import init_db, save_signal, get_open_signals, close_signal
from ai_scoring import get_ai_multiplier
from market_context import apply_market_context
from ta_sniper import analyze_ta_sniper
from retest_filter import retest_ok


def detect_market_phase(df_h1):
    try:
        if df_h1 is None or len(df_h1) < 50:
            return {"phase": "UNKNOWN", "score": 0}

        close = df_h1["close"]

        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()

        last_price = float(close.iloc[-1])
        ema20_last = float(ema20.iloc[-1])
        ema50_last = float(ema50.iloc[-1])

        # расстояние между EMA
        spread = abs(ema20_last - ema50_last) / last_price * 100

        # наклон EMA20
        slope = ema20.iloc[-1] - ema20.iloc[-5]

        trend_score = 0

        # направление
        if ema20_last > ema50_last:
            trend_score += 1
        elif ema20_last < ema50_last:
            trend_score += 1

        # наклон
        if abs(slope) > last_price * 0.001:
            trend_score += 1

        # расширение (есть движение)
        if spread > 0.2:
            trend_score += 1

        # классификация
        if trend_score >= 3:
            phase = "TREND"
        elif trend_score == 2:
            phase = "TRANSITION"
        else:
            phase = "FLAT"

        return {
            "phase": phase,
            "score": trend_score,
            "spread": round(spread, 3)
        }

    except Exception as e:
        print(f"[PHASE_ERROR] {e}", flush=True)
        return {"phase": "UNKNOWN", "score": 0}

# =====================
# MONEY FLOW
# =====================
def money_flow_ok(candles, oi_change, direction):
    try:
        if candles is None:
            return {"ok": False}

        # если pandas → в список
        if hasattr(candles, "iloc"):
            if candles.empty:
                return {"ok": False}
            data = candles.values.tolist()
        else:
            data = candles

        if len(data) < 3:
            return {"ok": False}

        last = data[-1]
        prev = data[-2]

        close_now = float(last[4])
        close_prev = float(prev[4])

        move_pct = (close_now - close_prev) / close_prev * 100 if close_prev else 0

        vol_now = float(last[5]) if len(last) > 5 else 0
        vol_prev = float(prev[5]) if len(prev) > 5 else 1

        vol_ok = vol_now > vol_prev
        impulse_ok = abs(move_pct) > 0.3

        oi_ok = False
        if oi_change is not None:
            try:
                if float(oi_change) > 0:
                    oi_ok = True
            except:
                pass

        return {
            "ok": vol_ok and impulse_ok and oi_ok
        }

    except Exception as e:
        print(f"[MF_ERROR] {e}", flush=True)
        return {"ok": False}


# =========================
# ANTI-SPAM TELEGRAM
# =========================

LAST_SENT = {}

def can_send(symbol, sec=300):
    now = time.time()
    last = LAST_SENT.get(symbol, 0)

    if now - last < sec:
        return False

    LAST_SENT[symbol] = now
    return True

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
RESULT_CHECK_SEC = int(os.getenv("RESULT_CHECK_SEC") or "1200")

SCAN_TOP_N = int(os.getenv("SCAN_TOP_N") or "300")
SCAN_MIN_VOL_USDT = float(os.getenv("SCAN_MIN_VOL_USDT") or "800000")
SCAN_MIN_PCT_24H = float(os.getenv("SCAN_MIN_PCT_24H") or "2")
PREBREAK_SCAN_MAX_PCT_24H = float(os.getenv("PREBREAK_SCAN_MAX_PCT_24H") or "1.2")
PREBREAK_SCAN_MIN_PCT_24H = float(os.getenv("PREBREAK_SCAN_MIN_PCT_24H") or "0.2")

ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE") or "2")
ALERT_TOP_M = int(os.getenv("ALERT_TOP_M") or "8")
DETAIL_TOP_K = int(os.getenv("DETAIL_TOP_K") or "1")
MIN_SCORE = int(os.getenv("MIN_SCORE") or "2")
ONE_OPEN_SIGNAL_PER_SYMBOL = (os.getenv("ONE_OPEN_SIGNAL_PER_SYMBOL") or "1").strip() != "0"
MIN_STOP_PCT = float(os.getenv("MIN_STOP_PCT") or "0.25")
SWING_MODE = (os.getenv("SWING_MODE") or "AUTO").upper()
OI_GOOD = float(os.getenv("OI_GOOD") or "0.15")
OI_STRONG = float(os.getenv("OI_STRONG") or "0.30")
OI_BAD = float(os.getenv("OI_BAD") or "-0.10")
TIMEOUT = int(os.getenv("TIMEOUT") or "8")

# =========================
# SWING MODE (H4 / H1 / M15)
# =========================
SWING_MODE = (os.getenv("SWING_MODE") or "1").strip() != "0"


SWING_USE_H4 = (os.getenv("SWING_USE_H4") or "1").strip() != "0"
SWING_USE_H1 = (os.getenv("SWING_USE_H1") or "1").strip() != "0"
SWING_USE_M15 = (os.getenv("SWING_USE_M15") or "1").strip() != "0"

SWING_ALERT_COOLDOWN_SEC = int(os.getenv("SWING_ALERT_COOLDOWN_SEC") or "14400")
SWING_ONE_IDEA_PER_SYMBOL = (os.getenv("SWING_ONE_IDEA_PER_SYMBOL") or "1").strip() != "0"

H4_EMA_FAST = int(os.getenv("H4_EMA_FAST") or "20")
H4_EMA_SLOW = int(os.getenv("H4_EMA_SLOW") or "50")
H4_EMA_TREND = int(os.getenv("H4_EMA_TREND") or "200")

H1_EMA_FAST = int(os.getenv("H1_EMA_FAST") or "20")
H1_EMA_SLOW = int(os.getenv("H1_EMA_SLOW") or "50")

SWING_MIN_H4_SCORE = int(os.getenv("SWING_MIN_H4_SCORE") or "3")
SWING_MIN_H1_SCORE = int(os.getenv("SWING_MIN_H1_SCORE") or "3")
SWING_MIN_TRIGGER_SCORE = int(os.getenv("SWING_MIN_TRIGGER_SCORE") or "3")

mode_now = SWING_MODE

if SWING_MODE == "AUTO":
    mode_now = "AGGRESSIVE" if ALERT_MIN_SCORE <= 3 else "SAFE"

if mode_now == "AGGRESSIVE":
    SWING_MIN_ROOM_TO_TARGET_PCT = 1.4
    SWING_MAX_STOP_PCT = 7.0
    SWING_MIN_RR = 1.8
else:
    SWING_MIN_ROOM_TO_TARGET_PCT = 1.6
    SWING_MAX_STOP_PCT = 7.0
    SWING_MIN_RR = 1.8

SWING_LATE_FROM_EMA_PCT = float(os.getenv("SWING_LATE_FROM_EMA_PCT") or "8.0")
SWING_MAX_ENTRY_ZONE_PCT = float(os.getenv("SWING_MAX_ENTRY_ZONE_PCT") or "8")
SWING_REQUIRE_STOP_OUTSIDE_ZONE = (os.getenv("SWING_REQUIRE_STOP_OUTSIDE_ZONE") or "1").strip() != "0"
SWING_BUILD_MIN_SCORE = int(os.getenv("SWING_BUILD_MIN_SCORE") or "2")


# =========================
# V2 ENV
# =========================
ALERT_COOLDOWN_SEC = int(os.getenv("ALERT_COOLDOWN_SEC") or "1800")
EARLY_ALERT_COOLDOWN_SEC = int(os.getenv("EARLY_ALERT_COOLDOWN_SEC") or "2700")
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
# ==============================
# PRE-BREAKOUT SETTINGS
# ==============================
PREBREAK_LOOKBACK = int(os.getenv("PREBREAK_LOOKBACK", "15"))
PREBREAK_RECENT_BARS = int(os.getenv("PREBREAK_RECENT_BARS", "3"))
PREBREAK_VOL_MULT = float(os.getenv("PREBREAK_VOL_MULT", "1.25"))
PREBREAK_RANGE_BUILD_MULT = float(os.getenv("PREBREAK_RANGE_BUILD_MULT", "1.15"))
PREBREAK_RANGE_MAX_PCT = float(os.getenv("PREBREAK_RANGE_MAX_PCT", "1.80"))
PREBREAK_EDGE_POS = float(os.getenv("PREBREAK_EDGE_POS", "0.25"))

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
START_AFTERGLOW_SEC = int(os.getenv("START_AFTERGLOW_SEC") or "3600")
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

# =========================
# MARKET CAP FILTER
# =========================
MARKET_CAP_MIN_USD = int(os.getenv("MARKET_CAP_MIN_USD", "150000000"))
MARKET_CAP_CACHE_TTL_SEC = int(os.getenv("MARKET_CAP_CACHE_TTL_SEC", "3600"))
COINGECKO_API_KEY = (os.getenv("COINGECKO_API_KEY") or "").strip()

_market_cap_cache = {
    "ts": 0,
    "data": {},
    "last_fail_ts": 0,
}

# =========================
# SWING TF CANDLES (H4 / H1 / M15)
# =========================
def _swing_tf_to_bybit(tf: str) -> str:
    mp = {
        "15m": "15",
        "1h": "60",
        "4h": "240",
    }
    return mp.get(tf, "60")


def _swing_tf_to_okx(tf: str) -> str:
    mp = {
        "15m": "15m",
        "1h": "1H",
        "4h": "4H",
    }
    return mp.get(tf, "1H")


def _okx_swap_symbol(instId: str) -> str:
    # BTCUSDT -> BTC-USDT-SWAP
    s = str(instId).replace("-", "").upper()
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}-USDT-SWAP"
    return instId


def _df_from_ohlcv_rows(rows, source="bybit"):
    if not rows:
        return pd.DataFrame()

    try:
        if source == "bybit":
            # Bybit v5 kline:
            # [startTime, open, high, low, close, volume, turnover]
            cols = ["ts", "open", "high", "low", "close", "volume", "turnover"]
            out = pd.DataFrame(rows, columns=cols[:len(rows[0])]).copy()
            out["ts"] = pd.to_datetime(out["ts"].astype("int64"), unit="ms", utc=True)
        else:
            # OKX candles:
            # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
            cols = ["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"]
            out = pd.DataFrame(rows, columns=cols[:len(rows[0])]).copy()
            out["ts"] = pd.to_datetime(out["ts"].astype("int64"), unit="ms", utc=True)

        for c in ["open", "high", "low", "close", "volume"]:
            if c in out.columns:
                out[c] = pd.to_numeric(out[c], errors="coerce")

        out = out.sort_values("ts").reset_index(drop=True)
        return out[["ts", "open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return pd.DataFrame()


def get_tf_candles_bybit(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    try:
        interval = _swing_tf_to_bybit(tf)
        url = "https://api.bybit.com/v5/market/kline"
        params = {
            "category": "linear",
            "symbol": str(instId).upper(),
            "interval": interval,
            "limit": int(limit),
        }
        r = requests.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        rows = (((data or {}).get("result") or {}).get("list")) or []
        return _df_from_ohlcv_rows(rows, source="bybit")
    except Exception:
        return pd.DataFrame()


def get_tf_candles_okx(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    try:
        bar = _swing_tf_to_okx(tf)
        symbol = _okx_swap_symbol(instId)
        url = "https://www.okx.com/api/v5/market/candles"
        params = {
            "instId": symbol,
            "bar": bar,
            "limit": str(int(limit)),
        }
        r = requests.get(url, params=params, timeout=TIMEOUT)
        data = r.json()
        rows = (data or {}).get("data") or []
        return _df_from_ohlcv_rows(rows, source="okx")
    except Exception:
        return pd.DataFrame()


def get_tf_candles(instId: str, tf: str = "1h", limit: int = 200) -> pd.DataFrame:
    """
    Универсальный слой для swing-анализа.
    Сначала Bybit linear, если пусто — fallback на OKX swap.
    """
    df = get_tf_candles_bybit(instId, tf=tf, limit=limit)
    if not df.empty:
        return df
    return get_tf_candles_okx(instId, tf=tf, limit=limit)

# =========================
# SWING H4 ANALYSIS
# =========================
def _swing_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _swing_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if df.empty or len(df) < length + 2:
        return pd.Series(dtype="float64")

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(length).mean()


def _pct(a, b) -> float:
    try:
        a = float(a)
        b = float(b)
        if b == 0:
            return 0.0
        return (a - b) / b * 100.0
    except Exception:
        return 0.0


def analyze_h4_context(df_h4: pd.DataFrame) -> dict:
    """
    Возвращает контекст старшего ТФ:
    - bias: LONG / SHORT / NEUTRAL
    - support_zone
    - resistance_zone
    - room_to_target_pct
    - atr
    - bias_score
    """
    empty = {
        "ok": False,
        "bias": "NEUTRAL",
        "bias_score": 0,
        "support_zone": None,
        "resistance_zone": None,
        "room_to_target_pct": 0.0,
        "atr": 0.0,
        "ema20": None,
        "ema50": None,
        "ema200": None,
        "higher_low": False,
        "lower_high": False,
        "close": None,
    }

    try:
        if df_h4 is None or df_h4.empty or len(df_h4) < 60:
            return empty

        df = df_h4.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema20 = _swing_ema(close, H4_EMA_FAST)
        ema50 = _swing_ema(close, H4_EMA_SLOW)
        ema200 = _swing_ema(close, H4_EMA_TREND)
        atr_s = _swing_atr(df, 14)

        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        last_ema200 = float(ema200.iloc[-1])
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty and pd.notna(atr_s.iloc[-1]) else 0.0

        # Структура последних двух блоков
        recent8 = df.tail(8)
        prev8 = df.tail(16).head(8) if len(df) >= 16 else df.head(0)

        higher_low = False
        lower_high = False
        if not prev8.empty and not recent8.empty:
            higher_low = float(recent8["low"].min()) > float(prev8["low"].min())
            lower_high = float(recent8["high"].max()) < float(prev8["high"].max())

        # Берём рабочие зоны не по последней свече, а по предыдущему участку
        base = df.iloc[:-2] if len(df) > 10 else df.copy()
        tail_zone = base.tail(20) if len(base) >= 20 else base

        support_raw = float(tail_zone["low"].min())
        resistance_raw = float(tail_zone["high"].max())

        zone_buf = last_atr * 0.35 if last_atr > 0 else last_close * 0.005

        support_zone = (
            round(support_raw, 6),
            round(support_raw + zone_buf, 6),
        )
        resistance_zone = (
            round(max(resistance_raw - zone_buf, 0), 6),
            round(resistance_raw, 6),
        )

        room_to_target_pct = _pct(resistance_raw, last_close) if resistance_raw > last_close else 0.0

        long_score = 0
        short_score = 0

        if last_close > last_ema50:
            long_score += 1
        else:
            short_score += 1

        if last_ema20 >= last_ema50:
            long_score += 1
        else:
            short_score += 1

        if last_ema50 >= last_ema200:
            long_score += 1
        else:
            short_score += 1

        if higher_low:
            long_score += 1

        if lower_high:
            short_score += 1

        if room_to_target_pct >= SWING_MIN_ROOM_TO_TARGET_PCT:
            long_score += 1

        bias = "NEUTRAL"
        bias_score = 0

        if long_score >= SWING_MIN_H4_SCORE and long_score >= short_score + 1:
            bias = "LONG"
            bias_score = long_score
        elif short_score >= SWING_MIN_H4_SCORE and short_score >= long_score + 1:
            bias = "SHORT"
            bias_score = short_score
        else:
            bias = "NEUTRAL"
            bias_score = max(long_score, short_score)

        return {
            "ok": True,
            "bias": bias,
            "bias_score": int(bias_score),
            "support_zone": support_zone,
            "resistance_zone": resistance_zone,
            "room_to_target_pct": round(room_to_target_pct, 2),
            "atr": round(last_atr, 6),
            "ema20": round(last_ema20, 6),
            "ema50": round(last_ema50, 6),
            "ema200": round(last_ema200, 6),
            "higher_low": higher_low,
            "lower_high": lower_high,
            "close": round(last_close, 6),
        }

    except Exception as e:
        print(f"[SWING_BUILD_ERROR H4] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        return empty

# =========================
# SWING H1 SETUP ANALYSIS
# =========================
def _swing_vwap(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype="float64")

    try:
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"].fillna(0.0)
        cum_vol = vol.cumsum()
        cum_tpv = (tp * vol).cumsum()
        out = cum_tpv / cum_vol.replace(0, pd.NA)
        return out.astype("float64")
    except Exception:
        return pd.Series(dtype="float64")


def analyze_h1_setup(df_h1: pd.DataFrame, h4_ctx: dict) -> dict:
    """
    Возвращает:
    - setup_type: pullback_hold / breakout_retest / late / none
    - side: LONG / SHORT / NEUTRAL
    - entry_zone
    - invalidation_level
    - setup_score
    """
    empty = {
        "ok": False,
        "side": "NEUTRAL",
        "setup_type": "none",
        "setup_score": 0,
        "entry_zone": None,
        "invalidation_level": None,
        "late": False,
        "close": None,
        "ema20": None,
        "ema50": None,
        "vwap": None,
        "atr": 0.0,
        "reason": "no_setup",
    }

    try:
        if df_h1 is None or df_h1.empty or len(df_h1) < 40:
            return empty

        if not h4_ctx or not h4_ctx.get("ok"):
            return empty

        df = df_h1.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]

        ema20 = _swing_ema(close, H1_EMA_FAST)
        ema50 = _swing_ema(close, H1_EMA_SLOW)
        atr_s = _swing_atr(df, 14)
        vwap_s = _swing_vwap(df)

        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty and pd.notna(atr_s.iloc[-1]) else 0.0
        last_vwap = float(vwap_s.iloc[-1]) if not vwap_s.empty and pd.notna(vwap_s.iloc[-1]) else last_close

        prev20 = df.iloc[-21:-1] if len(df) >= 21 else df.iloc[:-1]
        recent6 = df.tail(6)
        recent3 = df.tail(3)

        local_hi = float(prev20["high"].max()) if not prev20.empty else float(high.iloc[-2])
        local_lo = float(prev20["low"].min()) if not prev20.empty else float(low.iloc[-2])

        h4_bias = h4_ctx.get("bias", "NEUTRAL")
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")

        # Буфер
        zone_buf = last_atr * 0.25 if last_atr > 0 else last_close * 0.004

        # Для оценки "поздно/не поздно"
        dist_from_ema20_pct = _pct(last_close, last_ema20)
        late_long = dist_from_ema20_pct > SWING_LATE_FROM_EMA_PCT
        late_short = dist_from_ema20_pct < -SWING_LATE_FROM_EMA_PCT

        # -------------------------
        # LONG setup
        # -------------------------
        if h4_bias == "LONG":
            long_score = 0
            setup_type = "none"
            entry_zone = None
            invalidation = None
            reason = "h4_long_but_no_h1_setup"

            # Pullback hold:
            # цена выше EMA50, рядом с EMA20/VWAP, структура не сломана
            pullback_hold = (
                last_close > last_ema50
                and last_close >= last_vwap * 0.995
                and float(recent6["low"].min()) >= float(low.iloc[-12:-6].min()) if len(df) >= 12 else True
            )

            # Breakout retest:
            # цена уже выше локального хая, но не улетела слишком далеко
            breakout_up = last_close > local_hi
            breakout_retest = (
                breakout_up
                and last_close > last_ema20
                and last_close > last_vwap
            )

            if last_close > last_ema50:
                long_score += 1
            if last_ema20 >= last_ema50:
                long_score += 1
            if last_close >= last_vwap:
                long_score += 1

            if pullback_hold:
                long_score += 1
                setup_type = "pullback_hold"
                base_low = float(recent6["low"].min())
                zone_low = max(min(last_ema20, last_vwap) - zone_buf, 0)
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(base_low - zone_buf, 6)
                reason = "pullback_hold"

            if breakout_retest and last_close <= local_hi * 1.03:
                long_score += 1
                setup_type = "breakout_retest"
                zone_low = max(local_hi - zone_buf, 0)
                zone_high = local_hi + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(zone_low - zone_buf, 6)
                reason = "breakout_retest"

            continuation_pullback = (
                last_close > last_ema50
                and last_ema20 >= last_ema50
                and last_close >= last_vwap * 0.997
            )

            if setup_type == "none" and continuation_pullback and long_score >= 3:
                setup_type = "pullback_hold"
                swing_low = float(recent6["low"].min())
                zone_low = max(min(last_ema20, last_vwap) - zone_buf, 0)
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(swing_low - zone_buf, 6)
                reason = "continuation_pullback"

            if late_long and setup_type == "none":
                setup_type = "late"
                reason = "late_long"

            if support_zone and setup_type == "pullback_hold":
                # дополнительно усиливаем, если зона рядом с H4 support
                sz_low, sz_high = support_zone
                if last_close >= sz_low and last_close <= sz_high * 1.03:
                    long_score += 1

            if entry_zone is not None and invalidation is not None:
                zone_low, zone_high = entry_zone
                invalidation = min(float(invalidation), float(zone_low) - zone_buf)

            return {
                "ok": True,
                "side": "LONG",
                "setup_type": setup_type if long_score >= SWING_MIN_H1_SCORE else "none",
                "setup_score": int(long_score),
                "entry_zone": entry_zone if long_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "invalidation_level": invalidation if long_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "late": bool(late_long),
                "close": round(last_close, 6),
                "ema20": round(last_ema20, 6),
                "ema50": round(last_ema50, 6),
                "vwap": round(last_vwap, 6),
                "atr": round(last_atr, 6),
                "reason": reason,
            }

        # -------------------------
        # SHORT setup
        # -------------------------
        if h4_bias == "SHORT":
            short_score = 0
            setup_type = "none"
            entry_zone = None
            invalidation = None
            reason = "h4_short_but_no_h1_setup"

            pullback_hold = (
                last_close < last_ema50
                and last_close <= last_vwap * 1.005
                and float(recent6["high"].max()) <= float(high.iloc[-12:-6].max()) if len(df) >= 12 else True
            )

            breakout_down = last_close < local_lo
            breakout_retest = (
                breakout_down
                and last_close < last_ema20
                and last_close < last_vwap
            )

            if last_close < last_ema50:
                short_score += 1
            if last_ema20 <= last_ema50:
                short_score += 1
            if last_close <= last_vwap:
                short_score += 1

            if pullback_hold:
                short_score += 1
                setup_type = "pullback_hold"
                base_high = float(recent6["high"].max())
                zone_low = min(last_ema20, last_vwap) - zone_buf
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(max(zone_low, 0), 6), round(zone_high, 6))
                invalidation = round(base_high + zone_buf, 6)
                reason = "pullback_hold"

            if breakout_retest and last_close >= local_lo * 0.97:
                short_score += 1
                setup_type = "breakout_retest"
                zone_low = max(local_lo - zone_buf, 0)
                zone_high = local_lo + zone_buf
                entry_zone = (round(zone_low, 6), round(zone_high, 6))
                invalidation = round(zone_high + zone_buf, 6)
                reason = "breakout_retest"

                continuation_pullback = (
                last_close < last_ema50
                and last_ema20 <= last_ema50
                and last_close <= last_vwap * 1.003
            )

            if setup_type == "none" and continuation_pullback and short_score >= 3:
                setup_type = "pullback_hold"
                swing_high = float(recent6["high"].max())
                zone_low = min(last_ema20, last_vwap) - zone_buf
                zone_high = max(last_ema20, last_vwap) + zone_buf
                entry_zone = (round(max(zone_low, 0), 6), round(zone_high, 6))
                invalidation = round(swing_high + zone_buf, 6)
                reason = "continuation_pullback"

            if late_short and setup_type == "none":
                setup_type = "late"
                reason = "late_short"

            if resistance_zone and setup_type == "pullback_hold":
                rz_low, rz_high = resistance_zone
                if last_close <= rz_high and last_close >= rz_low * 0.97:
                    short_score += 1

            if entry_zone is not None and invalidation is not None:
                zone_low, zone_high = entry_zone
                invalidation = max(float(invalidation), float(zone_high) + zone_buf)

            return {
                "ok": True,
                "side": "SHORT",
                "setup_type": setup_type if short_score >= SWING_MIN_H1_SCORE else "none",
                "setup_score": int(short_score),
                "entry_zone": entry_zone if short_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "invalidation_level": invalidation if short_score >= SWING_MIN_H1_SCORE and setup_type != "late" else None,
                "late": bool(late_short),
                "close": round(last_close, 6),
                "ema20": round(last_ema20, 6),
                "ema50": round(last_ema50, 6),
                "vwap": round(last_vwap, 6),
                "atr": round(last_atr, 6),
                "reason": reason,
            }

        return empty

    except Exception:
        return empty

# =========================
# SWING M15 TRIGGER
# =========================
def analyze_m15_trigger(df_m15: pd.DataFrame, h1_setup: dict, h4_ctx: dict) -> dict:

    empty = {
        "ok": False,
        "trigger_ok": False,
        "entry_now": False,
        "trigger_type": "none",
        "trigger_score": 0,
        "micro_stop": None,
        "close": None,
        "ema20": None,
        "vwap": None,
        "atr": 0.0,
        "reason": "no_trigger",
    }

    try:
        if df_m15 is None or df_m15.empty or len(df_m15) < 30:
            return empty

        if not h1_setup or not h1_setup.get("ok"):
            return empty

        if h1_setup.get("setup_type") in ("none", "late"):
            return empty

        side = h1_setup.get("side", "NEUTRAL")
        entry_zone = h1_setup.get("entry_zone")
        invalidation = h1_setup.get("invalidation_level")

        if not entry_zone or invalidation is None or side not in ("LONG", "SHORT"):
            return empty

        df = df_m15.copy().reset_index(drop=True)

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        ema20 = _swing_ema(close, 20)
        atr_s = _swing_atr(df, 14)
        vwap_s = _swing_vwap(df)

        last_close = float(close.iloc[-1])
        last_high = float(high.iloc[-1])
        last_low = float(low.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_vwap = float(vwap_s.iloc[-1]) if not vwap_s.empty else last_close
        last_atr = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0

        prev6 = df.iloc[-7:-1] if len(df) >= 7 else df.iloc[:-1]
        recent3 = df.tail(3)

        vol_avg = float(volume.iloc[-21:-1].mean()) if len(df) >= 21 else float(volume.mean())
        vol_now = float(volume.iloc[-1])
        vol_mult = (vol_now / vol_avg) if vol_avg > 0 else 0.0

        zone_low, zone_high = entry_zone
        zone_buf = last_atr * 0.20 if last_atr > 0 else last_close * 0.003

        # =====================
        # PRO МЕТРИКИ
        # =====================
        rng = max(last_high - last_low, 1e-9)
        last_open = float(df["open"].iloc[-1])

        body = abs(last_close - last_open)
        body_ratio = body / rng

        strong_candle = body_ratio >= 0.55

        micro_range = float(high.tail(5).max()) - float(low.tail(5).min())
        compression_ready = (micro_range / last_close) <= 0.012 if last_close > 0 else False

        trigger_score = 0
        trigger_type = "none"
        trigger_ok = False
        entry_now = False
        micro_stop = None
        reason = "no_trigger"

        # =====================
        # LONG
        # =====================
        if side == "LONG":
            in_zone = (last_close >= zone_low - zone_buf) and (last_close <= zone_high + zone_buf)
            above_ema = last_close >= last_ema20
            above_vwap = last_close >= last_vwap

            local_break = not prev6.empty and last_close > float(prev6["high"].max())

            retest_hold = (
                in_zone and above_ema and above_vwap
                and float(recent3["low"].min()) > float(invalidation)
            )

            if above_ema: trigger_score += 1
            if above_vwap: trigger_score += 1
            if vol_mult >= 1.10: trigger_score += 1
            if strong_candle: trigger_score += 1
            if compression_ready: trigger_score += 1

            if retest_hold:
                trigger_score += 1
                trigger_type = "retest_hold"
                reason = "m15_retest_hold_long"

            if local_break:
                trigger_score += 1

                if compression_ready and strong_candle:
                    trigger_type = "compression_break"
                    reason = "m15_compression_break_long"
                elif trigger_type == "none":
                    trigger_type = "breakout_push"
                    reason = "m15_breakout_long"

            if trigger_score >= SWING_MIN_TRIGGER_SCORE:
                trigger_ok = True
                entry_now = True
                micro_stop = round(max(invalidation, last_low - zone_buf), 6)

        # =====================
        # SHORT
        # =====================
        if side == "SHORT":
            in_zone = (last_close >= zone_low - zone_buf) and (last_close <= zone_high + zone_buf)
            below_ema = last_close <= last_ema20
            below_vwap = last_close <= last_vwap

            local_break = not prev6.empty and last_close < float(prev6["low"].min())

            retest_hold = (
                in_zone and below_ema and below_vwap
                and float(recent3["high"].max()) < float(invalidation)
            )

            if below_ema: trigger_score += 1
            if below_vwap: trigger_score += 1
            if vol_mult >= 1.10: trigger_score += 1
            if strong_candle: trigger_score += 1
            if compression_ready: trigger_score += 1

            if retest_hold:
                trigger_score += 1
                trigger_type = "retest_hold"
                reason = "m15_retest_hold_short"

            if local_break:
                trigger_score += 1
                if trigger_type == "none":
                    trigger_type = "breakout_push"
                    reason = "m15_breakout_short"

            if trigger_score >= SWING_MIN_TRIGGER_SCORE:
                trigger_ok = True
                entry_now = True
                micro_stop = round(min(invalidation, last_high + zone_buf), 6)

        # =====================
        # FALLBACK
        # =====================
        if trigger_score >= SWING_MIN_TRIGGER_SCORE and trigger_type == "none":
            trigger_type = "momentum_ready"
            trigger_ok = True
            entry_now = True

        return {
            "ok": True,
            "trigger_ok": trigger_ok,
            "entry_now": entry_now,
            "trigger_type": trigger_type,
            "trigger_score": trigger_score,
            "micro_stop": micro_stop,
            "close": round(last_close, 6),
            "ema20": round(last_ema20, 6),
            "vwap": round(last_vwap, 6),
            "atr": round(last_atr, 6),
            "reason": reason,
        }

    except Exception:
        return empty


            
# =========================
# SWING SIGNAL BUILDER + TELEGRAM FORMAT
# =========================
def _swing_mid(zone):
    try:
        if not zone:
            return None
        a, b = zone
        return (float(a) + float(b)) / 2.0
    except Exception:
        return None


def _swing_rr(entry: float, stop: float, target: float, side: str) -> float:
    try:
        entry = float(entry)
        stop = float(stop)
        target = float(target)

        if side == "LONG":
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target

        if risk <= 0:
            return 0.0
        return reward / risk
    except Exception:
        return 0.0


def _fmt_px(x):
    try:
        x = float(x)
        if x >= 1000:
            return f"{x:.2f}"
        if x >= 100:
            return f"{x:.3f}"
        if x >= 1:
            return f"{x:.4f}"
        return f"{x:.6f}"
    except Exception:
        return str(x)


def build_swing_signal(instId: str, h4_ctx: dict, h1_setup: dict, m15_trigger: dict, sig: dict = None) -> dict:
    empty = {
        "ok": False,
        "symbol": instId,
        "status": "NONE",
        "side": "NEUTRAL",
        "entry_zone": None,
        "entry_price": None,
        "stop": None,
        "tp1": None,
        "tp2": None,
        "rr1": 0.0,
        "late": False,
        "sendable": False,
        "verdict": "no_signal",
        "oi_change": None,
        "flags": [],
        "price": None,
        "instId": instId,
    }

    try:
        h4_ctx = h4_ctx or {}
        h1_setup = h1_setup or {}
        m15_trigger = m15_trigger or {}
        sig = sig or {}

        if not h4_ctx or not h4_ctx.get("ok"):
            return empty

        # =====================
        # SIDE
        # =====================
        side = h1_setup.get("side", "NEUTRAL")
        if side not in ("LONG", "SHORT"):
            side = "LONG" if h4_ctx.get("bias") == "LONG" else (
                "SHORT" if h4_ctx.get("bias") == "SHORT" else "NEUTRAL"
            )

        if side == "NEUTRAL":
            return empty

        # =====================
        # M15 CHECK
        # =====================
        m15_ready = bool(
            m15_trigger and (
                m15_trigger.get("trigger_ok") is True
                or m15_trigger.get("ok") is True
                or str(m15_trigger.get("trigger_type", "none")) not in ("none", "", "None")
            )
        )

        print(f"[M15_READY] {instId} ready={m15_ready} raw={m15_trigger}")

        if not m15_ready:
            return empty

        def detect_market_mode(m15_trigger: dict) -> str:
            atr = float(m15_trigger.get("atr") or 0)
            price = float(m15_trigger.get("close") or 0)
            ema20 = float(m15_trigger.get("ema20") or 0)
            vwap = float(m15_trigger.get("vwap") or 0)
        
            if price <= 0:
                return "НЕИЗВЕСТНО"
        
            # нормализуем
            atr_pct = atr / price * 100
            ema_dist = abs(ema20 - vwap) / price * 100
        
            # =====================
            # ЛОГИКА
            # =====================
        
            # 💤 ФЛЕТ
            if atr_pct < 0.15 and ema_dist < 0.1:
                return "ФЛЕТ"
        
            # 💣 ХАОС
            if atr_pct > 0.8:
                return "ХАОС"
        
            # 🚀 ТРЕНД
            if ema_dist > 0.2:
                return "ТРЕНД"
        
            return "НЕЯСНО"

        # =====================
        # RETEST
        # =====================
        rt = retest_ok(sig, m15_trigger)

        # =====================
        # RETEST OVERRIDE (важно)
        # =====================
        if not rt.get("ok"):
        
            flags = set(sig.get("flags", []))
            direction = sig.get("direction", "")
        
            strong_momentum = (
                "BREAKOUT_UP" in flags or
                "CONTINUATION_UP" in flags or
                "BREAKOUT_DOWN" in flags or
                "CONTINUATION_DOWN" in flags
            )
        
            if strong_momentum:
                print(f"[RETEST_OVERRIDE] {instId} strong momentum → allow", flush=True)
        
                entry = sig.get("price")
        
                if "ВВЕРХ" in direction:
                    stop = entry * 0.985
                elif "ВНИЗ" in direction:
                    stop = entry * 1.015
                else:
                    return empty
        
            else:
                print(f"[RETEST_SKIP] {instId} {rt.get('reason')}", flush=True)
                return empty
        
        else:
            print(f"[RETEST_OK] {instId}", flush=True)
        
            entry = rt["entry"]
            stop = rt["stop"]


        # =====================
        # RR (ОБЩИЙ ДЛЯ ВСЕХ)
        # =====================
        tp1 = sig.get("tp1")

        if tp1 is None or entry is None or stop is None:
            rr = 0
        else:
            rr = abs(tp1 - entry) / max(abs(entry - stop), 1e-9)
        
        # 🔥 ВАЖНО — СЮДА
        if rr == 0 and entry and stop:
            rr = 2.0
            print(f"[RR_FIX] {instId} fallback rr=2.0", flush=True)
        
        print(f"[RR] {instId} rr={round(rr,2)}", flush=True)


        # =====================
        # RR FILTER
        # =====================
        score = sig.get("score", 0)
        
        if rr < 1 and score < 6:
            print(f"[RR_SKIP] {instId} rr={round(rr,2)} score={score}", flush=True)
            return empty
        
        
        # =====================
        # RSI FILTER (СЮДА)
        # =====================
        rsi = sig.get("rsi") or sig.get("rsi14")
        
        try:
            rsi = float(rsi)
        except:
            rsi = None
        
        if rsi is not None:
            if sig.get("side") in ("LONG", "BUY") and rsi > 80:
                print(f"[RSI_SKIP] {instId} перегрев LONG rsi={rsi}", flush=True)
                return empty
        
            if sig.get("side") in ("SHORT", "SELL") and rsi < 20:
                print(f"[RSI_SKIP] {instId} перепроданность SHORT rsi={rsi}", flush=True)
                return empty
        
        
        # =====================
        # OI INTELLIGENCE
        # =====================
        oi = sig.get("oi_change")
        
        try:
            oi = float(oi)
        except:
            oi = None
        
        score = sig.get("score", 0)
        
        oi_confirm = False
        oi_weak = False
        
        if oi is not None:
        
            # 🔥 деньги заходят
            if oi > 0.05:
                oi_confirm = True
                print(f"[OI_CONFIRM] {instId} oi={oi}", flush=True)
        
            # ⚠️ деньги выходят
            elif oi < -0.05:
                oi_weak = True
                print(f"[OI_WEAK] {instId} oi={oi}", flush=True)


        # =====================
        # OI FILTER
        # =====================
        if oi_weak and score < 6:
            print(f"[OI_SKIP] {instId} weak OI + low score", flush=True)
            return empty
        
        
        # =====================
        # OI BOOST
        # =====================
        if oi_confirm:
            sig["score"] = sig.get("score", 0) + 1
            print(f"[OI_BOOST] {instId} +1 score", flush=True)

        # =====================
        # SCORE FILTER
        # =====================
        score = float(sig.get("score") or 0)
        if score < 5:
            print(f"[SCORE_SKIP] {instId} score={score}", flush=True)
            return empty

        
        # =====================
        # RSI DIVERGENCE FILTER
        # =====================
        rsi = sig.get("rsi") or sig.get("rsi14")
        
        try:
            rsi = float(rsi)
        except:
            rsi = None
        
        prev_price = sig.get("prev_price")
        prev_rsi = sig.get("prev_rsi")
        
        try:
            prev_price = float(prev_price)
            prev_rsi = float(prev_rsi)
        except:
            prev_price = None
            prev_rsi = None
        
        price_now = float(m15_trigger.get("close") or 0)

        # =====================
        # SAVE PREVIOUS VALUES
        # =====================
        if "prev_price" not in sig:
            sig["prev_price"] = price_now
        
        if "prev_rsi" not in sig:
            sig["prev_rsi"] = rsi

        # =====================
        # LOAD PREVIOUS VALUES
        # =====================
        prev_price = sig.get("prev_price")
        prev_rsi = sig.get("prev_rsi")
        
        try:
            prev_price = float(prev_price)
            prev_rsi = float(prev_rsi)
        except:
            prev_price = None
            prev_rsi = None
        
        # LONG дивергенция (плохо для лонга)
        if sig.get("side") in ("LONG", "BUY"):
            if prev_price and prev_rsi and rsi:
                if price_now > prev_price and rsi < prev_rsi:
                    print(f"[DIV_SKIP] {instId} bearish divergence", flush=True)
                    return empty
        
    
        # SHORT дивергенция (плохо для шорта)
        if sig.get("side") in ("SHORT", "SELL"):
            if prev_price and prev_rsi and rsi:
                if price_now < prev_price and rsi > prev_rsi:
                    print(f"[DIV_SKIP] {instId} bullish divergence", flush=True)
                    return empty
        
        
        # =====================
        # 👉 ВСТАВИТЬ СЮДА (DOUBLE DIVERGENCE)
        # =====================
        oi = sig.get("oi_change")
        
        try:
            oi = float(oi)
        except:
            oi = None
        
        # LONG — ослабление
        if side == "LONG":
            if prev_price and prev_rsi and rsi and oi is not None:
                if price_now > prev_price and rsi < prev_rsi and oi < 0:
                    print(f"[DOUBLE_DIV_SKIP] {instId} LONG weak", flush=True)
                    return empty
        
        # SHORT — ослабление
        if side == "SHORT":
            if prev_price and prev_rsi and rsi and oi is not None:
                if price_now < prev_price and rsi > prev_rsi and oi < 0:
                    print(f"[DOUBLE_DIV_SKIP] {instId} SHORT weak", flush=True)
                    return empty


        # =====================
        # H4 FILTER
        # =====================
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")

        if side == "LONG" and resistance_zone:
            resistance = float(resistance_zone[1])
            dist = abs(resistance - entry) / entry * 100
            if dist < 0.8:
                print(f"[H4_SKIP] {instId} near resistance {round(dist,2)}%", flush=True)
                return empty

        if side == "SHORT" and support_zone:
            support = float(support_zone[0])
            dist = abs(entry - support) / entry * 100
            if dist < 0.8:
                print(f"[H4_SKIP] {instId} near support {round(dist,2)}%", flush=True)
                return empty

        

        # =====================
        # FALLBACK
        # =====================
        if m15_trigger.get("close") is not None:
            entry = float(m15_trigger.get("close"))

        if m15_trigger.get("micro_stop") is not None:
            stop = m15_trigger.get("micro_stop")

        # =====================
        # FLAT FILTER (БОКОВИК)
        # =====================
        
        ema20 = float(m15_trigger.get("ema20") or 0)
        vwap = float(m15_trigger.get("vwap") or 0)
        atr = float(m15_trigger.get("atr") or 0)
        price = float(m15_trigger.get("close") or 0)
        
        # диапазон через ATR
        range_pct = (atr / price * 100) if price > 0 else 0
        
        # расстояние между EMA и VWAP
        ema_dist = abs(ema20 - vwap) / price * 100 if price > 0 else 0
        
        # условия флета
        is_flat = (
            range_pct < 0.2   # слабое движение
            and ema_dist < 0.1  # нет тренда
        )
        
        if is_flat:
            print(f"[FLAT_SKIP] {instId} range={round(range_pct,3)} ema_dist={round(ema_dist,3)}", flush=True)
            return empty
        # =====================
        # CONTINUATION FILTER
        # =====================
        
        close = float(m15_trigger.get("close") or 0)
        ema20 = float(m15_trigger.get("ema20") or 0)
        vwap = float(m15_trigger.get("vwap") or 0)
        
        # сила продолжения
        if sig.get("side") in ("LONG", "BUY"):
        
            # цена должна быть выше EMA и VWAP
            if close < ema20 or close < vwap:
                print(f"[CONT_SKIP] {instId} weak long continuation", flush=True)
                return empty 
        
        elif sig.get("side") in ("SHORT", "SELL"):
        
            # цена должна быть ниже EMA и VWAP
            if close > ema20 or close > vwap:
                print(f"[CONT_SKIP] {instId} weak short continuation", flush=True)
                return empty

        # =====================
        # FAKE BREAKOUT (ANTI-TRAP)
        # =====================
        
        support_zone = h4_ctx.get("support_zone")
        resistance_zone = h4_ctx.get("resistance_zone")
        
        close = float(m15_trigger.get("close") or 0)
        atr = float(m15_trigger.get("atr") or 0)
        
        buffer = atr * 0.5
        
        if sig.get("side") in ("LONG", "BUY") and resistance_zone:
            resistance = float(resistance_zone[1])
        
            if abs(resistance - close) < buffer:
                print(f"[TRAP_SKIP] {instId} near resistance trap zone", flush=True)
                return empty
        
            if close < resistance - buffer:
                print(f"[TRAP_SKIP] {instId} weak breakout", flush=True)
                return empty
        
        
        elif sig.get("side") in ("SHORT", "SELL") and support_zone:
            support = float(support_zone[0])
        
            if abs(close - support) < buffer:
                print(f"[TRAP_SKIP] {instId} near support trap zone", flush=True)
                return empty
        
            if close > support + buffer:
                print(f"[TRAP_SKIP] {instId} weak breakdown", flush=True)
                return empty

        # =====================
        # IMPULSE 2.0 (STRONG MOVE FILTER)
        # =====================
        atr = float(m15_trigger.get("atr") or 0)
        price = float(m15_trigger.get("close") or 0)
        ema20 = float(m15_trigger.get("ema20") or 0)
        
        # защита
        if price <= 0 or atr <= 0:
            print(f"[IMPULSE_SKIP] {instId} no data", flush=True)
            return empty
        
        # сила движения
        atr_pct = atr / price * 100
        
        # расстояние от EMA (перегрев)
        ema_dist = abs(price - ema20) / price * 100
        
        # объём
        vol = float(sig.get("volume") or sig.get("vol") or 0)
        avg_vol = float(sig.get("avg_volume") or sig.get("vol_avg") or 0)
        
        vol_ok = True
        if avg_vol > 0:
            vol_ok = vol > avg_vol * 1.3
        
        # =====================
        # УСЛОВИЯ
        # =====================
        
        # ❌ слабое движение
        if atr_pct < 0.2:
            print(f"[IMPULSE_SKIP] {instId} weak move {round(atr_pct,3)}%", flush=True)
            return empty
        
        # ❌ нет объёма
        if not vol_ok:
            print(f"[IMPULSE_SKIP] {instId} weak volume", flush=True)
            return empty
        
        # ❌ перегрев (вход в конец движения)
        if ema_dist > 1.2:
            print(f"[IMPULSE_SKIP] {instId} overextended {round(ema_dist,2)}%", flush=True)
            return empty

        
        # =====================
        # SNIPER PRO FILTER
        # =====================
        
        # базовая проверка
        if entry is None or stop is None or sig.get("tp1") is None:
            print(f"[PRO_SKIP] {instId} empty trade", flush=True)
            return empty
        
        tp1 = sig.get("tp1")
        
        # RR
        if entry and stop and tp1:
            rr = abs(tp1 - entry) / max(abs(entry - stop), 1e-9)
        else:
            rr = 0
        
        # fallback
        if rr == 0 and entry and stop:
            rr = 2.0
            print(f"[RR_FIX] {instId}", flush=True)
        
        print(f"[RR] {instId} rr={round(rr,2)}", flush=True)
        
        score = sig.get("score", 0)
        
        # RR фильтр
        if rr < 1 and score < 6:
            print(f"[PRO_SKIP] weak RR", flush=True)
            return empty
        
        
        # RSI
        rsi = sig.get("rsi") or sig.get("rsi14")
        
        try:
            rsi = float(rsi)
        except:
            rsi = None
        
        if rsi is not None:
            if side in ("LONG", "BUY") and rsi > 80:
                print(f"[PRO_SKIP] RSI high", flush=True)
                return empty
        
            if side in ("SHORT", "SELL") and rsi < 20:
                print(f"[PRO_SKIP] RSI low", flush=True)
                return empty

        # =====================
        # MARKET FILTER
        # =====================
        df_h1_phase = get_tf_candles(instId, tf="1h", limit=100)
        market_phase = detect_market_phase(df_h1_phase)
        
        phase = market_phase.get("phase")
        score = sig.get("score", 0)
        
        if phase == "FLAT":
            print(f"[FLAT_SKIP] {instId}", flush=True)
            return empty
        
        if phase == "TRANSITION" and score < 6:
            print(f"[TRANSITION_SKIP] {instId}", flush=True)
            return empty
        
        if phase == "TREND":
            sig["score"] = sig.get("score", 0) + 1
            print(f"[TREND_BOOST] {instId}", flush=True)
        
        # 🔥 ВОТ ЭТО ДОБАВЬ
        score = sig.get("score", 0)

        # =====================
        # SIGNAL CLASSIFICATION (УРОВЕНЬ)
        # =====================
        
        level = "C"
        
        # временно без money flow
        mf_ok = False  
        
        if rr >= 2 and score >= 6:
            if mf_ok and oi_confirm:
                level = "A"
            else:
                level = "B"
        
        elif rr >= 1.2 and score >= 4:
            level = "B"
        
        print(f"[LEVEL] {instId} level={level}", flush=True)
        
        
        # =====================
        # FILTER WEAK (C)
        # =====================
        if level == "C":
            print(f"[SKIP] {instId} weak signal (C)", flush=True)
            return empty
        
        
        # =====================
        # SEND TELEGRAM
        # =====================
        send_telegram(
            f"🎯 <b>RETEST ENTRY — {instId}</b>\n\n"
            f"🧭 Side: <b>{side}</b>\n"
            f"💵 Entry: <b>{entry}</b>\n"
            f"🛑 Stop: <b>{stop}</b>\n"
            f"🎯 TP1: <b>{tp1}</b>\n"
            f"📊 RR: <b>{round(rr,2)}</b>\n\n"
            f"📌 Причина: {rt.get('reason')}"
        )
        
        # =====================
        # UPDATE PREVIOUS VALUES (ПОСЛЕ ОТПРАВКИ)
        # =====================
        sig["prev_price"] = price_now
        sig["prev_rsi"] = rsi
        
        return {
            "ok": True,
            "symbol": instId,
            "side": side,
            "entry_price": entry,
            "stop": stop,
            "tp1": sig.get("tp1"),
            "rr1": rr,
            "sendable": True
        }

    except Exception as e:
        print(f"[SWING_ERROR] {instId} {e}", flush=True)
        return empty

        
def format_swing_telegram(sig: dict) -> str:
    if not sig or not sig.get("ok"):
        return ""

    icon = "🧭"
    if sig.get("status") == "SWING TRIGGER":
        icon = "🚀"
    elif sig.get("status") == "SWING SETUP":
        icon = "📍"

    side_txt = "LONG" if sig.get("side") == "LONG" else "SHORT"

    support_zone = sig.get("h4_support_zone")
    resistance_zone = sig.get("h4_resistance_zone")
    entry_zone = sig.get("entry_zone")

    support_txt = "-"
    if support_zone:
        support_txt = f"{_fmt_px(support_zone[0])} → {_fmt_px(support_zone[1])}"

    resistance_txt = "-"
    if resistance_zone:
        resistance_txt = f"{_fmt_px(resistance_zone[0])} → {_fmt_px(resistance_zone[1])}"

    entry_txt = "-"
    if entry_zone:
        entry_txt = f"{_fmt_px(entry_zone[0])} → {_fmt_px(entry_zone[1])}"

    stop_txt = _fmt_px(sig["stop"]) if sig.get("stop") is not None else "-"
    tp1_txt = _fmt_px(sig["tp1"]) if sig.get("tp1") is not None else "-"
    tp2_txt = _fmt_px(sig["tp2"]) if sig.get("tp2") is not None else "-"

    return (
        f"{icon} <b>{sig.get('status')}</b> — {sig.get('symbol')}\n\n"
        f"Направление: <b>{side_txt}</b>\n"
        f"H4 Bias: <b>{sig.get('h4_bias')}</b> | score={sig.get('h4_bias_score')}\n"
        f"H1 Setup: <b>{sig.get('h1_setup_type')}</b> | score={sig.get('h1_setup_score')}\n"
        f"M15 Trigger: <b>{sig.get('m15_trigger_type')}</b> | score={sig.get('m15_trigger_score')}\n\n"
        f"H4 support: {support_txt}\n"
        f"H4 resistance: {resistance_txt}\n"
        f"Room to target: {sig.get('h4_room_to_target_pct')}%\n\n"
        f"Entry zone: {entry_txt}\n"
        f"Stop: {stop_txt}\n"
        f"TP1: {tp1_txt}\n"
        f"TP2: {tp2_txt}\n"
        f"RR1: {sig.get('rr1')}\n\n"
        f"🧠 <b>Вердикт</b>:\n{sig.get('verdict')}"
    )

def _chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def get_base_coin(symbol: str) -> str:
    symbol = str(symbol).upper().strip()

    if symbol.endswith("USDT"):
        return symbol[:-4]

    if symbol.endswith("-USDT"):
        return symbol[:-5]

    return symbol

def _get_coingecko_headers():
    headers = {"accept": "application/json"}

    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    return headers


def fetch_market_caps_usd(base_coins):
    """
    Возвращает словарь:
    {
        "BTC": 1800000000000,
        "ETH": 420000000000,
    }
    """

    base_coins = sorted({str(x).upper().strip() for x in base_coins if x})
    if not base_coins:
        return {}
    
    # 🔥 быстрый фильтр — берем только новые монеты (экономим API)
    if _market_cap_cache["data"]:
        missing = [c for c in base_coins if c not in _market_cap_cache["data"]]
    else:
        missing = base_coins.copy()
    
    # если всё уже есть в кеше — сразу отдаём
    # если всё есть в кеше И кеш свежий — используем его
    if not missing and (now - _market_cap_cache["ts"] < MARKET_CAP_CACHE_TTL_SEC):
        return _market_cap_cache["data"]
    now = time.time()

    market_cap_fail_cooldown_sec = int(os.getenv("MARKET_CAP_FAIL_COOLDOWN_SEC", "1800"))

    last_fail_ts = float(_market_cap_cache.get("last_fail_ts", 0) or 0)
    if last_fail_ts and (now - last_fail_ts < market_cap_fail_cooldown_sec):
        print("[MARKET_CAP] cooldown → cache")
        return _market_cap_cache["data"]

    

    # cache hit
    if _market_cap_cache["data"] and (now - _market_cap_cache["ts"] < MARKET_CAP_CACHE_TTL_SEC):
        cached = _market_cap_cache["data"]
        if all(c in cached for c in base_coins):
            return cached

    fresh = {}
    chunk_size = 20
    request_sleep_sec = float(os.getenv("MARKET_CAP_REQUEST_SLEEP_SEC", "3.0"))
    retry_sleep_sec = float(os.getenv("MARKET_CAP_RETRY_SLEEP_SEC", "8.0"))
    max_retries = int(os.getenv("MARKET_CAP_MAX_RETRIES", "2"))

    for chunk in _chunked(missing, chunk_size):
        success = False

        for attempt in range(max_retries):
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "symbols": ",".join(x.lower() for x in chunk),
                        "include_tokens": "top",
                        "order": "market_cap_desc",
                        "per_page": 250,
                        "page": 1,
                    },
                    headers=_get_coingecko_headers(),
                    timeout=20,
                )

                if r.status_code == 429:
                    print(f"[MARKET_CAP] 429 retry {attempt+1}")
                    time.sleep(retry_sleep_sec * (attempt + 1))
                    continue

                r.raise_for_status()
                rows = r.json()

                for row in rows:
                    sym = str(row.get("symbol", "")).upper().strip()
                    mcap = row.get("market_cap")

                    if sym and mcap is not None:
                        try:
                            fresh[sym] = float(mcap)
                        except:
                            pass

                success = True
                break

            except Exception as e:
                print(f"[MARKET_CAP ERROR] {e}")
                time.sleep(retry_sleep_sec * (attempt + 1))

        if not success:
            print(f"[MARKET_CAP FAIL] {chunk}")

        time.sleep(request_sleep_sec)

    # обновляем кэш
    if fresh:
        merged = dict(_market_cap_cache["data"])
        merged.update(fresh)
        _market_cap_cache["data"] = merged
        _market_cap_cache["ts"] = now
        _market_cap_cache["last_fail_ts"] = 0
    else:
        _market_cap_cache["last_fail_ts"] = now

    # ✅ ВАЖНО — внутри функции
    return _market_cap_cache["data"]


# =========================
# MARKET CAP FILTER
# =========================
def is_market_cap_ok(symbol: str, market_caps: dict) -> bool:
    base = get_base_coin(symbol)
    mcap = market_caps.get(base)

    if mcap is None:
        return False

    return mcap >= MARKET_CAP_MIN_USD


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

def is_empty(c):
    return (
        c is None or
        (hasattr(c, "empty") and c.empty) or
        (isinstance(c, list) and len(c) == 0)
    )

def get_last_price(symbol: str) -> float:
    candles = fetch_candles(symbol, "5m", 2)
    if not candles:
        raise RuntimeError(f"Нет свечей для {symbol}")
    return float(candles[-1][4])

def get_open_interest(symbol):
    try:
        url = "https://api.bybit.com/v5/market/open-interest"
        params = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min"
        }

        r = requests.get(url, params=params, timeout=10)
        data = r.json()

        rows = (((data or {}).get("result") or {}).get("list") or [])
        if len(rows) < 2:
            return None

        oi_now = float(rows[0]["openInterest"])
        oi_prev = float(rows[1]["openInterest"])

        if oi_prev <= 0:
            return None

        oi_change_pct = (oi_now - oi_prev) / oi_prev * 100.0
        return round(oi_change_pct, 2)

    except:
        return None

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

# =========================
# OPEN SIGNAL DUPLICATE CHECK
# =========================
def has_open_similar_signal(sig):
    try:
        open_signals = get_open_signals()
    except Exception:
        return False

    symbol = sig.get("symbol") or sig.get("instId")
    direction_code = sig.get("direction_code") or direction_code_from_text(sig.get("direction", ""))
    entry_type = sig.get("entry_type", sig.get("entry", "UNKNOWN"))

    for s in open_signals:
        s_symbol = s.get("symbol")
        s_direction = s.get("direction_code") or direction_code_from_text(s.get("direction", ""))
        s_entry = s.get("entry_type", s.get("entry", "UNKNOWN"))

        if s_symbol == symbol and s_direction == direction_code and s_entry == entry_type:
            return True

    return False

def has_any_open_signal_for_symbol(symbol: str) -> bool:
    try:
        open_signals = get_open_signals()
    except Exception:
        return False

    for s in open_signals:
        s_symbol = s.get("symbol") or s.get("instId")
        if s_symbol == symbol:
            return True

    return False

# =========================
# PRE-BREAKOUT BUILD-UP
# =========================
def volume_build_inside_range(
    candles,
    lookback=PREBREAK_LOOKBACK,
    recent=PREBREAK_RECENT_BARS,
    vol_mult=PREBREAK_VOL_MULT,
    range_mult=PREBREAK_RANGE_BUILD_MULT,
):
    if not candles or len(candles) < lookback + recent + 2:
        return False

    segment = candles[-(lookback + recent):-recent]
    recent_segment = candles[-recent:]

    try:
        prev_vols = [float(c[5]) for c in segment]
        last_vols = [float(c[5]) for c in recent_segment]

        prev_ranges = [float(c[2]) - float(c[3]) for c in segment]
        last_ranges = [float(c[2]) - float(c[3]) for c in recent_segment]
    except Exception:
        return False

    if not prev_vols or not last_vols or not prev_ranges or not last_ranges:
        return False

    avg_prev_vol = sum(prev_vols) / len(prev_vols)
    avg_last_vol = sum(last_vols) / len(last_vols)

    avg_prev_range = sum(prev_ranges) / len(prev_ranges)
    avg_last_range = sum(last_ranges) / len(last_ranges)

    if avg_prev_vol <= 0 or avg_prev_range <= 0:
        return False

    vol_build = avg_last_vol >= avg_prev_vol * vol_mult
    range_not_expanded = avg_last_range <= avg_prev_range * range_mult

    return vol_build and range_not_expanded


# =========================
# PRE-BREAKOUT PRESSURE FLAG
# =========================
def detect_pre_breakout_pressure(candles, flags, pmeta, ema_state):
    flags = set(flags)

    if not candles or not pmeta:
        return None

    pos = pmeta.get("pos")
    range_pct = pmeta.get("range_pct")

    if pos is None or range_pct is None:
        return None

    try:
        pos = float(pos)
        range_pct = float(range_pct)
    except Exception:
        return None

    # диапазон уже слишком широкий — это уже не тот флет
    if range_pct > PREBREAK_RANGE_MAX_PCT:
        return None

    # если уже есть подтверждённый пробой/расширение — это поздно
    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return None

    if "ATR_EXPANSION" in flags:
        return None

    build_ok = volume_build_inside_range(candles)

    if not build_ok:
        return None

    comp_ok = ("COMP_5M" in flags) or ("COMP_15M" in flags)

    # PRE-BREAKOUT SELL
    if (
        pos <= PREBREAK_EDGE_POS
        and comp_ok
        and "PRESSURE_DOWN" in flags
        and "CONTINUATION_DOWN" in flags
        and ema_state == "EMA_BEAR"
        and "SWEEP_DOWN" not in flags
        and "FAKE_DUMP" not in flags
    ):
        return "PRE_BREAKOUT_SELL"

    # PRE-BREAKOUT BUY
    if (
        pos >= (1.0 - PREBREAK_EDGE_POS)
        and comp_ok
        and "PRESSURE_UP" in flags
        and "CONTINUATION_UP" in flags
        and ema_state == "EMA_BULL"
        and "SWEEP_UP" not in flags
    ):
        return "PRE_BREAKOUT_BUY"

    return None


# =========================
# ENTRY SIGNAL FILTER
# =========================
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

    rsi_state = s.get("rsi_state")
    direction = str(s.get("direction") or "")

    if rsi_state == "EXTREME_OVERBOUGHT" and "⬆️" in direction:
        return False

    if rsi_state == "EXTREME_OVERSOLD" and "⬇️" in direction:
        return False

    oi = s.get("oi_change", None)

    if oi is not None:
        if oi <= OI_BAD:
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
    avg_move = stats.get("sum_move", 0) / resolved if resolved > 0 else 0

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


def calc_ema(values, period):
    if not values or len(values) < period:
        return None

    try:
        values = [float(x) for x in values]
    except Exception:
        return None

    k = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period

    for v in values[period:]:
        ema = (v * k) + (ema * (1.0 - k))

    return ema


def get_ema_trend(candles):
    closes = extract_closes(candles)

    if not closes or len(closes) < 200:
        return {
            "ema20": None,
            "ema50": None,
            "ema200": None,
            "price": None,
            "state": "EMA_UNKNOWN",
        }

    try:
        price = float(closes[-1])
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        ema200 = calc_ema(closes, 200)
    except Exception:
        return {
            "ema20": None,
            "ema50": None,
            "ema200": None,
            "price": None,
            "state": "EMA_UNKNOWN",
        }

    if ema20 is None or ema50 is None or ema200 is None:
        return {
            "ema20": ema20,
            "ema50": ema50,
            "ema200": ema200,
            "price": price,
            "state": "EMA_UNKNOWN",
        }

    if price > ema20 > ema50 > ema200:
        state = "EMA_BULL"
    elif price < ema20 < ema50 < ema200:
        state = "EMA_BEAR"
    else:
        state = "EMA_MIXED"

    return {
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "price": price,
        "state": state,
    }


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


    if "BREAKOUT_UP" in flags:
        up += 2; reasons.append("Пробой ВВЕРХ (+2)")
    if "BREAKOUT_DOWN" in flags:
        down += 2; reasons.append("Пробой ВНИЗ (+2)")

    if "PRESSURE_UP" in flags:
        up += 1; reasons.append("Давление к верху (+1)")

    if "CONTINUATION_UP" in flags:
        up += 2; reasons.append("Продолжение ВВЕРХ (+2)")
    if "CONTINUATION_DOWN" in flags:
        down += 2; reasons.append("Продолжение ВНИЗ (+2)")
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
        up += 0.5; reasons.append("Стакан: перевес BID (+0.5)")
    if "OB_ASKS" in flags:
        down += 0.5; reasons.append("Стакан: перевес ASK (+0.5)")
    if "OB_WALL_BID" in flags:
        up += 0.5; reasons.append("Стена BID (+0.5)")
    if "OB_WALL_ASK" in flags:
        down += 0.5; reasons.append("Стена ASK (+0.5)")

    if up >= down + 2:
        return "⬆️ ВВЕРХ", reasons, up, down
    if down >= up + 2:
        return "⬇️ ВНИЗ", reasons, up, down
    return "⚖️ БАЛАНС", reasons, up, down

def decision_engine(sig):

    score = sig.get("score", 0)
    acc = sig.get("acc_score", 0)
    oi = sig.get("oi_change")
    ema = sig.get("ema_state")
    rsi = sig.get("rsi_state")
    stage = sig.get("stage", "")
    flags = set(sig.get("flags", []))

    confidence = 0

    confidence += score * 1.2
    confidence += acc * 0.8

    if oi is not None:
        if oi >= OI_STRONG:
            confidence += 2
        elif oi >= OI_GOOD:
            confidence += 1
        elif oi <= OI_BAD:
            confidence -= 2

    if "ВВЕРХ" in sig.get("direction", "") and ema == "EMA_BULL":
        confidence += 1
    if "ВНИЗ" in sig.get("direction", "") and ema == "EMA_BEAR":
        confidence += 1

    if "ATR_EXPANSION" in flags and "VOL_SPIKE" in flags:
        confidence += 2

    if "SWEEP_UP" in flags or "SWEEP_DOWN" in flags:
        confidence += 1

    if rsi in ("EXTREME_OVERBOUGHT", "EXTREME_OVERSOLD"):
        confidence -= 1

    if confidence >= 10:
        return "ELITE"
    elif confidence >= 7:
        return "STRONG"
    elif confidence >= 5:
        return "NORMAL"
    else:
        return "WEAK"

def entry_engine(score, flags, direction_text, up_w, down_w, rsi7, ema_state, price, target):

    if "БАЛАНС" in direction_text:
        return "🔴 WAIT", "Нет явного направления"
    
    strong_confirmed_impulse = (
        ("BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags)
        and ("ATR_EXPANSION" in flags or "VOL_SPIKE" in flags)
    )
    
    # =========================
    # RSI SAFETY FILTER
    # =========================
    
    if direction_text == "⬆️ ВВЕРХ" and rsi7 is not None and rsi7 >= RSI_OB_BLOCK and not strong_confirmed_impulse:
        return "🔴 WAIT", "RSI перегрет — возможен ложный пробой"
    
    if direction_text == "⬇️ ВНИЗ" and rsi7 is not None and rsi7 <= RSI_OS_BLOCK and not strong_confirmed_impulse:
        return "🔴 WAIT", "RSI перепродан — возможен ложный пролив"

    # =========================
    # EMA TREND FILTER
    # =========================
    if direction_text == "⬆️ ВВЕРХ" and ema_state == "EMA_BEAR":
        return "🔴 WAIT", "Сигнал против EMA-тренда вниз"

    if direction_text == "⬇️ ВНИЗ" and ema_state == "EMA_BULL":
        return "🔴 WAIT", "Сигнал против EMA-тренда вверх"

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
        if too_close_to_target(price, target, min_room_pct=0.35):
            return "🔴 WAIT", "Слишком близко к цели — SAFE ENTRY поздний"

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
    aggressive_setup = (
        score >= EDGE_MID_SCORE and any(
            f.startswith("BREAKOUT") or f.startswith("PRESSURE")
            for f in flags
        )
    )

    if aggressive_setup:
        if direction_text == "⬆️ ВВЕРХ" and ema_state == "EMA_BEAR":
            return "🔴 WAIT", "Ранний вход против EMA-тренда вниз"

        if direction_text == "⬇️ ВНИЗ" and ema_state == "EMA_BULL":
            return "🔴 WAIT", "Ранний вход против EMA-тренда вверх"

        return "🟡 AGGRESSIVE", "Ранний вход по структуре"

    return "🔴 WAIT", "Недостаточно факторов"

def smart_money_stage(score, flags):
    flags = set(flags)

    if score < 2:
        return "⚪ NEUTRAL", "Структуры почти нет"

    confirmed_up = "BREAKOUT_CONFIRM_UP" in flags
    confirmed_down = "BREAKOUT_CONFIRM_DOWN" in flags
    breakout_up = "BREAKOUT_UP" in flags
    breakout_down = "BREAKOUT_DOWN" in flags

    pressure_up = "PRESSURE_UP" in flags
    pressure_down = "PRESSURE_DOWN" in flags

    continuation_up = "CONTINUATION_UP" in flags
    continuation_down = "CONTINUATION_DOWN" in flags

    vol = "VOL_SPIKE" in flags
    atr = "ATR_EXPANSION" in flags

    comp = ("COMP_5M" in flags) or ("COMP_15M" in flags)

    fake_dump = "FAKE_DUMP" in flags
    sweep_up = "SWEEP_UP" in flags
    sweep_down = "SWEEP_DOWN" in flags
    bull_trap = "BULL_TRAP" in flags
    bear_trap = "BEAR_TRAP" in flags
    stop_hunt_up = "STOP_HUNT_UP" in flags
    stop_hunt_down = "STOP_HUNT_DOWN" in flags

    # 1. Реальное движение / подтверждённый импульс
    if (
        (confirmed_up or confirmed_down)
        and (
            atr
            or vol
            or (pressure_up and continuation_up)
            or (pressure_down and continuation_down)
        )
    ):
        return "🟢 EXPANSION", "Подтверждённый импульс по направлению"

    # 2. Манипуляция / сбор ликвидности
    if (
        fake_dump
        or sweep_up
        or sweep_down
        or bull_trap
        or bear_trap
        or stop_hunt_up
        or stop_hunt_down
    ):
        return "🟡 MANIPULATION", "Вероятен сбор ликвидности"

    # 3. Накопление / сжатие
    if comp:
        return "🟣 ACCUMULATION", "Накопление/сжатие перед движением"

    # 4. Ранний направленный разгон, но ещё без полного подтверждения
    if (
        (breakout_up and pressure_up)
        or (breakout_down and pressure_down)
        or (continuation_up and pressure_up)
        or (continuation_down and pressure_down)
    ):
        return "🟠 TRANSITION", "Движение начинается, но ещё не полностью подтверждено"

    return "⚪ NEUTRAL", "Смешанные признаки"

    # =========================
    # EARLY PRESSURE DETECTOR
    # =========================
def detect_early_pressure(sig):
    flags = set(sig.get("flags", []))
    stage = str(sig.get("stage", ""))
    ema_state = sig.get("ema_state", "EMA_MIXED")

    up_score = 0
    down_score = 0
    up_reasons = []
    down_reasons = []

    def add_up(points, reason):
        nonlocal up_score
        up_score += points
        up_reasons.append(reason)

    def add_down(points, reason):
        nonlocal down_score
        down_score += points
        down_reasons.append(reason)

    # -------------------------
    # CORE DIRECTIONAL SIGNALS
    # -------------------------
    if "PRESSURE_UP" in flags:
        add_up(3, "PRESSURE_UP")
    if "PRESSURE_DOWN" in flags:
        add_down(3, "PRESSURE_DOWN")

    if "CONTINUATION_UP" in flags:
        add_up(2, "CONTINUATION_UP")
    if "CONTINUATION_DOWN" in flags:
        add_down(2, "CONTINUATION_DOWN")

    if "BREAKOUT_UP" in flags:
        add_up(2, "BREAKOUT_UP")
    if "BREAKOUT_DOWN" in flags:
        add_down(2, "BREAKOUT_DOWN")

    # -------------------------
    # EMA CONTEXT
    # -------------------------
    if ema_state == "EMA_BULL":
        add_up(2, "EMA_BULL")
    elif ema_state == "EMA_BEAR":
        add_down(2, "EMA_BEAR")

    # -------------------------
    # STAGE BOOST
    # -------------------------
    if "ACCUMULATION" in stage:
        if up_score > 0:
            add_up(1, "ACCUMULATION_CONTEXT")
        if down_score > 0:
            add_down(1, "ACCUMULATION_CONTEXT")

    if "TRANSITION" in stage:
        if up_score > 0:
            add_up(1, "TRANSITION_CONTEXT")
        if down_score > 0:
            add_down(1, "TRANSITION_CONTEXT")

    

    # -------------------------
    # VOL / ATR BOOST
    # -------------------------
    if "VOL_SPIKE" in flags:
        if up_score >= 3:
            add_up(1, "VOL_SPIKE")
        if down_score >= 3:
            add_down(1, "VOL_SPIKE")

    if "ATR_EXPANSION" in flags:
        if up_score >= 3:
            add_up(1, "ATR_EXPANSION")
        if down_score >= 3:
            add_down(1, "ATR_EXPANSION")

    result = {
        "early_pressure_side": None,
        "early_pressure_score": 0,
        "early_pressure_label": None,
        "early_pressure_reasons": [],
        "early_pressure_up_score": up_score,
        "early_pressure_down_score": down_score,
    }

    if up_score >= 6 and up_score >= down_score + 2:
        result["early_pressure_side"] = "BUY"
        result["early_pressure_score"] = up_score
        result["early_pressure_label"] = (
            "STRONG_EARLY_BUY_PRESSURE" if up_score >= 8 else "EARLY_BUY_PRESSURE"
        )
        result["early_pressure_reasons"] = up_reasons

    elif down_score >= 6 and down_score >= up_score + 2:
        result["early_pressure_side"] = "SELL"
        result["early_pressure_score"] = down_score
        result["early_pressure_label"] = (
            "STRONG_EARLY_SELL_PRESSURE" if down_score >= 8 else "EARLY_SELL_PRESSURE"
        )
        result["early_pressure_reasons"] = down_reasons

    return result
    

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

    def _bs_skip(reason: str):
        print(f"[BUILD_SIGNAL_SKIP] {instId} {reason}")
        return None

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
        return _bs_skip("c5_empty_or_lt_20")
    if not c15 or len(c15) < 200:
        return _bs_skip("c15_empty_or_lt_200")

    price = float(c5[-1][4])

    ema_meta = get_ema_trend(c15)
    ema_state = ema_meta.get("state", "EMA_UNKNOWN")

    if ema_state == "EMA_BULL":
        flags.add("EMA_BULL")
    elif ema_state == "EMA_BEAR":
        flags.add("EMA_BEAR")
    elif ema_state == "EMA_MIXED":
        flags.add("EMA_MIXED")

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
    # PRE-BREAKOUT PRESSURE
    # =========================
    pre_breakout = detect_pre_breakout_pressure(c5, flags, pmeta, ema_state)
    if pre_breakout:
        flags.add(pre_breakout)
        score += 1

    # =========================
    # CONFLUENCE BONUS
    # =========================
    long_confluence = (
        "PRESSURE_UP" in flags
        and "EMA_BULL" in flags
        and (
            "OB_WALL_BID" in flags
            or "OB_BIDS" in flags
        )
    )

    short_confluence = (
        "PRESSURE_DOWN" in flags
        and "EMA_BEAR" in flags
        and (
            "OB_WALL_ASK" in flags
            or "OB_ASKS" in flags
        )
    )

    if long_confluence:
        flags.add("CONFLUENCE_LONG")
        score += 1

    elif short_confluence:
        flags.add("CONFLUENCE_SHORT")
        score += 1

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

    tgt = liquidity_target(pmeta, flags, price)

    entry, entry_reason = entry_engine(
        score, flags, direction_text, up_w, down_w, rsi7, ema_state, price, tgt
    )

    entry_zone = calc_entry_zone(price, pmeta, flags, direction_code)

    stage, stage_reason = smart_money_stage(score, flags)

    # =========================
    # EXPECTED MOVE
    # =========================
    exp_min, exp_max = expected_move_pct(c5, pmeta)

    # =========================
    # RESULT FILTER
    # =========================
    swing_only_candidate = False
    can_survive_for_swing = True

    if score < MIN_SCORE:

        has_breakout = (
            "BREAKOUT_CONFIRM_UP" in flags
            or "BREAKOUT_CONFIRM_DOWN" in flags
        )

        has_pressure = (
            "PRESSURE_UP" in flags
            or "PRESSURE_DOWN" in flags
        )

        has_continuation = (
            "CONTINUATION_UP" in flags
            or "CONTINUATION_DOWN" in flags
        )

        normal_swing_pass = (
            (
                score >= SWING_BUILD_MIN_SCORE and (
                    acc_score >= 2
                    or has_breakout
                    or has_pressure
                    or has_continuation
                )
            )
            or (
                score >= 3
            )
        )

        early_exception_pass = (
            score == 1 and (
                acc_score >= 3
                or (has_breakout and has_pressure)
                or (acc_score >= 2 and has_pressure)
                or (acc_score >= 2 and has_breakout)
            )
        )

        can_survive_for_swing = (
            normal_swing_pass or early_exception_pass
        )

        if not can_survive_for_swing:
            return _bs_skip(
                f"score_below_min score={score} min={MIN_SCORE}"
            )

        swing_only_candidate = True

    # =========================
    # SIGNAL OBJECT
    # =========================

    tier = get_signal_tier(score, acc_score)

    signal = {
        "instId": instId,
        "symbol": instId,
        "price": price,
        "score": score,
        "swing_only_candidate": swing_only_candidate,
        "below_main_min_score": score < MIN_SCORE,
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
        "ema20": ema_meta.get("ema20"),
        "ema50": ema_meta.get("ema50"),
        "ema200": ema_meta.get("ema200"),
        "ema_state": ema_state,
        "rsi7": rsi7,
        "rsi14": rsi14,
        "rsi_state": rsi_state.get("state"),
        "ts": now_ts(),
        "created_at": time.time(),
    }
    # =========================
    # EXTRA SIGNAL LAYERS
    # =========================
    
    signal.update(detect_early_pressure(signal))
    
    signal["sniper"] = sniper_signal(signal)
    
    # =========================
    # FINAL DECISION
    # =========================
    
    signal["decision"] = decision_engine(signal)
    
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
    base = str(instId).upper().strip()

    if base.endswith("-USDT"):
        base = base[:-5]
    elif base.endswith("USDT"):
        base = base[:-4]

    for s in EXCLUDE_TOKENS_CONTAINS:
        if s in base:
            return True

    return False

# =====================
# MARKET CAP CHECK
# =====================
def is_market_cap_ok(instId, market_caps):
    try:
        base = get_base_coin(instId)
        cap = market_caps.get(base)

        if cap is None:
            return False

        return cap >= MARKET_CAP_MIN_USD

    except Exception:
        return False



# =====================
# MAIN FUNCTION
# =====================
def get_market_candidates_bybit():
    tickers = get_bybit_tickers_linear()
    print("BYBIT TICKERS COUNT:", len(tickers))

    raw_candidates = []

    # =====================
    # СБОР КАНДИДАТОВ
    # =====================
    for t in tickers:
        sym = t.get("symbol", "")

        if not sym.endswith("USDT"):
            continue

        if is_bad_symbol(sym):
            continue

        try:
            vol_usdt = float(t.get("turnover24h") or 0.0)
        except Exception:
            vol_usdt = 0.0

        try:
            last = float(t.get("lastPrice") or 0.0)
            prev = float(t.get("prevPrice24h") or 0.0)
            pct = ((last - prev) / prev * 100.0) if prev > 0 else 0.0
        except Exception:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        abs_pct = abs(pct)

        normal_move_ok = abs_pct >= SCAN_MIN_PCT_24H
        prebreak_move_ok = PREBREAK_SCAN_MIN_PCT_24H <= abs_pct <= PREBREAK_SCAN_MAX_PCT_24H

        if not ACCUMULATION_MODE:
            if not (normal_move_ok or prebreak_move_ok):
                continue

        instId = sym
        raw_candidates.append((instId, vol_usdt, pct))

    # =====================
    # ВНЕ ЦИКЛА
    # =====================
    print(f"[DEBUG] raw_candidates before filter: {len(raw_candidates)}", flush=True)

    if not raw_candidates:
        print("[MARKET_CAP] no raw candidates before market cap filter")
        return []

    raw_candidates.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)

    # =====================
    # MARKET CAP
    # =====================
    MARKET_CAP_PREFETCH_MULT = int(os.getenv("MARKET_CAP_PREFETCH_MULT") or "3")
    prefetch_limit = SCAN_BATCH * MARKET_CAP_PREFETCH_MULT

    prefetch_candidates = raw_candidates[:prefetch_limit]

    base_coins = [get_base_coin(instId) for instId, _, _ in prefetch_candidates]
    market_caps = fetch_market_caps_usd(base_coins)

    if not market_caps:
        print("[MARKET_CAP] SKIPPED (DEBUG MODE)")
        return raw_candidates[:SCAN_TOP_N]

    filtered_candidates = []

    for instId, vol_usdt, pct in raw_candidates:
        if is_market_cap_ok(instId, market_caps):
            filtered_candidates.append((instId, vol_usdt, pct))

    print(
        f"[MARKET_CAP] raw={len(raw_candidates)} "
        f"passed={len(filtered_candidates)}"
    )

    filtered_candidates.sort(key=lambda x: (x[1], abs(x[2])), reverse=True)

    return filtered_candidates[:SCAN_TOP_N]
    
        
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
        except Exception:
            vol_usdt = 0.0

        try:
            last = float(t.get("last") or 0.0)
            open24 = float(t.get("open24h") or 0.0)
            pct = (last - open24) / open24 * 100.0 if open24 > 0 else 0.0
        except Exception:
            pct = 0.0

        if vol_usdt < SCAN_MIN_VOL_USDT:
            continue

        abs_pct = abs(pct)

        normal_move_ok = abs_pct >= SCAN_MIN_PCT_24H
        prebreak_move_ok = PREBREAK_SCAN_MIN_PCT_24H <= abs_pct <= PREBREAK_SCAN_MAX_PCT_24H

        if not ACCUMULATION_MODE:
            if not (normal_move_ok or prebreak_move_ok):
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
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    # 🔥 фильтр реального импульса
    real_impulse = (
        "ATR_EXPANSION" in flags or
        "VOL_SPIKE" in flags or
        "BREAKOUT_CONFIRM_UP" in flags or
        "BREAKOUT_CONFIRM_DOWN" in flags
    )

    if not real_impulse and score < EDGE_HIGH_SCORE:
        return False

    # Expected move filter
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

    # EMA trend filter
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR" and score < EDGE_HIGH_SCORE:
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL" and score < EDGE_HIGH_SCORE:
        return False

    # BTC bias
    if regime == "RISK_OFF" and "ВВЕРХ" in direction:
        return False

    if regime == "RISK_ON" and "ВНИЗ" in direction:
        return False

    return True
      

# =========================
# AI FILTER DEBUG
# =========================
def debug_ai_filter_result(sig, regime, passed, layer="INTRADAY"):

    if not isinstance(sig, dict):
        return

    symbol = sig.get("symbol") or sig.get("instId") or "UNKNOWN"
    score = sig.get("score")
    acc = sig.get("acc_score")
    direction = sig.get("direction")
    tier = sig.get("tier")
    swing_candidate = sig.get("swing_only_candidate")
    below_main = sig.get("below_main_min_score")
    flags = sig.get("flags") or []

    print(
    f"[AI_FILTER][{layer}] "
    f"{'PASSED' if passed else 'BLOCKED'} "
    f"{symbol} | "
    f"regime={regime} | "
    f"score={score} | acc={acc} | tier={tier} | "
    f"direction={direction} | "
    f"oi={oi} | "
    f"swing_candidate={swing_candidate} | "
    f"below_main={below_main} | "
    f"flags={flags}",
    flush=True
)

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
    oi_text = oi_status_text(sig)
    oi_trap = oi_trap_detector(sig)

    lines.append(f"{tier}")
    lines.append(f"🧠 RADAR MEDIUM — {fmt_symbol(inst)}")
    lines.append(f"💵 {price:.6g}")
    lines.append(f"🎯 Expected move: {sig.get('exp_move_min', 0)}–{sig.get('exp_move_max', 0)}%")

    if sig.get("ema_state") and sig.get("ema20") is not None and sig.get("ema50") is not None and sig.get("ema200") is not None:
        lines.append(
            f"📈 EMA: {sig.get('ema_state')} | "
            f"20={sig.get('ema20'):.6g} | "
            f"50={sig.get('ema50'):.6g} | "
            f"200={sig.get('ema200'):.6g}"
        )

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(
            f"📍 RSI7={sig['rsi7']:.1f} | RSI14={sig['rsi14']:.1f} | {sig.get('rsi_state', 'UNKNOWN')}"
        )
    
    lines.append(f"📊 {score}/10 | {direction} (up={up_w}, down={down_w}) | acc={acc}")
    lines.append(f"🎯 ENTRY: {entry} — {entry_reason}")
    
    oi_text = oi_status_text(sig)
    if oi_text:
        lines.append(oi_text)
    
    oi_hint = oi_trap_detector(sig)
    if oi_hint:
        lines.append(oi_hint)
    
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

def oi_badge(oi):
    try:
        oi = float(oi)
    except:
        return "⚪ n/a"

    if oi >= 3:
        return f"🟢 +{oi:.2f}%"
    elif oi >= 1:
        return f"🟡 +{oi:.2f}%"
    elif oi > -1:
        return f"⚪ {oi:.2f}%"
    elif oi > -3:
        return f"🟠 {oi:.2f}%"
    else:
        return f"🔴 {oi:.2f}%"

def dir_badge(direction):
    d = str(direction).upper()

    if "ВВЕРХ" in d or "LONG" in d or "UP" in d:
        return "🟢⬆️⬆️ ВВЕРХ"

    if "ВНИЗ" in d or "SHORT" in d or "DOWN" in d:
        return "🔴⬇️⬇️ ВНИЗ"

    return "⚪↔️ БАЛАНС"

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

    lines.append(
        f"🎯 Expected move: {sig.get('exp_move_min',0)}–{sig.get('exp_move_max',0)}%"
    )

    if (
        sig.get("ema_state")
        and sig.get("ema20") is not None
        and sig.get("ema50") is not None
        and sig.get("ema200") is not None
    ):
        lines.append(
            f"📈 EMA: {sig.get('ema_state')} | "
            f"20={sig.get('ema20'):.6g} | "
            f"50={sig.get('ema50'):.6g} | "
            f"200={sig.get('ema200'):.6g}"
        )

    if sig.get("rsi7") is not None and sig.get("rsi14") is not None:
        lines.append(
            f"📍 RSI7={sig['rsi7']:.1f} | "
            f"RSI14={sig['rsi14']:.1f} | "
            f"{sig.get('rsi_state', 'UNKNOWN')}"
        )

    lines.append(f"📊 Score: {sig['score']}/10 | acc={sig.get('acc_score', 0)}")
    lines.append(
        f"🎯 Direction: {sig['direction']} "
        f"(up={sig['up_w']}, down={sig['down_w']})"
    )
    lines.append(f"🎯 ENTRY: {sig['entry']} — {sig['entry_reason']}")
    lines.append(f"🧬 STAGE: {sig['stage']} — {sig['stage_reason']}")

    pm = sig.get("pmeta") or {}

    if (
        pm.get("range_lo") is not None
        and pm.get("range_hi") is not None
        and pm.get("range_pct") is not None
    ):
        lines.append(
            f"🧲 Range(lookback): "
            f"{pm['range_lo']:.6g} → {pm['range_hi']:.6g} | "
            f"width≈{pm['range_pct']:.2f}%"
        )

    if sig.get("target") is not None:
        lines.append(f"🎯 Liquidity target: {sig['target']:.6g}")

    ez = sig.get("entry_zone")
    if ez:
        lines.append(
            f"📍 Entry zone: {ez.get('zone_type')} | "
            f"{ez.get('low'):.6g} → {ez.get('high'):.6g} | "
            f"stop {ez.get('stop'):.6g}"
        )

    if sig.get("flags"):
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


def swing_grade(sig):
    try:
        score = float(sig.get("score", 0))
        rr = float(sig.get("rr1", 0))
        room = float(sig.get("room_to_target", 0))
        h4 = float(sig.get("h4_bias_score", 0))
        h1 = float(sig.get("h1_setup_score", 0))
        m15 = float(sig.get("m15_trigger_score", 0))

        total = 0.0
        total += score * 0.8
        total += min(rr, 5) * 0.8
        total += h4 * 0.7
        total += h1 * 0.9
        total += m15 * 1.0
        total += min(room, 10) * 0.15

        if sig.get("late"):
            total -= 2

        try:
            entry = float(sig.get("entry_price", 0))
            stop = float(sig.get("stop", 0))

            if entry > 0 and stop > 0:
                stop_pct = abs(entry - stop) / entry * 100

                if stop_pct > 4:
                    total -= 2
                elif stop_pct > 2:
                    total -= 1
        except:
            pass

        total = round(total, 1)

        # ❗ сразу фильтр слабых
        if total < 5:
            return 0, None

        # безопасный риск
        try:
            risk_label, _ = coin_risk_label(sig)
        except:
            risk_label = "unknown"

        # уровни
        if total >= 9:
            title = "🔥 ТОП СДЕЛКА"
        elif total >= 7:
            title = "🟢 GOOD SETUP"
        else:
            title = "🟡 WATCH"

        # понижение из-за риска
        if isinstance(risk_label, str) and "высокий" in risk_label.lower():
            if title == "🔥 ТОП СДЕЛКА":
                title = "🟢 GOOD SETUP"
            elif title == "🟢 GOOD SETUP":
                title = "🟡 WATCH"

        return total, title

    except Exception:
        return 0, None
        
def coin_risk_label(sig):
    try:
        symbol = sig.get("instId") or sig.get("symbol") or ""
        price = float(sig.get("price", 0) or 0)
        oi = sig.get("oi_change")
        flags = set(sig.get("flags", []))

        risk = 0
        reasons = []

        # мемы / мелкие токены чаще резче двигаются
        risky_names = ["1000", "PEPE", "DOGE", "SHIB", "FLOKI", "BONK", "TRUMP", "FART"]
        if any(x in symbol.upper() for x in risky_names):
            risk += 1
            reasons.append("мем/агрессивный токен")

        # очень дешёвые монеты чаще шумные
        if price > 0 and price < 0.05:
            risk += 1
            reasons.append("очень низкая цена")

        # OI падает — интерес уходит
        if oi is not None:
            oi = float(oi)
            if oi <= OI_BAD:
                risk += 1
                reasons.append("OI падает")

        # ловушки/манипуляции
        if (
            "SWEEP_UP" in flags or
            "SWEEP_DOWN" in flags or
            "FAKE_DUMP" in flags or
            "BULL_TRAP" in flags or
            "BEAR_TRAP" in flags
        ):
            risk += 1
            reasons.append("есть признаки манипуляции")

        if risk >= 3:
            return "🔴 Риск монеты: высокий", reasons[:3]

        if risk >= 1:
            return "🟡 Риск монеты: средний", reasons[:3]

        return "🟢 Риск монеты: нормальный", reasons[:3]

    except Exception:
        return "⚪ Риск монеты: нет данных", []

def stop_risk_text(sig):
    try:
        entry = sig.get("entry_price")
        stop = sig.get("stop")
        side = str(sig.get("side", "")).upper()

        if entry is None or stop is None:
            return None

        entry = float(entry)
        stop = float(stop)

        if entry <= 0:
            return None

        dist_pct = abs(entry - stop) / entry * 100
        dist_pct = round(dist_pct, 2)

        if dist_pct <= 1.0:
            return f"🟢 Стоп: хороший ({dist_pct}%)"

        if dist_pct <= 2.5:
            return f"🟡 Стоп: широкий ({dist_pct}%)"

        return f"🔴 Стоп: высокий риск ({dist_pct}%)"

    except Exception:
        return None
    
def msg_swing(sig):
    side = str(sig.get("side", "")).upper()

    if side == "LONG":
        icon = "🟢"
        side_ru = "ЛОНГ / вверх"
    else:
        icon = "🔴"
        side_ru = "ШОРТ / вниз"

    score, grade = swing_grade(sig)
    score_show = min(score, 10)

    # --- переводы внутренних кодов ---
    h1_map = {
        "pullback_hold": "откат удержан",
        "breakout_retest": "пробой + ретест",
        "range_break": "выход из диапазона",
        "trend_hold": "тренд удерживается",
        "none": "нет структуры"
    }

    m15_map = {
        "momentum_ready": "импульс готов",
        "compression_break": "выход из сжатия",
        "breakout_push": "пробой с ускорением",
        "retest_hold": "ретест удержан",
        "none": "нет триггера"
    }

    h1_raw = str(sig.get("h1_setup_type", "none"))
    m15_raw = str(sig.get("m15_trigger_type", "none"))

    h1_text = h1_map.get(h1_raw, h1_raw)
    m15_text = m15_map.get(m15_raw, m15_raw)

    lines = []
    lines.append(f"{icon} <b>{grade} — {sig['symbol']}</b>")
    lines.append("")
    lines.append(f"📊 Сила сигнала: <b>{score_show}/10</b>")
    lines.append(f"🧭 Направление: <b>{side_ru}</b>")
    lines.append("")

    lines.append("📈 Контекст:")
    lines.append(f"• H4: <b>{sig.get('h4_bias','?')}</b>")
    lines.append(f"• H1: <b>{h1_text}</b>")
    lines.append(f"• M15: <b>{m15_text}</b>")

    if sig.get("entry_price") is not None:
        lines.append("")
        lines.append("🎯 План:")
        lines.append(f"• Вход: <b>{sig['entry_price']}</b>")

    if sig.get("stop") is not None:
        lines.append(f"• Стоп: <b>{sig['stop']}</b>")

    if sig.get("tp1") is not None:
        lines.append(f"• TP1: <b>{sig['tp1']}</b>")

    if sig.get("tp2") is not None:
        lines.append(f"• TP2: <b>{sig['tp2']}</b>")

    if sig.get("rr1") is not None:
        lines.append(f"• RR: <b>{sig['rr1']}</b>")
    stop_info = stop_risk_text(sig)
    if stop_info:
        lines.append(f"• {stop_info}")

    room = sig.get("room_to_target")
    if room is not None:
        lines.append(f"• Потенциал: <b>{room}%</b>")

    oi = oi_status_text(sig)
    if oi:
        lines.append("")
        lines.append(oi)

    risk_label, risk_reasons = coin_risk_label(sig)
    lines.append(risk_label)

    if risk_reasons:
        lines.append("Причины риска:")
        for r in risk_reasons:
            lines.append(f"• {r}")

    lines.append("")
    lines.append("🧠 Что делать:")

    verdict = str(sig.get("verdict", "")).lower()

    if "можно" in verdict:
        lines.append("✅ Можно искать вход по M15 после подтверждения.")
    elif "наблюдать" in verdict:
        lines.append("⏳ Пока наблюдать.")
    elif "skip" in verdict:
        lines.append("❌ Лучше пропустить.")
    else:
        lines.append(sig.get("verdict", "наблюдать"))

    return "\n".join(lines)


def oi_status_text(sig):
    try:
        oi = sig.get("oi_change", None)

        if oi is None:
            return "⚪ OI: нет данных"

        if oi >= OI_STRONG:
            return f"🟢 OI: +{oi}% — сильный приток денег"

        if oi >= OI_GOOD:
            return f"🟡 OI: +{oi}% — умеренный приток"

        if oi <= OI_BAD:
            return f"🔴 OI: {oi}% — интерес падает"

        return f"⚪ OI: {oi}% — нейтрально"

    except Exception:
        return "⚪ OI: ошибка чтения"


def oi_trap_detector(sig):
    try:
        flags = sig.get("flags", [])
        oi = sig.get("oi_change", None)
        direction = str(sig.get("direction", ""))
        score = float(sig.get("score", 0) or 0)

        if oi is None:
            return None

        # REAL MONEY MOVE
        if "⬆️" in direction and oi >= OI_STRONG:
            return "✅ OI CONFIRM: рост поддержан новыми деньгами"

        if "⬇️" in direction and oi >= OI_STRONG:
            return "✅ OI CONFIRM: падение поддержано новыми шортами"

        # WEAK MOVE
        if "⬆️" in direction and oi <= OI_BAD:
            return "⚠️ OI WARNING: цена растёт, но интерес падает"

        if "⬇️" in direction and oi <= OI_BAD:
            return "⚠️ OI WARNING: цена падает, возможна фиксация"

        # TRAP RISK
        if "BREAKOUT_UP" in flags and oi < OI_GOOD:
            return "🪤 TRAP RISK: пробой вверх без сильного OI"

        if "BREAKOUT_DOWN" in flags and oi < OI_GOOD:
            return "🪤 TRAP RISK: пробой вниз без сильного OI"

        # IMPULSE
        if "VOL_SPIKE" in flags and oi >= OI_STRONG and score >= 6:
            return "💥 IMPULSE: объём + OI подтверждают движение"

        return None

    except Exception:
        return None


def choose_detail_message(sig):
    if MESSAGE_MODE == "SHORT":
        msg = msg_short(sig)
    elif MESSAGE_MODE == "MEDIUM":
        msg = msg_medium(sig)
    elif MESSAGE_MODE == "FULL":
        msg = msg_full(sig)
    elif sig["score"] >= EDGE_HIGH_SCORE:
        msg = msg_full(sig)
    elif sig["score"] >= EDGE_MID_SCORE:
        msg = msg_medium(sig)
    else:
        msg = msg_short(sig)

    oi = sig.get("oi_change")
    if oi is not None:
        msg += f"\n📊 OI: {oi_badge(oi)}"

    return msg

def summary_message(alerts, cycle_info, regime):

    if not alerts:
        return None

    lines = []
    lines.append("🚨 SMART MONEY SCAN")
    lines.append(f"⏱ {cycle_info}")
    lines.append(f"🧭 BTC: {regime}")
    lines.append("")

    clean = []
    used = set()

    for s in alerts:
        sym = s.get("instId")
        if sym in used:
            continue
        used.add(sym)
        clean.append(s)

    top = clean[:3]

    for i, sig in enumerate(top, start=1):
        sym = sig.get("instId", "?")
        score = round(float(sig.get("score", 0)), 2)
        rank = round(float(sig.get("rank", 0)), 1)

        direction = dir_badge(sig.get("direction", ""))
        entry = sig.get("entry", "WAIT")
        stage = sig.get("stage", "NEUTRAL")

        oi = sig.get("oi_change", None)
        oi_text = oi_badge(oi) if oi is not None else "n/a"

        lines.append(
            f"{i}) {sym} | "
            f"score {score}/10 | "
            f"rank {rank} | "
            f"{direction} | "
            f"{entry} | "
            f"{stage} | "
            f"OI {oi_text}"
        )

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

    if not watch:
        return None

    top_n = min(len(watch), 3)
    lines.append(f"Top {top_n}:")

    for sig in watch[:top_n]:
        sym = sig.get("instId") or sig.get("symbol") or sig.get("sym") or "?"
        acc = sig.get("acc_score", 0)
        stage = sig.get("stage", "")
        direction = sig.get("direction", "")
        score = float(sig.get("score") or sig.get("rank") or 0)

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
    prev_entry = ss.get("prev_entry")
    prev_direction = ss.get("prev_direction")
    last_alert_ts = ss.get("last_alert_ts", 0)

    now = now_ts()

    if now - int(last_alert_ts or 0) < ALERT_COOLDOWN_SEC:
        return False

    cur_score = sig.get("score", 0)
    cur_flags = sig.get("flags", [])
    cur_entry = sig.get("entry")
    cur_direction = sig.get("direction")

    same_signal = (
        prev_score == cur_score
        and prev_entry == cur_entry
        and prev_direction == cur_direction
        and set(prev_flags) == set(cur_flags)
    )

    if same_signal:
        return False

    changed = (
        prev_score is None
        or cur_score != prev_score
        or set(cur_flags) != set(prev_flags)
        or cur_entry != prev_entry
        or cur_direction != prev_direction
    )

    crossed = (prev_score or 0) < ALERT_MIN_SCORE and cur_score >= ALERT_MIN_SCORE

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

    state["symbols"][sym]["prev_score"] = sig.get("score")
    state["symbols"][sym]["prev_flags"] = sig.get("flags", [])
    state["symbols"][sym]["prev_entry"] = sig.get("entry")
    state["symbols"][sym]["prev_direction"] = sig.get("direction")
    state["symbols"][sym]["last_ts"] = sig.get("ts", now_ts())
    
def safe_entry_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_safe_entry_ts", 0) or 0)
    return (now_ts() - last) < SAFE_ENTRY_SUPPRESS_SEC


def mark_safe_entry(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_safe_entry_ts"] = now_ts()

def should_send_summary(state, text):
    last = state.get("last_summary_text", "")
    now = now_ts()
    last_ts = int(state.get("last_summary_ts", 0) or 0)

    # одинаковое сообщение меньше 10 минут не шлем
    if text == last and (now - last_ts) < 600:
        return False

    state["last_summary_text"] = text
    state["last_summary_ts"] = now
    return True

# =========================
# START AFTERGLOW
# =========================
def start_afterglow_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_start_trigger_ts", 0) or 0)
    return (now_ts() - last) < START_AFTERGLOW_SEC

# =========================
# EARLY ALERT COOLDOWN
# =========================
def early_alert_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_early_alert_ts", 0) or 0)
    return (now_ts() - last) < EARLY_ALERT_COOLDOWN_SEC


def mark_early_alert(state, instId):
    state["symbols"].setdefault(instId, {})
    state["symbols"][instId]["last_early_alert_ts"] = now_ts()

# =========================
# START TRIGGER RECENCY LOCK
# =========================
def start_trigger_recent(state, instId):
    ss = state["symbols"].get(instId, {})
    last = int(ss.get("last_start_trigger_ts", 0) or 0)
    return (now_ts() - last) < TRIGGER_START_COOLDOWN

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
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    if score < 5:
        return False

    if acc < TRIGGER_PRE_ACC:
        return False

    # не даём PRE в полном балансе
    if "БАЛАНС" in direction:
        return False

    # не даём PRE против явного EMA-тренда
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR":
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL":
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
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")

    if score < 5:
        return False

    if acc < TRIGGER_PRE_ACC:
        return False

    # EMA filter: не даём START против явного EMA-тренда
    if "ВВЕРХ" in direction and ema_state == "EMA_BEAR":
        return False

    if "ВНИЗ" in direction and ema_state == "EMA_BULL":
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

# =========================
# EARLY PRESSURE ALERT FILTER
# =========================
def is_early_pressure_alert(sig):
    side = sig.get("early_pressure_side")
    ep_score = float(sig.get("early_pressure_score") or 0)
    label = sig.get("early_pressure_label")
    direction = str(sig.get("direction", ""))
    ema_state = sig.get("ema_state", "EMA_UNKNOWN")
    stage = str(sig.get("stage", ""))
    flags = set(sig.get("flags", []))
    price = sig.get("price")
    target = sig.get("target")

    if not side or not label:
        return False

    if ep_score < 7:
        return False

    # не шлём, если уже есть SAFE ENTRY
    if "SAFE ENTRY" in str(sig.get("entry", "")):
        return False

    # не шлём в полном балансе
    if "БАЛАНС" in direction:
        return False

    # направление должно совпадать
    if side == "BUY" and "ВВЕРХ" not in direction:
        return False

    if side == "SELL" and "ВНИЗ" not in direction:
        return False

    # против явного EMA не шлём
    if side == "BUY" and ema_state == "EMA_BEAR":
        return False

    if side == "SELL" and ema_state == "EMA_BULL":
        return False

    # слишком близко к цели — поздно
    if too_close_to_target(price, target, min_room_pct=0.35):
        return False

    # если уже confirm / expansion — это не early
    if "BREAKOUT_CONFIRM_UP" in flags or "BREAKOUT_CONFIRM_DOWN" in flags:
        return False

    if "ATR_EXPANSION" in flags:
        return False

    if "EXPANSION" in stage:
        return False

    # stage должен быть ранний
    if ("ACCUMULATION" not in stage) and ("TRANSITION" not in stage) and ("NEUTRAL" not in stage):
        return False

    # NEUTRAL пускаем только если есть явный PRE_BREAKOUT
    if "NEUTRAL" in stage:
        if "PRE_BREAKOUT_BUY" not in flags and "PRE_BREAKOUT_SELL" not in flags:
            return False

    # нужен реальный directional stack
    directional_ok = (
        "PRE_BREAKOUT_BUY" in flags or
        "PRE_BREAKOUT_SELL" in flags or
        "PRESSURE_UP" in flags or
        "PRESSURE_DOWN" in flags or
        "CONTINUATION_UP" in flags or
        "CONTINUATION_DOWN" in flags
    )

    if not directional_ok:
        return False

    return True



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
# EARLY PRESSURE MESSAGE
# =========================
def msg_early_pressure(sig):
    sym = fmt_symbol(sig["instId"])
    side = sig.get("early_pressure_side", "?")
    ep_score = sig.get("early_pressure_score", 0)
    label = sig.get("early_pressure_label", "EARLY_PRESSURE")
    reasons = sig.get("early_pressure_reasons", [])

    arrow = "⬆️ BUY PRESSURE" if side == "BUY" else "⬇️ SELL PRESSURE"

    lines = []
    lines.append(f"🟠 {label} — {sym}")
    lines.append(f"💵 {sig['price']:.6g} | pressure_score={ep_score}")
    lines.append(f"🧭 {arrow} | {sig.get('direction', '')}")
    lines.append(f"🧬 STAGE: {sig.get('stage', '')}")
    lines.append(f"🎯 ENTRY: {sig.get('entry', '')}")

    if sig.get("target") is not None:
        lines.append(f"🎯 ликвидность/цель: {sig['target']:.6g}")

    if reasons:
        lines.append("Причины:")
        for r in reasons[:6]:
            lines.append(f"• {r}")

    lines.append("Действие: открыть график и смотреть вход по малому риску. Это раннее давление, не confirm.")

    return "\n".join(lines)

def msg_market_pressure(buy_symbols, sell_symbols):
    buy_count = len(buy_symbols)
    sell_count = len(sell_symbols)

    if sell_count >= 4 and sell_count >= buy_count + 2:
        lines = []
        lines.append("🚨 MARKET SELL-OFF / RISK-OFF")
        lines.append(f"SELL pressure: {sell_count} | BUY pressure: {buy_count}")
        lines.append(f"Монеты: {', '.join(sell_symbols[:8])}")
        lines.append("Смысл: рынок широко давят вниз. Ищем слабые альты, шорт-сетапы и не лезем в случайные лонги.")
        return "\n".join(lines)

    if buy_count >= 4 and buy_count >= sell_count + 2:
        lines = []
        lines.append("🚀 MARKET BUY PRESSURE / RISK-ON")
        lines.append(f"BUY pressure: {buy_count} | SELL pressure: {sell_count}")
        lines.append(f"Монеты: {', '.join(buy_symbols[:8])}")
        lines.append("Смысл: рынок широко тащат вверх. Ищем сильные альты и продолжение движения.")
        return "\n".join(lines)

    return None

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

        # ⏱ ждём минимум RESULT_CHECK_SEC секунд
        if time.time() - created_at < RESULT_CHECK_SEC:
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

            resolved = stats.get("resolved", stats.get("hit", 0) + stats.get("fail", 0))

            if resolved > 0 and resolved % 5 == 0:
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

def rank_signal(sig):
    try:
        rank = 0.0

        rank += float(sig.get("score", 0)) * 1.2
        rank += min(float(sig.get("rr1", 0)), 5) * 1.5
        rank += float(sig.get("h4_bias_score", 0)) * 0.8
        rank += float(sig.get("h1_setup_score", 0)) * 1.0
        rank += float(sig.get("m15_trigger_score", 0)) * 1.1

        if str(sig.get("status", "")) == "SWING TRIGGER":
            rank += 2.0

        if sig.get("late"):
            rank -= 3.0

        verdict = str(sig.get("verdict", "")).lower()

        if "широкая" in verdict:
            rank -= 2.0

        if "близко" in verdict:
            rank -= 2.0

        # ===== OPEN INTEREST BONUS =====
        oi = sig.get("oi_change")

        if oi is not None:
            if oi >= OI_STRONG:
                rank += 2.0
            elif oi >= OI_GOOD:
                rank += 1.0
            elif oi <= OI_BAD:
                rank -= 1.0

        return round(rank, 2)

    except:
        return 0.0

def confirm_grade(sig):
    try:
        checks = 0

        if sig.get("rr1", 0) >= 1.8:
            checks += 1

        if sig.get("h4_bias_score", 0) >= 3:
            checks += 1

        if sig.get("h1_setup_score", 0) >= 3:
            checks += 1

        if sig.get("m15_trigger_score", 0) >= 3:
            checks += 1

        if not sig.get("late", False):
            checks += 1

        status = str(sig.get("status", ""))

        if status == "SWING TRIGGER" and checks >= 4:
            return "CONFIRMED"

        if checks >= 3:
            return "WATCH"

        return "SKIP"

    except:
        return "SKIP"

def get_open_interest_change(symbol):
    try:
        url = "https://api.bybit.com/v5/market/open-interest"
        params = {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": "5min"
        }

        r = requests.get(url, params=params, timeout=8)
        data = r.json()

        rows = data["result"]["list"]
        if len(rows) < 2:
            return None

        now_oi = float(rows[0]["openInterest"])
        prev_oi = float(rows[1]["openInterest"])

        if prev_oi <= 0:
            return None

        change = (now_oi - prev_oi) / prev_oi * 100
        return round(change, 2)

    except:
        return None

def is_best_only_signal(sig):
    try:
        rank = float(sig.get("rank", 0))
        score = float(sig.get("score", 0))
        acc = int(sig.get("acc_score", 0))
        rr1 = float(sig.get("rr1", 0))
        late = bool(sig.get("late", False))
        grade = str(sig.get("grade", "SKIP"))
        oi = sig.get("oi_change")

        if grade != "CONFIRMED":
            return False

        if rank < 22:
            return False

        if score < 7:
            return False

        if acc < 2:
            return False

        if rr1 < 3:
            return False

        if late:
            return False

        if oi is not None and float(oi) < -0.05:
            return False

        return True

    except:
        return False
                           
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

    if not isinstance(state.get("swing_sent"), dict):
        state["swing_sent"] = {}

    # =====================
    # SCAN SETTINGS
    # =====================

    SCAN_BATCH = int(os.getenv("SCAN_BATCH") or "75")
    TOP_ALERTS_LIMIT = int(os.getenv("TOP_ALERTS_LIMIT") or "3")
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
            MARKET_MODE = "NEUTRAL"

            if "BULL" in str(regime).upper() or "UP" in str(regime).upper():
                MARKET_MODE = "BULL"
            
            elif "BEAR" in str(regime).upper() or "DOWN" in str(regime).upper():
                MARKET_MODE = "BEAR"

            alerts = []
            manip_watch = []
            early_count = 0
            start_count = 0
            pre_count = 0
            early_buy_symbols = []
            early_sell_symbols = []
            swing_candidates = []

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
                print(f"[LOOP] {instId} start")
                print(f"[STEP1] {instId} before_fetch")
                

                time.sleep(0.55)

                try:

                    sig = build_signal(instId)
    
                    if not isinstance(sig, dict):
                        print(f"[RAW_SKIP] {instId} build_signal_returned_non_dict")
                        continue
                    
                    # =====================
                    # OI BLOCK (FIX)
                    # =====================
                    
                    oi_ttl = int(os.getenv("OI_CACHE_TTL_SEC", "1800"))
                    
                    new_oi = get_open_interest_change(instId)
                    
                    state["symbols"].setdefault(instId, {})
                    sym_state = state["symbols"][instId]
                    
                    prev = sym_state.get("last_oi_change")
                    prev_ts = int(sym_state.get("last_oi_ts", 0) or 0)
                    age = now_ts() - prev_ts if prev_ts else None
                    
                    if new_oi is not None:
                        sig["oi_change"] = new_oi
                        sym_state["last_oi_change"] = new_oi
                        sym_state["last_oi_ts"] = now_ts()
                    
                        print(f"[OI_NEW] {instId} fresh OI={new_oi}%", flush=True)
                    
                    elif prev is not None and age is not None and age <= oi_ttl:
                        sig["oi_change"] = prev
                        print(f"[OI_CACHE] {instId} cached OI={prev}% age={age}s", flush=True)
                    
                    else:
                        sig["oi_change"] = None
                        print(f"[OI_NONE] {instId} no fresh OI / cache expired", flush=True)
                    
                
                    # =====================
                    # LOAD CANDLES FOR TA
                    # =====================
                    
                    
                    candles_m15 = get_tf_candles(instId, "15m", 200)
                    candles_h1 = get_tf_candles(instId, "1h", 200)
                    candles_day = get_tf_candles(instId, "1d", 200)
                    candles_month = get_tf_candles(instId, "1M", 120)
                    
                    # =====================
                    # TA SNIPER (SAFE)
                    # =====================
                    
                    try:
                        if (
                            is_empty(candles_m15) or
                            is_empty(candles_h1) or
                            is_empty(candles_day) or
                            is_empty(candles_month)
                        ):
                            print(f"[TA_SKIP] {instId} empty candles", flush=True)
                            ta = None
                        else:
                            ta = analyze_ta_sniper(
                                symbol=instId,
                                candles_month=candles_month,
                                candles_day=candles_day,
                                candles_h1=candles_h1,
                                candles_m15=candles_m15,
                                max_stop_pct=3.5
                            )
                   
                    except Exception as e:
                        print(f"[TA_ERROR] {instId} {type(e).__name__}: {e}", flush=True)
                        print(traceback.format_exc(), flush=True)
                        ta = None 
                         
                    # =====================
                    # ELITE FILTER
                    # =====================
                    
                    elite = False
                    reasons = []
                    
                    # 1. есть swing сигнал
                    if sig.get("sendable"):
                        elite = True
                        reasons.append("SWING_OK")
                    
                    # 2. хороший риск/прибыль
                    rr1 = sig.get("rr1", 0)
                    if rr1 >= 2:
                        reasons.append("RR_OK")
                    else:
                        elite = False
                    
                    # 3. подтверждение давления
                    flags = sig.get("flags", [])
                    if "PRESSURE_DOWN" in flags or "PRESSURE_UP" in flags:
                        reasons.append("PRESSURE_OK")
                    else:
                        elite = False
                    
                    # 4. TA совпадает по направлению
                    if isinstance(ta, dict) and ta.get("entry") and ta.get("stop") and ta.get("tp1"):
                        if ta.get("side") == sig.get("side"):
                            reasons.append("TA_CONFIRM")
                        else:
                            elite = False
                    else:
                        elite = False

                    if elite:
                        send_telegram(
                            f"🔥 <b>ELITE SIGNAL — {instId}</b>\n\n"
                            f"🧭 Side: {sig.get('side')}\n"
                            f"💰 Price: {sig.get('price')}\n"
                            f"📊 RR: {rr1}\n\n"
                            f"📌 Reasons: {', '.join(reasons)}"
                        )

                    
                    # =====================
                    # SEND SIGNAL
                    # =====================
                    
                    if isinstance(ta, dict) and ta.get("entry") and ta.get("stop") and ta.get("tp1"):
                        send_telegram(
                            f"🎯 <b>TA SNIPER — {ta.get('symbol')}</b>\n\n"
                            f"🧭 Направление: <b>{ta.get('side')}</b>\n"
                            f"💵 Вход: <b>{ta.get('entry')}</b>\n"
                            f"🛑 Стоп: <b>{ta.get('stop')}</b> ({ta.get('stop_pct')}%)\n"
                            f"🎯 TP1: <b>{ta.get('tp1')}</b>\n"
                            f"🎯 TP2: <b>{ta.get('tp2')}</b>\n\n"
                            f"📍 Уровень: <b>{ta.get('level_price')}</b>\n"
                            f"📏 Дистанция: <b>{ta.get('level_distance_pct')}%</b>\n"
                            f"📦 Проторговка: <b>{ta.get('range_bars')} свечей</b>\n"
                            f"🧲 Диапазон: {ta.get('range_low')} → {ta.get('range_high')}\n"
                            f"💪 Buyer: {ta.get('buyer_power')} | Seller: {ta.get('seller_power')}\n"
                            f"⚡ Breakout: {ta.get('breakout')}"
                        )
                                                        
                   
            
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

                    print(f"[STEP2] {instId} after_build_signal")

                    # =====================
                    # MARKET CONTEXT
                    # =====================

                    print(f"[STEP3] {instId} before_market_context")
                    sig = apply_market_context(sig)
                    print(f"[SIG_RAW] {instId} sig_exists={bool(sig)}")
                    
                    if not sig:
                        print(f"[RAW_SKIP] {instId} no_signal_from_analyzer")
                        continue

                    # =====================
                    # REGIME BIAS
                    # =====================

                    sig = apply_regime_bias(sig, regime)
                    # AI market filter
                    if MARKET_MODE == "BEAR":
                        if "⬆️" in str(sig.get("direction", "")) and float(sig.get("score", 0)) < 7:
                            print(f"[AI_FILTER] skip weak long in bear market {instId}")
                            continue
                    
                    if MARKET_MODE == "BULL":
                        if "⬇️" in str(sig.get("direction", "")) and float(sig.get("score", 0)) < 7:
                            print(f"[AI_FILTER] skip weak short in bull market {instId}")
                            continue

                    # =====================
                    # SAVE SIGNAL
                    # =====================

                    print(
                        f"[SCAN] {instId} "
                        f"price={sig.get('price')} "
                        f"score={sig.get('score')} "
                        f"acc={sig.get('acc_score')} "
                        f"oi={sig.get('oi_change')} "
                        f"flags={sig.get('flags')}"
                    )

                    # =====================
                    # SWING LAYER (H4 / H1 / M15) — ADDON, НЕ ЛОМАЕТ СКАЛЬП
                    # =====================
                    if SWING_MODE:
                        try:
                            swing_sent = state.get("swing_sent", {})
                            if not isinstance(swing_sent, dict):
                                swing_sent = {}
                    
                            last_sw_ts = float(swing_sent.get(instId, 0) or 0)
                            now_sw_ts = time.time()
                    
                            same_symbol_locked = (now_sw_ts - last_sw_ts) < SWING_ALERT_COOLDOWN_SEC
                            can_send_swing = (not same_symbol_locked) if SWING_ONE_IDEA_PER_SYMBOL else True
                    
                            swing_candidate = (
                                float(sig.get("score", 0) or 0) >= max(4, MIN_SCORE)
                                or int(sig.get("acc_score", 0) or 0) >= 2
                                or bool(sig.get("early_pressure_label"))
                            )
                    
                            if can_send_swing and swing_candidate:
                                df_h4 = get_tf_candles(instId, tf="4h", limit=200) if SWING_USE_H4 else pd.DataFrame()
                                df_h1 = get_tf_candles(instId, tf="1h", limit=200) if SWING_USE_H1 else pd.DataFrame()
                                df_m15 = get_tf_candles(instId, tf="15m", limit=200) if SWING_USE_M15 else pd.DataFrame()
                                market_phase = detect_market_phase(df_h1)

                                print(f"[MARKET] {instId} phase={market_phase['phase']} score={market_phase['score']}", flush=True)
                    
                                h4_ctx = analyze_h4_context(df_h4) if not df_h4.empty else {"ok": False}
                                h1_setup = analyze_h1_setup(df_h1, h4_ctx) if not df_h1.empty else {"ok": False}
                                m15_trigger = analyze_m15_trigger(df_m15, h1_setup, h4_ctx) if not df_m15.empty else {"ok": False}

                                # =====================
                                # CAP + VOLUME BOOST (SWING)
                                # =====================
                                try:
                                    base = get_base_coin(instId)
                                    mcap = market_caps.get(base) if 'market_caps' in locals() or 'market_caps' in globals() else None
                                
                                    sig["market_cap"] = mcap
                                
                                    vol = float(sig.get("volume") or sig.get("vol") or 0)
                                    avg = float(sig.get("avg_volume") or sig.get("vol_avg") or 0)
                                
                                    if mcap and vol > 0 and avg > 0:
                                
                                        vol_ratio = vol / avg
                                
                                        # 🔥 сильный вход денег
                                        if vol_ratio >= 2.0:
                                
                                            if mcap < 500_000_000:
                                                sig["score"] += 2
                                                sig.setdefault("flags", []).append("CAP_VOL_STRONG")
                                                print(f"[CAP+VOL BOOST STRONG] {instId}", flush=True)
                                
                                            elif mcap < 2_000_000_000:
                                                sig["score"] += 1
                                                sig.setdefault("flags", []).append("CAP_VOL_MID")
                                                print(f"[CAP+VOL BOOST MID] {instId}", flush=True)
                                
                                except Exception as e:
                                    print(f"[CAP_VOL_ERROR] {instId} {e}", flush=True)
                                                    
                                    swing_sig = build_swing_signal(instId, h4_ctx, h1_setup, m15_trigger, sig) or {
                                        "status": "NONE",
                                        "sendable": False,
                                        "late": False,
                                        "rr1": 0.0,
                                        "verdict": "no_signal",
                                        "side": "NEUTRAL",
                                        "h4_bias": "NONE",
                                        "h1_setup_type": "none",
                                        "m15_trigger_type": "none"
                                    }
                                    try:
                                        rr = float(swing_sig.get("rr1"))
                                    except:
                                        rr = 0
                                    
                                    if rr <= 0:
                                        rr = 2.0
                                        print(f"[RR_FIX_SWING] {instId} fallback rr=2.0", flush=True)
                                                                
                                swing_sig["rr1"] = rr
                                # =====================
                                # SWING OVERRIDE (СРЕДНЕСРОК БЕЗ M15)
                                # =====================
                                
                                if not swing_sig.get("sendable"):
                                
                                    h4_ok = h4_ctx.get("ok")
                                    h1_ok = h1_setup.get("ok")
                                
                                    if h4_ok and h1_ok:
                                        print(f"[SWING_OVERRIDE] {instId} HTF strong → allow", flush=True)
                                
                                        swing_sig["sendable"] = True
                                        swing_sig["status"] = "HTF_SWING"
                                        swing_sig["verdict"] = "htf_override"

                                # =====================
                                # FIX SIDE FROM H4
                                # =====================
                                
                                if swing_sig.get("side") == "NEUTRAL":
                                
                                    h4_bias = h4_ctx.get("bias")
                                
                                    if h4_bias == "LONG":
                                        swing_sig["side"] = "BUY"
                                
                                    elif h4_bias == "SHORT":
                                        swing_sig["side"] = "SELL"
                    
                                print(
                                    f"[SWING_DEBUG] {instId} "
                                    f"h4_ok={h4_ctx.get('ok')} "
                                    f"h4_bias={h4_ctx.get('bias')} "
                                    f"h1_ok={h1_setup.get('ok')} "
                                    f"h1_type={h1_setup.get('setup_type')} "
                                    f"m15_ok={m15_trigger.get('ok')} "
                                    f"m15_type={m15_trigger.get('trigger_type')} "
                                    f"status={swing_sig.get('status')} "
                                    f"sendable={swing_sig.get('sendable')} "
                                    f"rr1={swing_sig.get('rr1')} "
                                    f"verdict={swing_sig.get('verdict')}"
                                )
                    
                                if swing_sig.get("sendable"):
                                    swing_candidates.append(swing_sig)
                                
                                    send_telegram(msg_swing(swing_sig))
                                
                                    swing_sent[instId] = now_sw_ts
                                    state["swing_sent"] = swing_sent
                                
                                    print(
                                        f"[SWING] {instId} "
                                        f"side={swing_sig.get('side')} "
                                        f"rr1={swing_sig.get('rr1')}"
                                    )
                    
                        except Exception as e:
                            import traceback
                            print(f"[SWING_ERROR] {instId}: {e}")
                            print(traceback.format_exc())
                    if sig.get("swing_only_candidate"):
                        print(
                            f"[SWING_ONLY_SKIP_MAIN] {instId} "
                            f"score={sig.get('score')} "
                            f"acc={sig.get('acc_score')} "
                            f"flags={sig.get('flags')}"
                        )
                        continue

                    if sig.get("early_pressure_label"):
                        flags_set = set(sig.get("flags", []))
                        stage_txt = str(sig.get("stage", ""))

                        is_late_candidate = (
                            ("BREAKOUT_CONFIRM_UP" in flags_set)
                            or ("BREAKOUT_CONFIRM_DOWN" in flags_set)
                            or ("ATR_EXPANSION" in flags_set)
                            or ("EXPANSION" in stage_txt)
                        )

                        if not is_late_candidate:
                            print(
                                f"[EARLY_CANDIDATE] {instId} "
                                f"side={sig.get('early_pressure_side')} "
                                f"score={sig.get('early_pressure_score')} "
                                f"label={sig.get('early_pressure_label')} "
                                f"up={sig.get('early_pressure_up_score')} "
                                f"down={sig.get('early_pressure_down_score')} "
                                f"reasons={sig.get('early_pressure_reasons')}"
                            )


                    entry_ok_for_save = is_entry_signal(sig)
                    same_side_open = has_open_similar_signal(sig)
                    any_open_same_symbol = has_any_open_signal_for_symbol(instId)
                    
                    if entry_ok_for_save:
                        if same_side_open:
                            print(f"[SAVE_SKIP] {instId} same-side open signal already exists")
                    
                        elif ONE_OPEN_SIGNAL_PER_SYMBOL and any_open_same_symbol:
                            print(f"[SAVE_SKIP] {instId} open signal already exists for this symbol")
                    
                        else:
                            save_signal(sig)
                            print(f"[SAVE_OK] {instId} saved")

            

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

                    # допуск сильных сигналов
                    if (not entry_ok) and score >= 7:
                        if sig.get("entry") in ["🟡 AGGRESSIVE", "🟢 SAFE", "SAFE ENTRY"]:
                            entry_ok = True
                    
                    profit_ok = is_profitable(sig)
                    can_alert_now = should_alert_symbol(state, sig)
                    
                    start_ready = is_start_trigger(sig)
                    pre_ready = is_pre_trigger(sig)
                    confirm_ready = is_confirm_trigger(sig)
                    early_ready = is_early_pressure_alert(sig)
                    recent_start_lock = start_afterglow_recent(state, instId)
                    recent_early_lock = early_alert_recent(state, instId)
                    
                    if early_ready:
                        if sig.get("early_pressure_side") == "BUY":
                            early_buy_symbols.append(fmt_symbol(instId))
                        elif sig.get("early_pressure_side") == "SELL":
                            early_sell_symbols.append(fmt_symbol(instId))
                    
                    sent_main_now = False
                    sent_pre_now = False
                    sent_start_now = False
                    sent_early_now = False
                    
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
                    
                    score = float(sig.get("score", 0))
                    acc   = int(sig.get("acc_score", 0))
                    
                    if score >= 7:
                        print(
                            f"[CHECK] {instId} "
                            f"score={score} "
                            f"acc={acc} "
                            f"entry={sig.get('entry')} "
                            f"stage={sig.get('stage')} "
                            f"dir={sig.get('direction')} "
                            f"rsi={sig.get('rsi_state')} "
                            f"entry_ok={entry_ok} "
                            f"profit_ok={profit_ok} "
                            f"can_alert_now={can_alert_now} "
                            f"sent_main_now={sent_main_now}"
                        )
                    
                    summary_ok = (
                        score >= 5.5
                        or (acc >= 2 and score >= 4)
                        or sig.get("sendable", False)
                    )
                        
                    if summary_ok:
                        if not any(a.get("instId") == sig.get("instId") for a in alerts):
                            alerts.append(sig)
                            print(f"[SUMMARY_ADD] {instId} score={score} acc={acc}")
                    
                    # Для live логики строгие условия отдельно
                    live_ok = (
                        entry_ok
                        and profit_ok
                        and can_alert_now
                        and (not sent_main_now)
                    )
                    
                    if live_ok:
                        print(f"[LIVE_OK] {instId}")

                    
                    # =====================
                    # EARLY PRESSURE ALERT
                    # =====================
                    if (
                        (not sent_main_now)
                        and (not recent_safe_lock)
                        and (not recent_start_lock)
                        and (not recent_early_lock)
                        and (not start_ready)
                        and early_ready
                        and can_alert_now
                    ):
                        # send_telegram(msg_early_pressure(sig))
                        print(
                            f"[EARLY_SENT] {instId} "
                            f"side={sig.get('early_pressure_side')} "
                            f"score={sig.get('early_pressure_score')} "
                            f"stage={sig.get('stage')} "
                            f"entry={sig.get('entry')}"
                        )
                        early_count += 1
                        sent_early_now = True
                        mark_alert_sent(state, sig)
                        mark_early_alert(state, instId)

                    # =====================
                    # V3 TRIGGERS
                    # =====================

                    if (not recent_safe_lock) and start_ready and trigger_allowed(state, instId, "last_start_trigger_ts", TRIGGER_START_COOLDOWN):
                        # send_telegram(msg_start_trigger(sig))
                        start_count += 1
                        trigger_mark(state, instId, "last_start_trigger_ts")
                        sent_start_now = True
                
                    elif (not recent_safe_lock) and (not recent_start_lock) and pre_ready and trigger_allowed(state, instId, "last_pre_trigger_ts", TRIGGER_PRE_COOLDOWN):
                        # send_telegram(msg_pre_trigger(sig))
                        pre_count += 1
                        trigger_mark(state, instId, "last_pre_trigger_ts")
                        sent_pre_now = True
                    
                    if (
                        (not sent_main_now)
                        and confirm_ready
                        and entry_ok
                        and trigger_allowed(state, instId, "last_confirm_trigger_ts", TRIGGER_CONFIRM_COOLDOWN)
                    ):
                        # send_telegram(msg_confirm_trigger(sig))
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
                        and (not recent_start_lock)
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
            
            for s in alerts:
                s["rank"] = rank_signal(s)
                s["grade"] = confirm_grade(s)
            
            # добавляем SWING сигналы в общий список
            if "swing_candidates" in locals():
                for sw in swing_candidates:
                    if sw.get("sendable"):
                        sw["rank"] = rank_signal(sw)
                        sw["grade"] = confirm_grade(sw)
                        alerts.append(sw)
            
            alerts.sort(key=lambda s: s.get("rank", 0), reverse=True)
            manip_watch.sort(key=lambda s: s.get("acc_score", 0), reverse=True)
            
            swing_top = [
                s for s in alerts
                if str(s.get("status", "")).startswith("SWING")
                and str(s.get("grade", "")) != "WATCH"
            ][:3]
            
            sent_sw = set()
            ready_swings = []
            
            for sw in swing_top:
                sid = sw.get("instId")
            
                if sid in sent_sw:
                    continue
            
                text = msg_swing(sw)
            
                if text:
                    ready_swings.append((sid, text))
            
            # if ready_swings:
            #     send_telegram("🏆 SWING TOP SETUPS")
            #
            #     for sid, text in ready_swings:
            #         sent_sw.add(sid)
            #         send_telegram(text)
            
            
            cycle_info = time.strftime("%Y-%m-%d %H:%M:%S")

            print("ALERTS FOUND:", len(alerts))
            print(f"EARLY FOUND: {early_count} | START FOUND: {start_count} | PRE FOUND: {pre_count}")
            print(f"EARLY BUY SYMBOLS: {early_buy_symbols}")
            print(f"EARLY SELL SYMBOLS: {early_sell_symbols}")

            market_msg = msg_market_pressure(early_buy_symbols, early_sell_symbols)
            if market_msg:
                send_telegram(market_msg)

            # msg = summary_message(alerts, cycle_info, regime)
            # if msg and msg.strip() and should_send_summary(state, msg):
            #     send_telegram(msg)
                        
            
                top_alerts = [
                    s for s in alerts
                    if is_best_only_signal(s)
                ][:3]
                            
                sent_ids = set()
            
                for sig in top_alerts:
                    sid = sig.get("instId")
            
                    if sid in sent_ids:
                        continue
            
                    if sid in sent_sw:
                        continue
            
                    sent_ids.add(sid)
            
                    if can_send(sid, 3600):
                        send_telegram(choose_detail_message(sig))
            
            save_state(state)
                
        except Exception as e:
            err = traceback.format_exc()
            send_telegram(f"❌ Scan Error:\n{err}")
