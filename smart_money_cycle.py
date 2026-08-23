SMART_CYCLE_VERSION = "V1_DIAGNOSTIC"

ACC_READY_MIN = 3.0
EP_READY_MIN = 7.0


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    if isinstance(value, (int, float)):
        return value != 0

    text = str(value).strip().upper()

    return text in {
        "1",
        "TRUE",
        "YES",
        "ON",
        "ACTIVE",
        "CONFIRMED",
    }


def _normalize_direction(signal):
    """
    Определяем направление будущего движения.

    Приоритет:
    1. Реальный ENTRY, который уже выбрал основной движок.
    2. Набор directional flags.
    3. CVD.
    4. Старые direction_code / direction / side только как fallback.
    """

    # ====================================================
    # 1. ENTRY — самый прямой источник направления
    # ====================================================

    entry = str(
        signal.get("entry")
        or signal.get("entry_type")
        or ""
    ).upper()

    if "SHORT" in entry or "SELL" in entry:
        return "SHORT"

    if "LONG" in entry or "BUY" in entry:
        return "LONG"

    # ====================================================
    # 2. DIRECTIONAL FLAGS
    # ====================================================

    flags = set(signal.get("flags") or [])

    long_flags = {
        "PRESSURE_UP",
        "STRUCTURE_HH_HL",
        "EMA_BULL",
        "EMA_BULL_STRONG",
        "BREAKOUT_UP",
        "BREAKOUT_CONFIRM_UP",
        "ACCELERATION_UP",
        "BULLISH_SHIFT",
        "MTF_LONG_ALIGN",
        "BOS_UP",
    }

    short_flags = {
        "PRESSURE_DOWN",
        "STRUCTURE_LH_LL",
        "EMA_BEAR",
        "EMA_BEAR_STRONG",
        "BREAKOUT_DOWN",
        "BREAKOUT_CONFIRM_DOWN",
        "ACCELERATION_DOWN",
        "BEARISH_SHIFT",
        "MTF_SHORT_ALIGN",
        "BOS_DOWN",
    }

    long_score = sum(
        1 for flag in long_flags
        if flag in flags
    )

    short_score = sum(
        1 for flag in short_flags
        if flag in flags
    )

    if long_score > short_score:
        return "LONG"

    if short_score > long_score:
        return "SHORT"

    # ====================================================
    # 3. CVD — fallback при равенстве структуры
    # ====================================================

    cvd_state = str(
        signal.get("cvd_state")
        or ""
    ).upper()

    if cvd_state in {
        "BUY_CVD",
        "STRONG_BUY_CVD",
    }:
        return "LONG"

    if cvd_state in {
        "SELL_CVD",
        "STRONG_SELL_CVD",
    }:
        return "SHORT"

    # ====================================================
    # 4. Старые direction fields — только последний fallback
    # ====================================================

    raw = str(
        signal.get("direction_code")
        or signal.get("direction")
        or signal.get("side")
        or ""
    ).upper()

    if (
        "LONG" in raw
        or "UP" in raw
        or "BUY" in raw
        or "ВВЕРХ" in raw
    ):
        return "LONG"

    if (
        "SHORT" in raw
        or "DOWN" in raw
        or "SELL" in raw
        or "ВНИЗ" in raw
    ):
        return "SHORT"

    return "NEUTRAL"


