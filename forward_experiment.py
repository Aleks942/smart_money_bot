"""Pre-registered forward-only experiment for SmartMoney signal selection.

Does not send orders, alter alerts, or tune filters using current outcomes.
Rules are fixed here before their evaluation period begins. Reports are
hypothetical fixed +/-1% first-touch results, NOT executed trading PnL.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from collections import Counter

from signal_analyst import DB_FILE

_EXPERIMENT_ID = "SMARTMONEY_FORWARD_V1_20261010"
_REGISTRY_SPEC = {
    "version": "1",
    "baseline": "all recorded alert signals",
    "LONG_RETEST": "UP and retest_state RETEST_BUILDUP",
    "LONG_DUAL_CVD": "UP and spot buy and futures buy CVD",
    "LONG_REAL_MONEY": "UP and real_money_confirm true",
    "SHORT_OI_SELL": "DOWN and oi_state NEW_SHORTS and futures sell CVD",
    "LONG_STRUCTURAL_RISK": (
        "UP with frozen structural stop width 0.35..1.5%, "
        "valid goal and reward/risk >= 1.8"
    ),
    "outcome_model": "first touch +1pct or -1pct, no-touch excluded",
    "cost_model": "two sides of fee and adverse slippage; defaults 6+5 bps per side",
}
_REGISTRY_HASH = hashlib.sha256(
    json.dumps(_REGISTRY_SPEC, sort_keys=True).encode("utf-8")
).hexdigest()
_MAX_ROWS = 5000
_MIN_SIGNAL_AGE = 300
_PROSPECTIVE_MIN_RESOLVED = 50
_PROSPECTIVE_MIN_DAYS = 30


def _cost():
    out = []
    for name, default in (
        ("SHADOW_FEE_BPS_SIDE", 6.0),
        ("SHADOW_SLIPPAGE_BPS_SIDE", 5.0),
    ):
        try:
            num = float(os.getenv(name, str(default)))
            if not math.isfinite(num) or not (0 <= num <= 100):
                num = default
        except (ValueError, TypeError):
            num = default
        out.append(num)
    return 2.0 * sum(out) / 100.0


def _valid_structural(snap, entry, direction):
    if snap.get("level_snapshot_version") != "frozen-v1":
        return False
    try:
        price = float(entry)
        stop = float(snap.get("stop"))
        target = float(snap.get("tp1") or snap.get("target"))
        if not all(math.isfinite(x) and x > 0 for x in (price, stop, target)):
            return False
        if direction != "UP" or not stop < price < target:
            return False
        stop_width_pct = (price - stop) / price * 100.0
        rr = (target - price) / (price - stop)
        return 0.35 <= stop_width_pct <= 1.5 and rr >= 1.8
    except (ValueError, TypeError, ZeroDivisionError):
        return False


def _rules(direction, snap, entry):
    side = str(direction or "").upper()
    retest = str(snap.get("retest_state") or "").upper()
    fut = str(snap.get("cvd_state") or "").upper()
    spot = str(snap.get("spot_cvd_state") or "").upper()
    oi = str(snap.get("oi_state") or "").upper()
    return {
        "BASELINE": True,
        "LONG_RETEST": side == "UP" and retest == "RETEST_BUILDUP",
        "LONG_DUAL_CVD": (
            side == "UP" and "BUY_CVD" in fut and "SPOT_BUY" in spot
        ),
        "LONG_REAL_MONEY": (
            side == "UP" and snap.get("real_money_confirm") is True
        ),
        "SHORT_OI_SELL": (
            side == "DOWN" and oi == "NEW_SHORTS" and "SELL_CVD" in fut
        ),
        "LONG_STRUCTURAL_RISK": _valid_structural(snap, entry, side),
    }


def _register(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_experiment_registry (
            experiment_id TEXT PRIMARY KEY,
            registered_at INTEGER NOT NULL,
            rule_hash TEXT NOT NULL,
            specification TEXT NOT NULL
        )
    """)
    # +1 second ensures no signal that was already stored at registration
    # can become a member of this prospective population.
    conn.execute("""
        INSERT OR IGNORE INTO forward_experiment_registry (
            experiment_id, registered_at, rule_hash, specification
        ) VALUES (?, ?, ?, ?)
    """, (
        _EXPERIMENT_ID, int(time.time()) + 1, _REGISTRY_HASH,
        json.dumps(_REGISTRY_SPEC, sort_keys=True),
    ))
    row = conn.execute("""
        SELECT registered_at, rule_hash FROM forward_experiment_registry
        WHERE experiment_id=?
    """, (_EXPERIMENT_ID,)).fetchone()
    if not row or row[1] != _REGISTRY_HASH:
        raise RuntimeError("REGISTERED_RULES_CHANGED_USE_NEW_EXPERIMENT_ID")
    return int(row[0])


