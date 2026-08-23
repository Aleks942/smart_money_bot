"""
MARKET EXHAUSTION V2
====================

Диагностический движок истощения предыдущего движения.

Главная задача:
не искать вход,
не давать LONG/SHORT,
не менять score,
не блокировать сигнал.

Модуль отвечает только на вопрос:

    Предыдущее движение всё ещё развивается
    ИЛИ
    оно начинает терять способность двигать цену?

Логика цикла:

TREND
    ↓
EXHAUSTION
    ↓
STOPPING
    ↓
COMPRESSION / ABSORPTION
    ↓
ACCUMULATION
    ↓
POSITION BUILDUP
    ↓
PREMOVE
    ↓
EXPANSION

V2 использует только реальные OHLCV-свечи.

Поддерживаются форматы свечей:

1. dict:
    {
        "open": ...,
        "high": ...,
        "low": ...,
        "close": ...,
        "volume": ...
    }

2. OKX array:
    [
        ts,
        open,
        high,
        low,
        close,
        volume,
        ...
        confirm
    ]

Возвращаемые поля специально совпадают с тем,
что уже ожидает smart_money_cycle.py:

    previous_move
    price_stalling
    range_contracting
    volume_fading
    high_stop
    low_stop
    atr_fading
    compression_after_trend

Дополнительно:

    exhaustion
    exhaustion_score
    exhaustion_strength
    exhaustion_direction
    potential_reversal
    exhaustion_reasons
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


MARKET_EXHAUSTION_VERSION = "V2_DIAGNOSTIC"


# ============================================================
# CONFIG
# ============================================================

MIN_CANDLES = 18

# Сколько последних свечей считаем зоной возможной остановки
STOP_WINDOW = 5

# Сколько свечей до зоны остановки используем
# для определения предыдущего движения
TREND_WINDOW = 14

# Минимальный размер предыдущего движения в ATR.
# Если движение меньше — не называем его полноценным trend move.
MIN_TREND_ATR = 2.0

# Последние свечи должны проходить существенно меньше,
# чем нормальное движение.
STALL_ATR_FACTOR = 0.40

# Сжатие среднего candle range
RANGE_CONTRACTION_FACTOR = 0.80

# Затухание ATR
ATR_FADING_FACTOR = 0.82

# Затухание объёма
VOLUME_FADING_FACTOR = 0.85

# Допуск обновления high / low.
STOP_LEVEL_ATR_TOLERANCE = 0.20

# Минимальное число подтверждений Exhaustion.
MIN_EXHAUSTION_EVIDENCE = 3


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg(values: List[float]) -> float:
    clean = [float(v) for v in values if v is not None]

    if not clean:
        return 0.0

    return sum(clean) / len(clean)


def _neutral_result(reason: str = "") -> Dict[str, Any]:
    return {
        "market_exhaustion_version": MARKET_EXHAUSTION_VERSION,

        "previous_move": "UNKNOWN",

        "price_stalling": False,
        "range_contracting": False,
        "volume_fading": False,

        "high_stop": False,
        "low_stop": False,

        "atr_fading": False,
        "compression_after_trend": False,

        "exhaustion": False,
        "exhaustion_score": 0,
        "exhaustion_strength": "NONE",

        "exhaustion_direction": "UNKNOWN",
        "potential_reversal": "NEUTRAL",

        "exhaustion_reasons": [],
        "exhaustion_debug": {
            "error": reason,
        },
    }


# ============================================================
# CANDLE NORMALIZATION
# ============================================================

def _normalize_candle(raw: Any) -> Optional[Dict[str, float]]:
    """
    Приводим свечу к единому виду:

        ts
        open
        high
        low
        close
        volume
        confirm

    Поддерживаем dict и OKX list/tuple.
    """

    # --------------------------------------------------------
    # DICT FORMAT
    # --------------------------------------------------------

    if isinstance(raw, dict):

        open_price = _safe_float(
            raw.get("open", raw.get("o"))
        )

        high = _safe_float(
            raw.get("high", raw.get("h"))
        )

        low = _safe_float(
            raw.get("low", raw.get("l"))
        )

        close = _safe_float(
            raw.get("close", raw.get("c"))
        )

        volume = _safe_float(
            raw.get(
                "volume",
                raw.get(
                    "vol",
                    raw.get("v", 0.0)
                ),
            )
        )

        ts = _safe_float(
            raw.get(
                "timestamp",
                raw.get(
                    "ts",
                    raw.get("time", 0.0)
                ),
            )
        )

        confirm_raw = raw.get(
            "confirm",
            raw.get("closed")
        )

        confirm = None

        if confirm_raw is not None:
            confirm = str(confirm_raw)

        if high <= 0 or low <= 0 or close <= 0 or open_price <= 0:
            return None

        if high < low:
            return None

        return {
            "ts": ts,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "confirm": confirm,
        }

    # --------------------------------------------------------
    # OKX / ARRAY FORMAT
    #
    # [
    #   ts,
    #   open,
    #   high,
    #   low,
    #   close,
    #   volume,
    #   volCcy,
    #   volCcyQuote,
    #   confirm
    # ]
    # --------------------------------------------------------

    if isinstance(raw, (list, tuple)) and len(raw) >= 6:

        ts = _safe_float(raw[0])

        open_price = _safe_float(raw[1])
        high = _safe_float(raw[2])
        low = _safe_float(raw[3])
        close = _safe_float(raw[4])
        volume = _safe_float(raw[5])

        confirm = None

        if len(raw) >= 9:
            confirm = str(raw[8])

        if high <= 0 or low <= 0 or close <= 0 or open_price <= 0:
            return None

        if high < low:
            return None

        return {
            "ts": ts,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "confirm": confirm,
        }

    return None


def _normalize_candles(candles: Any) -> List[Dict[str, float]]:
    """
    Нормализация всего массива.

    Также умеем принять pandas DataFrame,
    если он случайно попадёт сюда из main.py.
    """

    if candles is None:
        return []

    # DataFrame-like object
    if hasattr(candles, "to_dict"):
        try:
            candles = candles.to_dict("records")
        except Exception:
            pass

    try:
        raw_list = list(candles)
    except Exception:
        return []

    normalized: List[Dict[str, float]] = []

    for raw in raw_list:

        candle = _normalize_candle(raw)

        if candle is None:
            continue

        # OKX confirm=0 означает незакрытую свечу.
        # Для Exhaustion V2 лучше использовать закрытые свечи.
        if candle.get("confirm") == "0":
            continue

        normalized.append(candle)

    if not normalized:
        return []

    # Если timestamp присутствует —
    # гарантируем порядок от старой свечи к новой.
    timestamps = [
        c["ts"]
        for c in normalized
        if c.get("ts", 0.0) > 0
    ]

    if len(timestamps) >= 2:
        normalized.sort(
            key=lambda x: x.get("ts", 0.0)
        )

    return normalized


# ============================================================
# TRUE RANGE / ATR
# ============================================================

def _true_ranges(
    candles: List[Dict[str, float]]
) -> List[float]:

    if not candles:
        return []

    result: List[float] = []

    previous_close = candles[0]["close"]

    for candle in candles:

        high = candle["high"]
        low = candle["low"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        result.append(tr)

        previous_close = candle["close"]

    return result


def _atr(
    candles: List[Dict[str, float]]
) -> float:

    return _avg(
        _true_ranges(candles)
    )


# ============================================================
# PREVIOUS MOVE
# ============================================================

def _detect_previous_move(
    candles: List[Dict[str, float]],
) -> Dict[str, Any]:
    """
    Определяем, существовало ли ДО последних STOP_WINDOW свечей
    реальное направленное движение.

    Важно:

    Мы НЕ используем direction текущего сигнала.

    previous_move отвечает только за то,
    что делала цена ДО возможной остановки.
    """

    if len(candles) < MIN_CANDLES:
        return {
            "direction": "UNKNOWN",
            "move": 0.0,
            "move_atr": 0.0,
            "efficiency": 0.0,
        }

    stop_window = min(
        STOP_WINDOW,
        max(3, len(candles) // 4),
    )

    trend_end = len(candles) - stop_window

    trend_start = max(
        0,
        trend_end - TREND_WINDOW,
    )

    trend = candles[
        trend_start:trend_end
    ]

    if len(trend) < 6:
        return {
            "direction": "UNKNOWN",
            "move": 0.0,
            "move_atr": 0.0,
            "efficiency": 0.0,
        }

    start_close = trend[0]["close"]
    end_close = trend[-1]["close"]

    signed_move = end_close - start_close
    absolute_move = abs(signed_move)

    trend_atr = _atr(trend)

    if trend_atr <= 0:
        return {
            "direction": "UNKNOWN",
            "move": signed_move,
            "move_atr": 0.0,
            "efficiency": 0.0,
        }

    move_atr = absolute_move / trend_atr

    closes = [
        c["close"]
        for c in trend
    ]

    traveled = 0.0

    for i in range(1, len(closes)):
        traveled += abs(
            closes[i] - closes[i - 1]
        )

    efficiency = (
        absolute_move / traveled
        if traveled > 0
        else 0.0
    )

    direction = "UNKNOWN"

    # Нам нужен не случайный микро-наклон,
    # а полноценное предыдущее движение.
    if move_atr >= MIN_TREND_ATR:

        if signed_move > 0:
            direction = "UP"

        elif signed_move < 0:
            direction = "DOWN"

    return {
        "direction": direction,
        "move": signed_move,
        "move_atr": round(move_atr, 3),
        "efficiency": round(efficiency, 3),
    }


# ============================================================
# PRICE STALLING
# ============================================================

def _detect_price_stalling(
    candles: List[Dict[str, float]],
    previous_move: str,
    base_atr: float,
) -> Dict[str, Any]:
    """
    Измеряем:

    продолжает ли цена реально продвигаться
    в направлении предыдущего движения?

    Если продвижение почти остановилось
    относительно ATR — price_stalling=True.
    """

    if previous_move not in {"UP", "DOWN"}:
        return {
            "value": False,
            "progress": 0.0,
        }

    if len(candles) < STOP_WINDOW + 2:
        return {
            "value": False,
            "progress": 0.0,
        }

    recent = candles[-STOP_WINDOW:]

    start_close = candles[
        -(STOP_WINDOW + 1)
    ]["close"]

    end_close = recent[-1]["close"]

    if previous_move == "UP":
        directional_progress = (
            end_close - start_close
        )

    else:
        directional_progress = (
            start_close - end_close
        )

    threshold = (
        base_atr * STALL_ATR_FACTOR
    )

    stalling = (
        directional_progress <= threshold
    )

    return {
        "value": bool(stalling),
        "progress": round(
            directional_progress,
            8,
        ),
        "threshold": round(
            threshold,
            8,
        ),
    }


# ============================================================
# HIGH / LOW STOP
# ============================================================

def _detect_level_stop(
    candles: List[Dict[str, float]],
    previous_move: str,
    base_atr: float,
) -> Dict[str, Any]:
    """
    Проверяем:

    UP:
        цена перестала нормально обновлять HIGH.

    DOWN:
        цена перестала нормально обновлять LOW.

    Это не означает разворот.
    Это означает потерю способности
    старого тренда продвигать цену дальше.
    """

    result = {
        "high_stop": False,
        "low_stop": False,
        "previous_level": 0.0,
        "recent_level": 0.0,
    }

    if previous_move not in {"UP", "DOWN"}:
        return result

    if len(candles) < 10:
        return result

    recent_count = 3

    recent = candles[
        -recent_count:
    ]

    reference = candles[
        -(recent_count + 6):-recent_count
    ]

    if len(reference) < 3:
        return result

    tolerance = (
        base_atr
        * STOP_LEVEL_ATR_TOLERANCE
    )

    # --------------------------------------------------------
    # PREVIOUS MOVE UP
    # --------------------------------------------------------

    if previous_move == "UP":

        previous_high = max(
            c["high"]
            for c in reference
        )

        recent_high = max(
            c["high"]
            for c in recent
        )

        high_stop = (
            recent_high
            <= previous_high + tolerance
        )

        result.update({
            "high_stop": bool(high_stop),
            "previous_level": previous_high,
            "recent_level": recent_high,
        })

    # --------------------------------------------------------
    # PREVIOUS MOVE DOWN
    # --------------------------------------------------------

    elif previous_move == "DOWN":

        previous_low = min(
            c["low"]
            for c in reference
        )

        recent_low = min(
            c["low"]
            for c in recent
        )

        low_stop = (
            recent_low
            >= previous_low - tolerance
        )

        result.update({
            "low_stop": bool(low_stop),
            "previous_level": previous_low,
            "recent_level": recent_low,
        })

    return result


# ============================================================
# RANGE CONTRACTION
# ============================================================

def _detect_range_contraction(
    candles: List[Dict[str, float]],
) -> Dict[str, Any]:

    if len(candles) < 12:
        return {
            "value": False,
            "recent_range": 0.0,
            "previous_range": 0.0,
            "ratio": 0.0,
        }

    recent = candles[-4:]
    previous = candles[-12:-4]

    recent_range = _avg([
        c["high"] - c["low"]
        for c in recent
    ])

    previous_range = _avg([
        c["high"] - c["low"]
        for c in previous
    ])

    if previous_range <= 0:
        return {
            "value": False,
            "recent_range": recent_range,
            "previous_range": previous_range,
            "ratio": 0.0,
        }

    ratio = (
        recent_range / previous_range
    )

    contracted = (
        ratio <= RANGE_CONTRACTION_FACTOR
    )

    return {
        "value": bool(contracted),
        "recent_range": round(
            recent_range,
            8,
        ),
        "previous_range": round(
            previous_range,
            8,
        ),
        "ratio": round(
            ratio,
            3,
        ),
    }


# ============================================================
# ATR FADING
# ============================================================

def _detect_atr_fading(
    candles: List[Dict[str, float]],
) -> Dict[str, Any]:

    if len(candles) < 14:
        return {
            "value": False,
            "recent_atr": 0.0,
            "previous_atr": 0.0,
            "ratio": 0.0,
        }

    recent = candles[-4:]
    previous = candles[-14:-4]

    recent_atr = _atr(recent)
    previous_atr = _atr(previous)

    if previous_atr <= 0:
        return {
            "value": False,
            "recent_atr": recent_atr,
            "previous_atr": previous_atr,
            "ratio": 0.0,
        }

    ratio = (
        recent_atr / previous_atr
    )

    fading = (
        ratio <= ATR_FADING_FACTOR
    )

    return {
        "value": bool(fading),
        "recent_atr": round(
            recent_atr,
            8,
        ),
        "previous_atr": round(
            previous_atr,
            8,
        ),
        "ratio": round(
            ratio,
            3,
        ),
    }


# ============================================================
# VOLUME FADING
# ============================================================

def _detect_volume_fading(
    candles: List[Dict[str, float]],
) -> Dict[str, Any]:

    if len(candles) < 12:
        return {
            "value": False,
            "recent_volume": 0.0,
            "previous_volume": 0.0,
            "ratio": 0.0,
            "available": False,
        }

    recent = candles[-4:]
    previous = candles[-12:-4]

    recent_volumes = [
        c["volume"]
        for c in recent
        if c["volume"] > 0
    ]

    previous_volumes = [
        c["volume"]
        for c in previous
        if c["volume"] > 0
    ]

    if (
        not recent_volumes
        or not previous_volumes
    ):
        return {
            "value": False,
            "recent_volume": 0.0,
            "previous_volume": 0.0,
            "ratio": 0.0,
            "available": False,
        }

    recent_volume = _avg(
        recent_volumes
    )

    previous_volume = _avg(
        previous_volumes
    )

    if previous_volume <= 0:
        return {
            "value": False,
            "recent_volume": recent_volume,
            "previous_volume": previous_volume,
            "ratio": 0.0,
            "available": False,
        }

    ratio = (
        recent_volume / previous_volume
    )

    fading = (
        ratio <= VOLUME_FADING_FACTOR
    )

    return {
        "value": bool(fading),
        "recent_volume": round(
            recent_volume,
            3,
        ),
        "previous_volume": round(
            previous_volume,
            3,
        ),
        "ratio": round(
            ratio,
            3,
        ),
        "available": True,
    }


# ============================================================
# MAIN ENGINE
# ============================================================

def detect_market_exhaustion_v2(
    candles: Any,
    symbol: str = "",
    emit_log: bool = False,
) -> Dict[str, Any]:
    """
    Главная функция Market Exhaustion V2.

    На следующем этапе её можно будет подключить так:

        exhaustion_v2 = detect_market_exhaustion_v2(
            c5,
            symbol=symbol,
        )

        signal.update(exhaustion_v2)

    Но ПОКА модуль диагностический,
    поэтому main.py пока не трогаем.
    """

    try:

        normalized = _normalize_candles(
            candles
        )

        # ----------------------------------------------------
        # MINIMUM DATA
        # ----------------------------------------------------

        if len(normalized) < MIN_CANDLES:

            result = _neutral_result(
                f"NOT_ENOUGH_CANDLES:{len(normalized)}"
            )

            if emit_log:
                print(
                    f"[MARKET_EXHAUSTION_V2] "
                    f"{symbol or 'UNKNOWN'} "
                    f"status=NOT_ENOUGH_CANDLES "
                    f"candles={len(normalized)}",
                    flush=True,
                )

            return result

        # ----------------------------------------------------
        # PREVIOUS MOVE
        # ----------------------------------------------------

        previous = _detect_previous_move(
            normalized
        )

        previous_move = previous[
            "direction"
        ]

        # Базовый ATR для нормализации.
        base_window = normalized[-14:]

        base_atr = _atr(
            base_window
        )

        # ----------------------------------------------------
        # PRICE STALLING
        # ----------------------------------------------------

        stalling_data = (
            _detect_price_stalling(
                normalized,
                previous_move,
                base_atr,
            )
        )

        price_stalling = bool(
            stalling_data["value"]
        )

        # ----------------------------------------------------
        # HIGH / LOW STOP
        # ----------------------------------------------------

        stop_data = _detect_level_stop(
            normalized,
            previous_move,
            base_atr,
        )

        high_stop = bool(
            stop_data["high_stop"]
        )

        low_stop = bool(
            stop_data["low_stop"]
        )

        # ----------------------------------------------------
        # RANGE CONTRACTION
        # ----------------------------------------------------

        range_data = (
            _detect_range_contraction(
                normalized
            )
        )

        range_contracting = bool(
            range_data["value"]
        )

        # ----------------------------------------------------
        # ATR FADING
        # ----------------------------------------------------

        atr_data = (
            _detect_atr_fading(
                normalized
            )
        )

        atr_fading = bool(
            atr_data["value"]
        )

        # ----------------------------------------------------
        # VOLUME FADING
        # ----------------------------------------------------

        volume_data = (
            _detect_volume_fading(
                normalized
            )
        )

        volume_fading = bool(
            volume_data["value"]
        )

        # ----------------------------------------------------
        # DIRECTIONAL STOP
        # ----------------------------------------------------

        directional_stop = bool(
            (
                previous_move == "UP"
                and high_stop
            )
            or
            (
                previous_move == "DOWN"
                and low_stop
            )
        )

        # ----------------------------------------------------
        # COMPRESSION AFTER TREND
        # ----------------------------------------------------

        compression_after_trend = bool(
            previous_move in {"UP", "DOWN"}
            and range_contracting
            and (
                price_stalling
                or directional_stop
                or atr_fading
            )
        )

        # ----------------------------------------------------
        # EVIDENCE
        # ----------------------------------------------------

        evidence_flags = {
            "price_stalling": price_stalling,
            "range_contracting": range_contracting,
            "volume_fading": volume_fading,
            "high_stop": high_stop,
            "low_stop": low_stop,
            "atr_fading": atr_fading,
            "compression_after_trend": (
                compression_after_trend
            ),
        }

        evidence = sum(
            1
            for value
            in evidence_flags.values()
            if value
        )

        reasons = [
            name.upper()
            for name, value
            in evidence_flags.items()
            if value
        ]

        # ----------------------------------------------------
        # EXHAUSTION
        #
        # Не достаточно просто набрать 3 случайных признака.
        #
        # Обязательно:
        #
        # 1. Должно существовать предыдущее движение.
        # 2. Цена должна переставать продвигаться
        #    ИЛИ перестать обновлять directional extreme.
        # 3. Должно появиться хотя бы одно подтверждение
        #    сокращения volatility/range.
        # ----------------------------------------------------

        progress_loss = bool(
            price_stalling
            or directional_stop
        )

        volatility_loss = bool(
            range_contracting
            or atr_fading
            or compression_after_trend
        )

        exhaustion = bool(
            previous_move in {"UP", "DOWN"}
            and evidence >= MIN_EXHAUSTION_EVIDENCE
            and progress_loss
            and volatility_loss
        )

        # ----------------------------------------------------
        # STRENGTH
        # ----------------------------------------------------

        if not exhaustion:
            exhaustion_strength = "NONE"

        elif evidence >= 5:
            exhaustion_strength = "STRONG"

        elif evidence >= 4:
            exhaustion_strength = "CONFIRMED"

        else:
            exhaustion_strength = "EARLY"

        # ----------------------------------------------------
        # POTENTIAL REVERSAL
        #
        # Это НЕ сигнал!
        #
        # Это только направление,
        # которое потенциально выигрывает,
        # если старое движение действительно остановится.
        # ----------------------------------------------------

        potential_reversal = "NEUTRAL"

        if exhaustion:

            if previous_move == "UP":
                potential_reversal = "SHORT"

            elif previous_move == "DOWN":
                potential_reversal = "LONG"

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        result = {
            "market_exhaustion_version": (
                MARKET_EXHAUSTION_VERSION
            ),

            "previous_move": previous_move,

            "price_stalling": price_stalling,
            "range_contracting": range_contracting,
            "volume_fading": volume_fading,

            "high_stop": high_stop,
            "low_stop": low_stop,

            "atr_fading": atr_fading,

            "compression_after_trend": (
                compression_after_trend
            ),

            "exhaustion": exhaustion,
            "exhaustion_score": evidence,
            "exhaustion_strength": (
                exhaustion_strength
            ),

            "exhaustion_direction": (
                previous_move
            ),

            "potential_reversal": (
                potential_reversal
            ),

            "exhaustion_reasons": reasons,

            "exhaustion_debug": {
                "candles": len(normalized),

                "base_atr": round(
                    base_atr,
                    8,
                ),

                "previous_move_atr": (
                    previous["move_atr"]
                ),

                "previous_move_efficiency": (
                    previous["efficiency"]
                ),

                "stall_progress": (
                    stalling_data.get(
                        "progress",
                        0.0,
                    )
                ),

                "stall_threshold": (
                    stalling_data.get(
                        "threshold",
                        0.0,
                    )
                ),

                "range_ratio": (
                    range_data.get(
                        "ratio",
                        0.0,
                    )
                ),

                "atr_ratio": (
                    atr_data.get(
                        "ratio",
                        0.0,
                    )
                ),

                "volume_ratio": (
                    volume_data.get(
                        "ratio",
                        0.0,
                    )
                ),

                "volume_available": (
                    volume_data.get(
                        "available",
                        False,
                    )
                ),

                "previous_level": (
                    stop_data.get(
                        "previous_level",
                        0.0,
                    )
                ),

                "recent_level": (
                    stop_data.get(
                        "recent_level",
                        0.0,
                    )
                ),

                "evidence": evidence,

                "progress_loss": (
                    progress_loss
                ),

                "volatility_loss": (
                    volatility_loss
                ),
            },
        }

        # ----------------------------------------------------
        # DIAGNOSTIC LOG
        # ----------------------------------------------------

        if emit_log:

            print(
                f"[MARKET_EXHAUSTION_V2] "
                f"{symbol or 'UNKNOWN'} "
                f"prev={previous_move} "
                f"move_atr={previous['move_atr']} "
                f"stall={price_stalling} "
                f"range_contract={range_contracting} "
                f"volume_fade={volume_fading} "
                f"high_stop={high_stop} "
                f"low_stop={low_stop} "
                f"atr_fade={atr_fading} "
                f"compression_after_trend="
                f"{compression_after_trend} "
                f"evidence={evidence} "
                f"exhaustion={exhaustion} "
                f"strength={exhaustion_strength} "
                f"potential={potential_reversal}",
                flush=True,
            )

        return result

    # ========================================================
    # SAFETY
    #
    # Диагностический модуль никогда не должен
    # уронить основной бот.
    # ========================================================

    except Exception as exc:

        result = _neutral_result(
            f"{type(exc).__name__}: {exc}"
        )

        if emit_log:
            print(
                f"[MARKET_EXHAUSTION_V2_ERROR] "
                f"{symbol or 'UNKNOWN'} "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

        return result


# ============================================================
# PUBLIC EXPORT
# ============================================================

__all__ = [
    "detect_market_exhaustion_v2",
    "MARKET_EXHAUSTION_VERSION",
]