def _infer_previous_move(flags, signal):
    """
    Это пока НЕ новый Market Exhaustion V2.
    Здесь только диагностический fallback по уже существующим данным.
    """

    # Сначала используем наиболее прямые признаки истощения.
    if (
        "LONG_EXHAUSTION" in flags
        or "IMPULSE_EXHAUSTION_LONG" in flags
    ):
        return "UP"

    if (
        "SHORT_EXHAUSTION" in flags
        or "IMPULSE_EXHAUSTION_SHORT" in flags
    ):
        return "DOWN"

    # Если позже Market Exhaustion V2 начнет сохранять previous_move
    # прямо в signal — модуль автоматически его подхватит.
    explicit = str(
        signal.get("previous_move")
        or signal.get("market_previous_move")
        or ""
    ).upper()

    if explicit in ("UP", "LONG", "BUY", "BULL"):
        return "UP"

    if explicit in ("DOWN", "SHORT", "SELL", "BEAR"):
        return "DOWN"

    # Fallback по структуре / EMA.
    bull = (
        "STRUCTURE_HH_HL" in flags
        or "EMA_BULL" in flags
        or "EMA_BULL_STRONG" in flags
        or "MTF_LONG_ALIGN" in flags
    )

    bear = (
        "STRUCTURE_LH_LL" in flags
        or "EMA_BEAR" in flags
        or "EMA_BEAR_STRONG" in flags
        or "MTF_SHORT_ALIGN" in flags
    )

    if bull and not bear:
        return "UP"

    if bear and not bull:
        return "DOWN"

    return "UNKNOWN"


def _oi_direction(state):
    state = str(state or "").upper()

    if state == "NEW_LONGS":
        return "LONG"

    if state == "NEW_SHORTS":
        return "SHORT"

    if state == "SHORT_COVERING":
        return "LONG_WEAK"

    if state == "LONG_EXIT":
        return "SHORT_WEAK"

    return "NEUTRAL"


