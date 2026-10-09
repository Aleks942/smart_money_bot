"""Bounded, best-effort shadow TP/SL audits independent of legacy closure.

Audits recent saved signals while OPEN, and can backfill recently closed ones.
Never changes trade signals, legacy results, or Telegram messages.
"""
import sqlite3
import time

from outcome_audit import audit_bybit_1m
from signal_analyst import DB_FILE, save_outcome_audit

# Two requests per scan, up to 4 seconds each. This runs inside the scan loop
# without additional threads, services, or persistent background workers.
_BATCH = 2
_RECHECK_SEC = 300
_MIN_AGE_SEC = 180
_MAX_AGE_SEC = 24 * 3600
_HORIZON_SEC = 4 * 3600
_TIMEOUT_GRACE_SEC = 120

_RETRYABLE = (
    "NO_TOUCH", "NOT_READY", "FETCH_ERROR",
    "INCOMPLETE_HISTORY", "DATA_GAP",
)


def audit_pending_saved_signals():
    """Audit at most two recent signal IDs per scan cycle."""
    now = int(time.time())

    try:
        with sqlite3.connect(DB_FILE, timeout=3) as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.symbol, s.entry_price, s.direction,
                       s.ts, s.result
                FROM signals AS s
                LEFT JOIN outcome_audits AS a ON a.signal_id = s.id
                WHERE s.ts >= ? AND s.ts <= ?
                  AND (
                      a.signal_id IS NULL
                      OR (
                          a.verdict IN (?, ?, ?, ?, ?)
                          AND a.checked_at <= ?
                      )
                  )
                ORDER BY
                    CASE WHEN s.result = 'OPEN' THEN 0 ELSE 1 END,
                    CASE WHEN a.signal_id IS NULL THEN 0 ELSE 1 END,
                    s.ts DESC
                LIMIT ?
                """,
                (
                    now - _MAX_AGE_SEC, now - _MIN_AGE_SEC,
                    *_RETRYABLE, now - _RECHECK_SEC, _BATCH,
                ),
            ).fetchall()
    except Exception as exc:
        print(
            f"[SHADOW_AUDIT_SELECT_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
        return

    for signal_id, symbol, price, direction, created_at, legacy in rows:
        try:
            created_at = int(created_at)
            # Keep the same 4-hour evaluation horizon as the legacy analyst,
            # but use minute-bar first touch rather than a later snapshot.
            end_at = min(now, created_at + _HORIZON_SEC)
            audit = audit_bybit_1m(
                symbol, price, direction, created_at,
                checked_at=end_at, timeout=4,
            )
            if (
                audit.get("status") == "NO_TOUCH"
                and now >= created_at + _HORIZON_SEC + _TIMEOUT_GRACE_SEC
            ):
                audit = {**audit, "status": "TIMEOUT_NO_TOUCH"}

            saved = save_outcome_audit(signal_id, legacy, audit)
            print(
                f"[SHADOW_AUDIT] {symbol} signal_id={signal_id} "
                f"status={audit.get('status')} "
                f"legacy={legacy} "
                f"bars={audit.get('bars')} "
                f"saved={saved}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[SHADOW_AUDIT_ERROR] signal_id={signal_id} "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
