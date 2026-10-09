"""Read-only *estimated* net expectancy from durable first-touch audits.

This is a hypothetical cost scenario, not executed PnL:
TP=+1% / SL=-1%; two taker-fee and two adverse slippage charges.
Funding, market impact beyond assumed slippage, spread variation and gaps are
not observable here. Ambiguous / incomplete audits are excluded from expectancy.
No decisions about trading signals depend on this module.
"""
import math
import os
import sqlite3
import time

from signal_analyst import DB_FILE

_WINDOW_SEC = 30 * 24 * 3600
_MIN_EVALUATED = 30
_MAX_GROUPS = 12
_ALLOWED_VERDICTS = ("TP_FIRST", "SL_FIRST")


def _assumption_bps(name, default):
    """Never accept negative, NaN, infinite or unrealistic cost assumptions."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) and 0 <= value <= 100 else float(default)


def estimate_outcome(verdict, fee_bps=6.0, slippage_bps=5.0):
    """Estimated net percentage of notional, or None for unresolved outcomes."""
    if verdict not in _ALLOWED_VERDICTS:
        return None
    try:
        fee = float(fee_bps)
        slip = float(slippage_bps)
    except (ValueError, TypeError):
        return None
    if (
        not math.isfinite(fee) or not math.isfinite(slip)
        or fee < 0 or slip < 0 or fee > 100 or slip > 100
    ):
        return None

    gross_pct = 1.0 if verdict == "TP_FIRST" else -1.0
    round_trip_cost_pct = 2 * (fee + slip) / 100.0
    return round(gross_pct - round_trip_cost_pct, 6)


def _describe(label, tp, sl, other, fee_bps, slip_bps):
    n = tp + sl
    total = n + other
    excluded = other
    if not n:
        return (
            f"[SHADOW_NET] entry={label} audited={total} resolved=0 "
            f"excluded={excluded} status=NO_DATA"
        )

    net_tp = estimate_outcome("TP_FIRST", fee_bps, slip_bps)
    net_sl = estimate_outcome("SL_FIRST", fee_bps, slip_bps)
    expectancy = (tp * net_tp + sl * net_sl) / n
    winrate = tp / n * 100.0
    # For +1/-1 gross TP/SL, winrate breakeven >=(1+cost)/2.
    break_even = (1.0 + 2 * (fee_bps + slip_bps) / 100.0) / 2.0 * 100.0

    # Fewer than 30 independent recorded signals is too early even to
    # form a preliminary working hypothesis.
    status = "EARLY_SAMPLE" if n < _MIN_EVALUATED else "NEEDS_OOS_VALIDATION"
    return (
        f"[SHADOW_NET] entry={label} audited={total} resolved={n} "
        f"tp={tp} sl={sl} excluded={excluded} "
        f"hit_rate={winrate:.1f}% "
        f"net_expectancy={expectancy:+.4f}% "
        f"net_tp={net_tp:+.2f}% net_sl={net_sl:+.2f}% "
        f"breakeven_hit_rate={break_even:.1f}% "
        f"fee_bps_side={fee_bps:g} slip_bps_side={slip_bps:g} "
        f"status={status} model=FIXED_1PCT_SHADOW_NO_FUNDING"
    )


def print_shadow_profit_report():
    """SQL aggregation in existing DB; report only, no writes or API calls."""
    fee_bps = _assumption_bps("SHADOW_FEE_BPS_SIDE", 6.0)
    slip_bps = _assumption_bps("SHADOW_SLIPPAGE_BPS_SIDE", 5.0)
    try:
        with sqlite3.connect(DB_FILE, timeout=3) as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(NULLIF(s.entry_type, ''), 'UNKNOWN'),
                       a.verdict, COUNT(*)
                FROM outcome_audits AS a
                JOIN signals AS s ON s.id = a.signal_id
                WHERE s.ts >= ?
                GROUP BY COALESCE(NULLIF(s.entry_type, ''), 'UNKNOWN'),
                         a.verdict
                """,
                (int(time.time()) - _WINDOW_SEC,),
            ).fetchall()

        groups = {}
        totals = [0, 0, 0]
        for entry_type, verdict, count in rows:
            counters = groups.setdefault(str(entry_type), [0, 0, 0])
            target = 0 if verdict == "TP_FIRST" else 1 if verdict == "SL_FIRST" else 2
            counters[target] += count
            totals[target] += count

        print(_describe("ALL", *totals, fee_bps, slip_bps), flush=True)
        for entry_type, counters in sorted(
            groups.items(), key=lambda t: -(t[1][0] + t[1][1])
        )[:_MAX_GROUPS]:
            print(
                _describe(entry_type.replace(" ", "_"), *counters, fee_bps, slip_bps),
                flush=True,
            )
    except Exception as exc:
        print(
            f"[SHADOW_NET_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