def detect_smart_money_cycle(signal, emit_log=True):
    """
    SMART MONEY CYCLE V1.

    Диагностический движок.
    Принимает готовый signal после FLOW + OI + SMART MONEY + CVD +
    LATE MOVE и возвращает signal с дополнительными smart_cycle_* полями.

    Ничего в рабочем скоринге/фильтрах не меняет.
    """

    # Не ломаем pipeline, если вдруг пришел некорректный объект.
    if not isinstance(signal, dict):
        return signal

    try:
        # Работаем с shallow-copy.
        # Вложенные структуры не изменяем.
        result = dict(signal)

        flags = set(signal.get("flags") or [])

        symbol = (
            signal.get("instId")
            or signal.get("symbol")
            or "UNKNOWN"
        )

        direction = _normalize_direction(signal)
        previous_move = _infer_previous_move(flags, signal)

        score = _safe_float(signal.get("score"))
        acc = _safe_float(signal.get("acc_score"))
        ep = _safe_float(signal.get("early_pressure_score"))

        flow_score = _safe_float(signal.get("flow_score"))
        flow_total = _safe_float(signal.get("flow_total_score"))
        capital_flow_score = _safe_float(
            signal.get("capital_flow_score")
        )

        # Старый отдельный Capital Flow Engine.
        legacy_capital_score = _safe_float(
            signal.get("capital_score")
        )

        flow_state = str(
            signal.get("flow_state")
            or "WEAK_OR_NO_FLOW"
        ).upper()

        capital_state = str(
            signal.get("capital_state")
            or "UNKNOWN"
        ).upper()

        oi_state = str(
            signal.get("oi_state")
            or "NEUTRAL"
        ).upper()

        real_oi_state = str(
            signal.get("real_oi_state")
            or "NO_OI_HISTORY"
        ).upper()

        cvd_state = str(
            signal.get("cvd_state")
            or "NEUTRAL"
        ).upper()

        stage_old = str(
            signal.get("stage")
            or ""
        ).upper()

        setup_class = str(
            signal.get("setup_class")
            or ""
        ).upper()

        late_move_penalty = _safe_float(
            signal.get("late_move_penalty")
        )

        late_entry = _safe_bool(
            signal.get("late_entry")
        )

        reasons = []

        # ====================================================
        # 1. COMPRESSION
        # ====================================================

        compression_flags = {
            "COMP_PRO_5M",
            "COMP_PRO_15M",
            "COMP_5M",
            "COMP_15M",
            "RANGE_COMPRESSION",
            "TIGHT_RANGE",
            "FLOW_COMPRESSION",
            "FLOW_BUILDUP",
        }

        compression = any(
            flag in flags
            for flag in compression_flags
        )

        # Если range detector уже сохранил bool.
        if _safe_bool(signal.get("range_compression")):
            compression = True

        if compression:
            reasons.append("COMPRESSION")

        # ====================================================
        # 2. ABSORPTION — СТРОГО ПО НАПРАВЛЕНИЮ
        # ====================================================

        buyer_absorption = (
            "BUYER_ABSORPTION" in flags
        )

        seller_absorption = (
            "SELLER_ABSORPTION" in flags
        )

        if direction == "LONG":
            absorption = buyer_absorption

        elif direction == "SHORT":
            absorption = seller_absorption

        else:
            absorption = False

        if absorption:
            reasons.append(
                f"{direction}_ABSORPTION"
            )

        # ====================================================
        # 3. PRESSURE / PRESSURE SHIFT
        # ====================================================

        if direction == "LONG":
            pressure_aligned = any(
                flag in flags
                for flag in {
                    "PRESSURE_UP",
                    "PRESSURE_LONG_PERSIST_2",
                    "PRESSURE_LONG_PERSIST_3",
                    "BULLISH_SHIFT",
                    "EARLY_IMBALANCE_UP",
                }
            )

            pressure_shift = (
                "BULLISH_SHIFT" in flags
            )

        elif direction == "SHORT":
            pressure_aligned = any(
                flag in flags
                for flag in {
                    "PRESSURE_DOWN",
                    "PRESSURE_SHORT_PERSIST_2",
                    "PRESSURE_SHORT_PERSIST_3",
                    "BEARISH_SHIFT",
                    "EARLY_IMBALANCE_DOWN",
                }
            )

            pressure_shift = (
                "BEARISH_SHIFT" in flags
            )

        else:
            pressure_aligned = False
            pressure_shift = False

        if pressure_shift:
            reasons.append("PRESSURE_SHIFT")

        elif pressure_aligned:
            reasons.append("PRESSURE_ALIGNED")

        # ====================================================
        # 4. EXHAUSTION
        #
        # Пока используем:
        # - существующие exhaustion flags;
        # - будущие поля Market Exhaustion V2, если они появятся.
        # ====================================================

        explicit_price_stalling = _safe_bool(
            signal.get("price_stalling")
        )

        explicit_range_contracting = _safe_bool(
            signal.get("range_contracting")
        )

        explicit_volume_fading = _safe_bool(
            signal.get("volume_fading")
        )

        explicit_high_stop = _safe_bool(
            signal.get("high_stop")
        )

        explicit_low_stop = _safe_bool(
            signal.get("low_stop")
        )

        explicit_atr_fading = _safe_bool(
            signal.get("atr_fading")
        )

        explicit_compression_after_trend = _safe_bool(
            signal.get("compression_after_trend")
        )

        old_exhaustion = (
            "LONG_EXHAUSTION" in flags
            or "SHORT_EXHAUSTION" in flags
            or "IMPULSE_EXHAUSTION_LONG" in flags
            or "IMPULSE_EXHAUSTION_SHORT" in flags
        )

        exhaustion_evidence = sum([
            explicit_price_stalling,
            explicit_range_contracting,
            explicit_volume_fading,
            explicit_high_stop,
            explicit_low_stop,
            explicit_atr_fading,
            explicit_compression_after_trend,
        ])

        # ====================================================
        # MARKET EXHAUSTION SOURCE
        #
        # Если подключен Market Exhaustion V2 —
        # доверяем его финальному решению.
        #
        # V2 дополнительно проверяет наличие реального
        # предыдущего движения и не позволяет простому
        # сжатию рынка стать EXHAUSTION.
        #
        # Старую эвристику оставляем только как fallback.
        # ====================================================
        
        v2_exhaustion_available = (
            str(
                signal.get("market_exhaustion_version")
                or ""
            ).upper().startswith("V2")
        )
        
        v2_exhaustion = _safe_bool(
            signal.get("exhaustion")
        )
        
        if v2_exhaustion_available:
            exhaustion = v2_exhaustion
        else:
            exhaustion = bool(
                old_exhaustion
                or exhaustion_evidence >= 3
            )

        if exhaustion:
            reasons.append("EXHAUSTION")

        # ====================================================
        # EXHAUSTION DIAGNOSTIC
        # ====================================================
        
        exhaustion_debug = {
            "price_stalling": explicit_price_stalling,
            "range_contracting": explicit_range_contracting,
            "volume_fading": explicit_volume_fading,
            "high_stop": explicit_high_stop,
            "low_stop": explicit_low_stop,
            "atr_fading": explicit_atr_fading,
            "compression_after_trend": explicit_compression_after_trend,
            "old_exhaustion": old_exhaustion,
            "evidence": exhaustion_evidence,
        }

        # ====================================================
        # 5. STOPPING
        #
        # Приоритет:
        # 1) будущие явные признаки V2;
        # 2) безопасный proxy из уже существующих данных.
        # ====================================================

        if previous_move == "UP":
            structural_stop = explicit_high_stop

        elif previous_move == "DOWN":
            structural_stop = explicit_low_stop

        else:
            structural_stop = (
                explicit_high_stop
                or explicit_low_stop
            )

        explicit_stopping = (
            explicit_price_stalling
            and (
                structural_stop
                or explicit_range_contracting
                or explicit_atr_fading
            )
        )

        # Временный proxy до полноценного MARKET_EXHAUSTION V2.
        proxy_stopping = (
            exhaustion
            and compression
        )

        stopping = bool(
            exhaustion
            and (
                explicit_stopping
                or proxy_stopping
            )
        )

        stopping_source = "NONE"

        if explicit_stopping:
            stopping_source = "EXPLICIT_V2_DATA"

        elif proxy_stopping:
            stopping_source = "EXHAUSTION_PLUS_COMPRESSION_PROXY"

        if stopping:
            reasons.append(
                f"STOPPING:{stopping_source}"
            )

        # ====================================================
        # 6. ACC / EP READY
        # ====================================================

        acc_ready = acc >= ACC_READY_MIN
        ep_ready = ep >= EP_READY_MIN

        if acc_ready:
            reasons.append("ACC_READY")

        if ep_ready:
            reasons.append("EP_READY")

        # ====================================================
        # 7. OI
        #
        # Не считаем:
        # LONG + SHORT_COVERING настоящим NEW LONG.
        # SHORT + LONG_EXIT настоящим NEW SHORT.
        # ====================================================

        expected_oi = None
        weak_oi = None

        if direction == "LONG":
            expected_oi = "NEW_LONGS"
            weak_oi = "SHORT_COVERING"

        elif direction == "SHORT":
            expected_oi = "NEW_SHORTS"
            weak_oi = "LONG_EXIT"

        oi_primary_confirmed = (
            expected_oi is not None
            and oi_state == expected_oi
        )

        oi_real_confirmed = (
            expected_oi is not None
            and real_oi_state == expected_oi
        )

        oi_primary_weak = (
            weak_oi is not None
            and oi_state == weak_oi
        )

        oi_real_weak = (
            weak_oi is not None
            and real_oi_state == weak_oi
        )

        primary_direction = _oi_direction(oi_state)
        real_direction = _oi_direction(real_oi_state)

        meaningful_primary = primary_direction != "NEUTRAL"
        meaningful_real = real_direction != "NEUTRAL"

        oi_conflict = bool(
            meaningful_primary
            and meaningful_real
            and primary_direction != real_direction
        )

        # Два OI-классификатора работают на немного разных срезах.
        # Поэтому подтверждение принимаем от любого из них,
        # НО не принимаем при явном конфликте.
        oi_confirmed = bool(
            (oi_primary_confirmed or oi_real_confirmed)
            and not oi_conflict
        )

        oi_weak = bool(
            oi_primary_weak
            or oi_real_weak
        )

        if oi_confirmed:
            reasons.append(
                f"OI_CONFIRMED:{expected_oi}"
            )

        elif oi_weak:
            reasons.append(
                f"OI_WEAK_CLOSE_ONLY:{weak_oi}"
            )

        if oi_conflict:
            reasons.append(
                f"OI_CONFLICT:{oi_state}/{real_oi_state}"
            )

        # ====================================================
        # 8. CAPITAL FLOW
        #
        # В текущем main.py есть два источника:
        #
        # A) capital_flow_score из analyze_flow_snapshot()
        # B) capital_score/capital_state из отдельного
        #    analyze_capital_flow()
        #
        # В V1 ничего не исправляем. Только читаем оба.
        # ====================================================

        snapshot_capital_confirmed = (
            capital_flow_score >= 1
        )

        legacy_capital_confirmed = (
            capital_state in {
                "BUILDING_CAPITAL_FLOW",
                "STRONG_CAPITAL_FLOW",
            }
            or legacy_capital_score >= 4
        )

        capital_confirmed = bool(
            snapshot_capital_confirmed
            or legacy_capital_confirmed
        )

        if snapshot_capital_confirmed:
            capital_source = "FLOW_SNAPSHOT"

        elif legacy_capital_confirmed:
            capital_source = "LEGACY_CAPITAL_ENGINE"

        else:
            capital_source = "NONE"

        if capital_confirmed:
            reasons.append(
                f"CAPITAL_CONFIRMED:{capital_source}"
            )
        else:
            reasons.append("NO_CAPITAL_CONFIRM")

        # Отдельно подсвечиваем текущую архитектурную проблему:
        # FLOW может показывать ранний pressure-flow, но его
        # capital_flow_score может оставаться 0.
        flow_capital_gap = bool(
            flow_score >= 2
            and capital_flow_score <= 0
            and legacy_capital_confirmed
        )

        if flow_capital_gap:
            reasons.append("FLOW_CAPITAL_GAP")

        # ====================================================
        # 9. FLOW
        #
        # Сильный pressure сам по себе НЕ равен real money.
        # ====================================================

        flow_state_confirmed = flow_state in {
            "BUILDING_MONEY_FLOW",
            "STRONG_MONEY_FLOW",
        }

        flow_early = (
            flow_state == "EARLY_MONEY_FLOW"
        )

        # Fallback для диагностики текущей архитектуры:
        # есть pressure-flow + независимое подтверждение капитала.
        reconstructed_flow_confirmed = bool(
            flow_score >= 2
            and capital_confirmed
        )

        flow_confirmed = bool(
            flow_state_confirmed
            or reconstructed_flow_confirmed
        )

        if flow_confirmed:
            if flow_state_confirmed:
                reasons.append(
                    f"FLOW_CONFIRMED:{flow_state}"
                )
            else:
                reasons.append(
                    "FLOW_CONFIRMED:PRESSURE_PLUS_CAPITAL"
                )

        elif flow_early:
            reasons.append("EARLY_FLOW_ONLY")

        else:
            reasons.append("WEAK_OR_NO_FLOW")

        # ====================================================
        # 10. CVD
        # ====================================================

        if direction == "LONG":
            cvd_confirmed = cvd_state in {
                "BUY_CVD",
                "STRONG_BUY_CVD",
            }

            cvd_opposite = cvd_state in {
                "SELL_CVD",
                "STRONG_SELL_CVD",
            }

        elif direction == "SHORT":
            cvd_confirmed = cvd_state in {
                "SELL_CVD",
                "STRONG_SELL_CVD",
            }

            cvd_opposite = cvd_state in {
                "BUY_CVD",
                "STRONG_BUY_CVD",
            }

        else:
            cvd_confirmed = False
            cvd_opposite = False

        if cvd_confirmed:
            reasons.append(
                f"CVD_CONFIRMED:{cvd_state}"
            )

        elif cvd_opposite:
            reasons.append(
                f"CVD_OPPOSITE:{cvd_state}"
            )

        # ====================================================
        # 11. LATE MOVE
        # ====================================================

        late_move = bool(
            late_move_penalty > 0
            or late_entry
            or setup_class == "LATE_TREND"
        )

        if late_move:
            reasons.append(
                f"LATE_MOVE:penalty={late_move_penalty}"
            )

        # ====================================================
        # 12. ACCUMULATION
        #
        # Строгая логика:
        # stopping + compression + directional absorption.
        #
        # Старый stage не используем как источник истины,
        # но сохраняем отдельно для сравнения.
        # ====================================================

        accumulation = bool(
            stopping
            and compression
            and absorption
        )

        raw_accumulation_evidence = bool(
            compression
            and absorption
        )

        if accumulation:
            reasons.append("ACCUMULATION_CONFIRMED")

        elif raw_accumulation_evidence:
            reasons.append(
                "ACCUMULATION_EVIDENCE_WAIT_STOPPING"
            )

        # ====================================================
        # 13. POSITION BUILDUP
        #
        # КЛЮЧ:
        # accumulation + NEW_LONGS/NEW_SHORTS.
        # Закрытие старых позиций сюда НЕ проходит.
        # ====================================================

        position_buildup = bool(
            accumulation
            and oi_confirmed
            and not oi_weak
            and not oi_conflict
            and not late_move
        )

        if position_buildup:
            reasons.append("POSITION_BUILDUP_CONFIRMED")

        # ====================================================
        # 14. PREMOVE
        #
        # Уже должны быть:
        # ACC + EP + OI + FLOW + CVD + pressure.
        # ====================================================

        premove = bool(
            position_buildup
            and acc_ready
            and ep_ready
            and pressure_aligned
            and flow_confirmed
            and cvd_confirmed
            and not late_move
        )

        if premove:
            reasons.append("PREMOVE_CONFIRMED")

        # ====================================================
        # 15. EXPANSION
        # ====================================================

        if direction == "LONG":
            acceleration = (
                "ACCELERATION_UP" in flags
            )

            breakout_confirmed = (
                "BREAKOUT_CONFIRM_UP" in flags
                or "BREAKOUT_UP" in flags
            )

            launch = any(
                flag in flags
                for flag in {
                    "EARLY_LAUNCH_UP",
                    "LAUNCH_READY_UP",
                    "LAUNCH_PROXIMITY_UP",
                    "EXPLOSION_READY_UP",
                    "EXPLOSIVE_MOVE_UP",
                }
            )

        elif direction == "SHORT":
            acceleration = (
                "ACCELERATION_DOWN" in flags
            )

            breakout_confirmed = (
                "BREAKOUT_CONFIRM_DOWN" in flags
                or "BREAKOUT_DOWN" in flags
            )

            launch = any(
                flag in flags
                for flag in {
                    "EARLY_LAUNCH_DOWN",
                    "LAUNCH_READY_DOWN",
                    "LAUNCH_PROXIMITY_DOWN",
                    "EXPLOSION_READY_DOWN",
                    "EXPLOSIVE_MOVE_DOWN",
                }
            )

        else:
            acceleration = False
            breakout_confirmed = False
            launch = False

        # Старый stage/setup_class сохраняем только для сравнения.
        # Он НЕ имеет права автоматически назначать Smart Cycle EXPANSION.
        
        old_stage_expansion = (
            "EXPANSION" in stage_old
            or setup_class == "EXPANSION"
        )
        
        # ====================================================
        # PRICE EXPANSION
        #
        # Цена действительно начала ускоряться / пробивать.
        # Но это еще НЕ доказывает Smart Money.
        # ====================================================
        
        price_expansion = bool(
            acceleration
            and (
                breakout_confirmed
                or launch
            )
        )
        
        # ====================================================
        # SMART MONEY EXPANSION
        #
        # EXPANSION Smart Cycle разрешаем только если:
        #
        # 1) движение уже прошло POSITION_BUILDUP / PREMOVE
        #    ИЛИ
        #
        # 2) мы могли пропустить ранние стадии, но прямо сейчас
        #    есть реальные новые позиции + FLOW + CVD.
        #
        # Просто сильная цена сюда НЕ проходит.
        # ====================================================
        
        expansion = bool(
            price_expansion
            and (
                premove
                or position_buildup
                or (
                    oi_confirmed
                    and flow_confirmed
                    and cvd_confirmed
                )
            )
        )

        if expansion:
            if late_move:
                expansion_phase = "LATE_EXPANSION"
                reasons.append("LATE_EXPANSION")
            else:
                expansion_phase = "EARLY_EXPANSION"
                reasons.append("EARLY_EXPANSION")
        else:
            expansion_phase = "NONE"

        # ====================================================
        # 16. FINAL SMART CYCLE STAGE
        #
        # Это описание текущей стадии, НЕ торговое решение.
        # ====================================================

        if expansion:
            cycle_stage = "EXPANSION"

        elif premove:
            cycle_stage = "PREMOVE"

        elif position_buildup:
            cycle_stage = "POSITION_BUILDUP"

        elif accumulation:
            cycle_stage = "ACCUMULATION"

        elif stopping:
            cycle_stage = "STOPPING"

        elif exhaustion:
            cycle_stage = "EXHAUSTION"

        else:
            cycle_stage = "TREND"

        # ====================================================
        # 17. SEQUENCE QUALITY
        #
        # Показывает, насколько полно построена цепочка.
        # Это НЕ score торгового сигнала.
        # ====================================================

        sequence_checks = {
            "previous_move": previous_move != "UNKNOWN",
            "exhaustion": exhaustion,
            "stopping": stopping,
            "compression": compression,
            "absorption": absorption,
            "oi": oi_confirmed,
            "flow": flow_confirmed,
            "cvd": cvd_confirmed,
            "acc": acc_ready,
            "ep": ep_ready,
        }

        sequence_count = sum(
            1
            for value in sequence_checks.values()
            if value
        )

        sequence_total = len(sequence_checks)

        sequence_pct = round(
            sequence_count / sequence_total * 100,
            1
        )

        # ====================================================
        # 18. SAVE DIAGNOSTIC FIELDS
        # ====================================================

        result["smart_cycle_version"] = SMART_CYCLE_VERSION
        result["smart_cycle_stage"] = cycle_stage

        result["smart_cycle_direction"] = direction
        result["smart_cycle_previous_move"] = previous_move

        result["smart_cycle_exhaustion"] = exhaustion
        result["smart_cycle_exhaustion_debug"] = exhaustion_debug
        result["smart_cycle_exhaustion_evidence"] = (
            exhaustion_evidence
        )

        result["smart_cycle_stopping"] = stopping
        result["smart_cycle_stopping_source"] = (
            stopping_source
        )

        result["smart_cycle_compression"] = compression
        result["smart_cycle_absorption"] = absorption
        result["smart_cycle_pressure"] = pressure_aligned
        result["smart_cycle_pressure_shift"] = pressure_shift

        result["smart_cycle_acc_ready"] = acc_ready
        result["smart_cycle_ep_ready"] = ep_ready

        result["smart_cycle_oi_state"] = oi_state
        result["smart_cycle_real_oi_state"] = real_oi_state
        result["smart_cycle_oi_confirmed"] = oi_confirmed
        result["smart_cycle_oi_weak"] = oi_weak
        result["smart_cycle_oi_conflict"] = oi_conflict

        result["smart_cycle_flow_state"] = flow_state
        result["smart_cycle_flow_score"] = flow_score
        result["smart_cycle_flow_total"] = flow_total
        result["smart_cycle_flow_confirmed"] = flow_confirmed
        result["smart_cycle_flow_early"] = flow_early

        result["smart_cycle_capital_flow_score"] = (
            capital_flow_score
        )
        result["smart_cycle_legacy_capital_score"] = (
            legacy_capital_score
        )
        result["smart_cycle_capital_state"] = capital_state
        result["smart_cycle_capital_confirmed"] = (
            capital_confirmed
        )
        result["smart_cycle_capital_source"] = capital_source
        result["smart_cycle_flow_capital_gap"] = (
            flow_capital_gap
        )

        result["smart_cycle_cvd_state"] = cvd_state
        result["smart_cycle_cvd_confirmed"] = cvd_confirmed

        result["smart_cycle_accumulation"] = accumulation
        result["smart_cycle_raw_accumulation_evidence"] = (
            raw_accumulation_evidence
        )

        result["smart_cycle_position_buildup"] = (
            position_buildup
        )

        result["smart_cycle_premove"] = premove

        result["smart_cycle_expansion"] = expansion
        result["smart_cycle_expansion_phase"] = (
            expansion_phase
        )

        result["smart_cycle_late_move"] = late_move
        result["smart_cycle_late_move_penalty"] = (
            late_move_penalty
        )

        result["smart_cycle_sequence_count"] = (
            sequence_count
        )
        result["smart_cycle_sequence_total"] = (
            sequence_total
        )
        result["smart_cycle_sequence_pct"] = (
            sequence_pct
        )
        result["smart_cycle_sequence_checks"] = (
            sequence_checks
        )

        result["smart_cycle_reasons"] = reasons

        # Сохраняем старые значения только для сравнения.
        result["smart_cycle_old_stage"] = (
            signal.get("stage")
        )
        result["smart_cycle_old_smart_money_state"] = (
            signal.get("smart_money_state")
        )

        # ====================================================
        # 19. RAILWAY LOG
        # ====================================================

        if emit_log:
            print(
                f"[SMART_CYCLE] "
                f"{symbol} "
                f"dir={direction} "
                f"prev={previous_move} "
                f"exhaustion={exhaustion} "
                f"stopping={stopping} "
                f"compression={compression} "
                f"absorption={absorption} "
                f"oi={oi_state} "
                f"real_oi={real_oi_state} "
                f"oi_ok={oi_confirmed} "
                f"flow={flow_state} "
                f"flow_score={flow_score} "
                f"capital_flow={capital_flow_score} "
                f"capital={capital_state}:{legacy_capital_score} "
                f"cvd={cvd_state} "
                f"ACC={acc} "
                f"EP={ep} "
                f"late={late_move} "
                f"stage={cycle_stage} "
                f"expansion={expansion_phase} "
                f"seq={sequence_count}/{sequence_total}",
                flush=True,
            )
            
            if compression or absorption or acc >= 3 or ep >= 15:
                print(
                    f"[SMART_CYCLE_EXHAUST] "
                    f"{symbol} "
                    f"price_stalling={explicit_price_stalling} "
                    f"range_contracting={explicit_range_contracting} "
                    f"volume_fading={explicit_volume_fading} "
                    f"high_stop={explicit_high_stop} "
                    f"low_stop={explicit_low_stop} "
                    f"atr_fading={explicit_atr_fading} "
                    f"compression_after_trend={explicit_compression_after_trend} "
                    f"old_exhaustion={old_exhaustion} "
                    f"evidence={exhaustion_evidence}",
                    flush=True,
                )

            if flow_capital_gap:
                print(
                    f"[SMART_CYCLE_WARN] "
                    f"{symbol} "
                    f"FLOW_CAPITAL_GAP "
                    f"flow_score={flow_score} "
                    f"capital_flow_score={capital_flow_score} "
                    f"legacy_capital_state={capital_state} "
                    f"legacy_capital_score={legacy_capital_score}",
                    flush=True,
                )

            if oi_conflict:
                print(
                    f"[SMART_CYCLE_WARN] "
                    f"{symbol} "
                    f"OI_CONFLICT "
                    f"oi_state={oi_state} "
                    f"real_oi_state={real_oi_state}",
                    flush=True,
                )

        return result

    except Exception as e:
        # Критически важно:
        # ошибка диагностики НЕ должна ломать основной бот.
        try:
            signal["smart_cycle_version"] = SMART_CYCLE_VERSION
            signal["smart_cycle_stage"] = "SMART_CYCLE_ERROR"
            signal["smart_cycle_error"] = str(e)

            if emit_log:
                print(
                    f"[SMART_CYCLE_ERROR] "
                    f"{signal.get('instId') or signal.get('symbol')} "
                    f"{e}",
                    flush=True,
                )

        except Exception:
            pass

        return signal
