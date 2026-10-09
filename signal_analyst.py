import sqlite3
import json
import time
import os
from datetime import datetime

DB_FILE = os.getenv("SIGNALS_DB_FILE", "signals.db")


# ==============================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# ==============================

def init_db():

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        ts INTEGER,
        time_str TEXT,
        entry_price REAL,
        entry_type TEXT,
        direction TEXT,
        score INTEGER,
        acc_score INTEGER,
        stage TEXT,
        setup TEXT,
        expected_move_min REAL,
        expected_move_max REAL,
        result TEXT,
        move_pct REAL,
        snapshot_json TEXT,
        oi_firewall_blocked INTEGER DEFAULT 0,
        oi_firewall_reason TEXT
    )
    """)

    try:
        cur.execute("ALTER TABLE signals ADD COLUMN setup TEXT")
    except:
        pass

    try:
        cur.execute("ALTER TABLE signals ADD COLUMN entry_type TEXT")
    except:
        pass

    try:
        cur.execute(
            "ALTER TABLE signals "
            "ADD COLUMN oi_firewall_blocked INTEGER DEFAULT 0"
        )
    except:
        pass

    try:
        cur.execute(
            "ALTER TABLE signals "
            "ADD COLUMN oi_firewall_reason TEXT"
        )
    except:
        pass

    try:
        cur.execute(
            "ALTER TABLE signals "
            "ADD COLUMN snapshot_json TEXT"
        )
    except:
        pass

    # Separate shadow results; never overwrite the legacy signals/stats.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS outcome_audits (
        signal_id INTEGER PRIMARY KEY,
        checked_at INTEGER NOT NULL,
        audit_version TEXT NOT NULL,
        verdict TEXT NOT NULL,
        legacy_result TEXT,
        first_touch_ts INTEGER,
        bars INTEGER,
        mfe_pct REAL,
        mae_pct REAL
    )
    """)

    conn.commit()

    # Read-only startup health check; never interrupt trading on report failure.
    try:
        rows = cur.execute("""
            SELECT verdict, COUNT(*) FROM outcome_audits GROUP BY verdict
        """).fetchall()
        summary = {verdict: count for verdict, count in rows}
        print(
            f"[OUTCOME_AUDIT_DB_READY] rows={sum(summary.values())} "
            f"verdicts={summary}",
            flush=True,
        )
    except Exception as exc:
        print(
            f"[OUTCOME_AUDIT_DB_CHECK_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )

    conn.close()


# ==============================
# СОХРАНЕНИЕ СИГНАЛА
# ==============================

def save_signal(signal):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    ts = signal.get("ts", int(time.time()))
    time_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    snapshot = {
        "direction_code": signal.get("direction_code"),
        "direction": signal.get("direction"),
        "entry": signal.get("entry"),
        "entry_type": signal.get("entry_type"),
        "signal_group": signal.get("signal_group"),
        "signal_mode": signal.get("signal_mode"),
        "score": signal.get("score"),
        "rating": signal.get("rating"),
        "strength": signal.get("strength"),
        "legacy_confidence": signal.get("legacy_confidence"),
        "legacy_confidence_score": signal.get("legacy_confidence_score"),
        "context_grade": signal.get("context_grade"),
        "acc_score": signal.get("acc_score"),
        "early_pressure_score": signal.get("early_pressure_score"),
        "stage": signal.get("stage"),
        "oi_change": signal.get("oi_change"),
        "oi_state": signal.get("oi_state"),
        "real_oi_state": signal.get("real_oi_state"),
        "real_money_confirm": signal.get("real_money_confirm"),
        "flow_state": signal.get("flow_state"),
        "flow_score": signal.get("flow_score"),
        "capital_flow_score": signal.get("capital_flow_score"),
        "smart_money_state": signal.get("smart_money_state"),
        "smart_money_score": signal.get("smart_money_score"),
        "cvd_state": signal.get("cvd_state"),
        "spot_cvd_state": signal.get("spot_cvd_state"),
        "spot_cvd_ratio": signal.get("spot_cvd_ratio"),
        "spot_cvd_source": signal.get("spot_cvd_source"),
        "spot_cvd_window_sec": signal.get("spot_cvd_window_sec"),
        "late_move_penalty": signal.get("late_move_penalty"),
        "retest_state": signal.get("retest_state"),
        "smart_cycle_stage": signal.get("smart_cycle_stage"),
        "smart_cycle_sequence_pct": signal.get("smart_cycle_sequence_pct"),
        "flags": signal.get("flags", []),
    }

    snapshot_json = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )

    entry_name = str(
        signal.get("entry_type")
        or signal.get("entry")
        or signal.get("entry_reason")
        or ""
    ).upper()

    if "LONG" in entry_name or "BUY" in entry_name:
        persisted_direction = "UP"
    elif "SHORT" in entry_name or "SELL" in entry_name:
        persisted_direction = "DOWN"
    else:
        raw_direction = str(
            signal.get("direction_code")
            or signal.get("direction")
            or ""
        ).upper()

        if (
            raw_direction in ("DOWN", "SHORT", "SELL")
            or "SHORT" in raw_direction
            or "SELL" in raw_direction
            or "ВНИЗ" in raw_direction
        ):
            persisted_direction = "DOWN"
        elif (
            raw_direction in ("UP", "LONG", "BUY")
            or "LONG" in raw_direction
            or "BUY" in raw_direction
            or "ВВЕРХ" in raw_direction
        ):
            persisted_direction = "UP"
        else:
            persisted_direction = "FLAT"

    cur.execute("""
    INSERT INTO signals (
        symbol, ts, time_str,
        entry_price, entry_type, direction,
        score, acc_score,
        stage,
        expected_move_min,
        expected_move_max,
        result,
        move_pct,
        snapshot_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
        signal["instId"],
        ts,
        time_str,
        signal.get("entry_price", signal["price"]),
        signal.get("entry_type", signal.get("entry", "UNKNOWN")),
        persisted_direction,
        signal["score"],
        signal["acc_score"],
        signal["stage"],
        signal["exp_move_min"],
        signal["exp_move_max"],
        "OPEN",
        0.0,
        snapshot_json,
    ))

    conn.commit()
    conn.close()


# ==============================
# OI FIREWALL MARK
# ==============================

def mark_oi_firewall_blocked(signal, reason):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    symbol = signal.get("instId")

    entry_price = signal.get(
        "entry_price",
        signal.get("price")
    )

    cur.execute("""
        UPDATE signals
        SET
            oi_firewall_blocked = 1,
            oi_firewall_reason = ?
        WHERE id = (
            SELECT id
            FROM signals
            WHERE symbol = ?
              AND entry_price = ?
              AND result = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
        )
    """, (
        reason,
        symbol,
        entry_price
    ))

    conn.commit()
    conn.close()


# ==============================
# ОБНОВЛЕНИЕ РЕЗУЛЬТАТА
# ==============================

def update_signal_result(symbol, entry_price, current_price):

    move_pct = (current_price - entry_price) / entry_price * 100

    result = "NEUTRAL"

    if move_pct >= 0.5:
        result = "HIT"

    elif move_pct <= -0.5:
        result = "FAIL"

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    UPDATE signals
    SET result=?, move_pct=?
    WHERE symbol=? AND entry_price=? AND result='OPEN'
    """, (result, move_pct, symbol, entry_price))

    conn.commit()
    conn.close()