def register_forward_experiment():
    """Safe additive registry in existing SQLite; startup errors do not block scan."""
    try:
        with sqlite3.connect(DB_FILE, timeout=5) as conn:
            started = _register(conn)
        print(
            f"[FORWARD_EXPERIMENT_READY] id={_EXPERIMENT_ID} "
            f"start_ts={started} rules_hash={_REGISTRY_HASH[:12]} "
            f"no_retroactive_signals=true",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"[FORWARD_EXPERIMENT_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
        return False


def _wilson_lower_bound(wins, n, z=1.96):
    """Binomial indication only; correlated signals reduce true information."""
    if n <= 0:
        return None
    phat = wins / n
    z2 = z * z
    return (
        (phat + z2 / (2 * n)
         - z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n)))
        / (1 + z2 / n)
    )


def print_forward_experiment():
    """Hourly report of *future* saved signals and their audited outcomes."""
    now = int(time.time())
    try:
        with sqlite3.connect(DB_FILE, timeout=5) as conn:
            started = _register(conn)
            rows = conn.execute("""
                SELECT s.entry_price, s.direction, s.snapshot_json, a.verdict
                FROM signals s LEFT JOIN outcome_audits a ON a.signal_id=s.id
                WHERE s.ts >= ? AND s.ts <= ?
                ORDER BY s.ts, s.id LIMIT ?
            """, (started, now - _MIN_SIGNAL_AGE, _MAX_ROWS)).fetchall()
        counters = {name: Counter() for name in
                    ("BASELINE", "LONG_RETEST", "LONG_DUAL_CVD",
                     "LONG_REAL_MONEY", "SHORT_OI_SELL", "LONG_STRUCTURAL_RISK")}
        for price, direction, raw, verdict in rows:
            try:
                snap = json.loads(raw or "{}")
                if not isinstance(snap, dict):
                    snap = {}
            except (ValueError, TypeError):
                snap = {}
            for label, matched in _rules(direction, snap, price).items():
                if matched:
                    counters[label]["all"] += 1
                    if verdict == "TP_FIRST":
                        counters[label]["tp"] += 1
                    elif verdict == "SL_FIRST":
                        counters[label]["sl"] += 1
                    elif verdict in ("ENTRY_MINUTE_AMBIGUOUS", "SAME_MINUTE"):
                        counters[label]["ambiguous"] += 1
                    else:
                        counters[label]["pending_or_unresolved"] += 1

        cost = _cost()
        days = (now - started) / 86400.0
        print(
            f"[FORWARD_EXPERIMENT] id={_EXPERIMENT_ID} start_ts={started} "
            f"age_days={max(days,0):.2f} rows={len(rows)} "
            f"cost_pct={cost:.3f} prospective_only=true",
            flush=True,
        )
        for rule, stat in counters.items():
            won, lost = stat["tp"], stat["sl"]
            n = won + lost
            net = (won - lost) / n - cost if n else None
            lower = _wilson_lower_bound(won, n)
            # Never promote to live from this report. Even a positive sample
            # requires independent review of execution and drawdowns.
            status = (
                "NO_RESULTS" if n == 0
                else "COLLECTING" if n < _PROSPECTIVE_MIN_RESOLVED
                     or days < _PROSPECTIVE_MIN_DAYS
                else "REVIEW_REQUIRED"
            )
            print(
                f"[FORWARD_RULE] name={rule} eligible={stat['all']} "
                f"resolved={n} tp={won} sl={lost} "
                f"ambiguous={stat['ambiguous']} "
                f"pending={stat['pending_or_unresolved']} "
                f"hit_rate={f'{won/n*100:.1f}%' if n else 'NA'} "
                f"net_expectancy={f'{net:+.4f}%' if net is not None else 'NA'} "
                f"wilson_low={f'{lower*100:.1f}%' if lower is not None else 'NA'} "
                f"status={status} no_auto_trading=true",
                flush=True,
            )
    except Exception as exc:
        print(
            f"[FORWARD_EXPERIMENT_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