# ==============================
# СТАТИСТИКА
# ==============================

def get_stats():

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM signals")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM signals WHERE result='HIT'")
    hits = cur.fetchone()[0]

    winrate = 0

    if total > 0:
        winrate = hits / total * 100

    conn.close()

    return {
        "total": total,
        "hits": hits,
        "winrate": round(winrate, 2)
    }

# ==============================
# ПОЛУЧИТЬ ОТКРЫТЫЕ СИГНАЛЫ
# ==============================

def get_open_signals(older_than_sec=300):

    now = int(time.time())

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, symbol, entry_price, entry_type, direction, stage, ts, snapshot_json
        FROM signals
        WHERE result='OPEN'
    """)

    rows = cur.fetchall()
    conn.close()

    signals = []

    for r in rows:

        (
            signal_id,
            symbol,
            entry,
            entry_type,
            direction,
            stage,
            ts,
            snapshot_json,
        ) = r

        snapshot = {}

        if snapshot_json:
            try:
                snapshot = json.loads(snapshot_json)
            except Exception:
                snapshot = {}

        if now - ts >= older_than_sec:

            signals.append({
                "id": signal_id,
                "symbol": symbol,
                "entry": entry,
                "entry_type": entry_type,
                "direction": direction,
                "stage": stage,
                "created_at": ts,
                "snapshot": snapshot,
            })

    return signals


# ==============================
# ЗАКРЫТЬ СИГНАЛ
# ==============================

def close_signal(signal_id, move_pct, result):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE signals
        SET result=?, move_pct=?
        WHERE id=?
    """, (result, move_pct, signal_id))

    conn.commit()
    conn.close()



 

# ==============================
# INDEPENDENT SHADOW AUDIT STORAGE
# ==============================

def save_outcome_audit(signal_id, legacy_result, audit):
    """Store an independent candle verdict without touching legacy statistics."""
    try:
        verdict = str(audit.get("status") or "UNKNOWN")
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("""
                INSERT INTO outcome_audits (
                    signal_id, checked_at, audit_version, verdict,
                    legacy_result, first_touch_ts, bars, mfe_pct, mae_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    checked_at=excluded.checked_at,
                    audit_version=excluded.audit_version,
                    verdict=excluded.verdict,
                    legacy_result=excluded.legacy_result,
                    first_touch_ts=excluded.first_touch_ts,
                    bars=excluded.bars,
                    mfe_pct=excluded.mfe_pct,
                    mae_pct=excluded.mae_pct
            """, (
                int(signal_id), int(time.time()), "bybit-1m-v2",
                verdict, str(legacy_result),
                audit.get("first_ts"), audit.get("bars"),
                audit.get("mfe_pct"), audit.get("mae_pct"),
            ))
        return True
    except Exception as exc:
        print(
            f"[FIRST_TOUCH_AUDIT_DB_ERROR] signal_id={signal_id} "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return False
