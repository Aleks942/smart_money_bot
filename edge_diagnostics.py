"""Read-only diagnostic: where does SmartMoney's hypothetical edge disappear?

Frozen features at signal time; resolved first-touch outcomes only.
Segment differences are exploratory, not deployment-approved trade filters.
Structural rows are reported separately from the fixed-1% audit.
"""
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict

from signal_analyst import DB_FILE

_MAX_ROWS = 5000
_MIN_GROUP = 20
_MAX_GROUP_LINES = 28
_WINDOW = 30 * 86400


def _cost_pct():
    def bounded(name, default):
        try:
            value = float(os.getenv(name, str(default)))
            return value if math.isfinite(value) and 0 <= value <= 100 else float(default)
        except (TypeError, ValueError):
            return float(default)
    return (bounded("SHADOW_FEE_BPS_SIDE", 6) +
            bounded("SHADOW_SLIPPAGE_BPS_SIDE", 5)) * 2.0 / 100.0


def _net(tp, sl, cost):
    n = tp + sl
    return (tp - sl) / n - cost if n else None


def _side_alignment(direction, state, positive, negative):
    state = str(state or "").upper()
    if positive in state:
        return "ALIGN" if direction == "UP" else "OPPOSE"
    if negative in state:
        return "ALIGN" if direction == "DOWN" else "OPPOSE"
    return "UNKNOWN"


def _snapshot_features(direction, snap):
    real_money = snap.get("real_money_confirm")
    oi = str(snap.get("oi_state") or "").upper()
    oi_state = (
        "ALIGN" if ((direction == "UP" and oi == "NEW_LONGS")
                    or (direction == "DOWN" and oi == "NEW_SHORTS"))
        else "OPPOSE" if oi in ("NEW_LONGS", "NEW_SHORTS") else "OTHER"
    )
    future = _side_alignment(
        direction, snap.get("cvd_state"), "BUY_CVD", "SELL_CVD"
    )
    spot = _side_alignment(
        direction, snap.get("spot_cvd_state"), "SPOT_BUY", "SPOT_SELL"
    )
    return {
        "real_money": "YES" if real_money is True else "NO" if real_money is False else "UNKNOWN",
        "oi": oi_state,
        "futures_cvd": future,
        "spot_cvd": spot,
        "both_cvd": "ALIGN" if future == "ALIGN" and spot == "ALIGN"
                    else "UNKNOWN" if "UNKNOWN" in (future, spot) else "NOT_BOTH",
        "retest": str(snap.get("retest_state") or "UNKNOWN").upper()[:30],
        "cycle": str(snap.get("smart_cycle_stage") or "UNKNOWN").upper()[:30],
        "rating": str(snap.get("rating") or "UNKNOWN").upper()[:12],
    }


def _print_factor_report(conn, since, cost):
    rows = conn.execute(
        """
        SELECT s.id, s.ts, s.entry_type, s.direction,
               s.snapshot_json, a.verdict
        FROM outcome_audits a JOIN signals s ON s.id=a.signal_id
        WHERE s.ts >= ? AND a.verdict IN ('TP_FIRST','SL_FIRST')
        ORDER BY s.ts, s.id LIMIT ?
        """, (since, _MAX_ROWS)
    ).fetchall()

    all_counts = Counter(verdict for *_, verdict in rows)
    n_all = len(rows)
    print(
        f"[EDGE_OVERVIEW] model=FIXED_1PCT resolved={n_all} "
        f"tp={all_counts['TP_FIRST']} sl={all_counts['SL_FIRST']} "
        f"cost_pct={cost:.3f} "
        f"net_per_signal={_net(all_counts['TP_FIRST'],all_counts['SL_FIRST'],cost)} "
        f"status=EXPLORATORY_NO_EXECUTION",
        flush=True,
    )

    if n_all < 40:
        print("[EDGE_FACTORS] status=INSUFFICIENT_SAMPLE", flush=True)
        return

    cutoff = int(n_all * 0.75)
    data = []
    missing = 0
    for i, (sid, ts, entry, direction, raw, verdict) in enumerate(rows):
        direction = str(direction or "").upper()
        if direction not in ("UP", "DOWN"):
            missing += 1
            continue
        try:
            snap = json.loads(raw or "{}")
            if not isinstance(snap, dict):
                raise ValueError("snapshot not a dict")
        except (TypeError, ValueError):
            missing += 1
            continue
        data.append({
            "entry": str(entry or "UNKNOWN").upper(),
            "direction": direction,
            "tp": verdict == "TP_FIRST",
            "recent": i >= cutoff,
            "factors": _snapshot_features(direction, snap),
        })

    # Cohort and factor counted only from information available at signal time.
    buckets = defaultdict(lambda: [0, 0, 0, 0])
    for row in data:
        cohorts = ["ALL", row["direction"], row["entry"]]
        for cohort in cohorts:
            for f, state in row["factors"].items():
                idx = (2 if row["recent"] else 0) + (0 if row["tp"] else 1)
                buckets[(cohort, f, state)][idx] += 1

    selected = sorted(
        ((key, v) for key, v in buckets.items()
         if sum(v) >= _MIN_GROUP),
        key=lambda t: (-sum(t[1]), str(t[0])),
    )[:_MAX_GROUP_LINES]

    print(
        f"[EDGE_FACTORS] usable={len(data)} missing={missing} "
        f"older_n={cutoff} recent_n={n_all-cutoff} "
        f"min_group={_MIN_GROUP} "
        f"note=TIME_SPLIT_IS_EXPLORATORY_NOT_TRUE_OOS",
        flush=True,
    )

    for (cohort, factor, state), (ot, os_, rt, rs) in selected:
        nt = ot + rt
        ns = os_ + rs
        historic = _net(ot, os_, cost)
        recent = _net(rt, rs, cost)
        combined = _net(nt, ns, cost)
        print(
            f"[EDGE_FACTOR] cohort={cohort} key={factor} value={state} "
            f"n={nt+ns} tp={nt} sl={ns} "
            f"old_n={ot+os_} old_net={f'{historic:+.4f}%' if historic is not None else 'NA'} "
            f"recent_n={rt+rs} "
            f"recent_net={f'{recent:+.4f}%' if recent is not None else 'NA'} "
            f"all_net={combined:+.4f}% "
            f"status=RESEARCH_ONLY",
            flush=True,
        )


def _stop_bucket(pct):
    if pct < 0.35:
        return "LT_0.35PCT"
    if pct < 0.75:
        return "0.35_TO_0.75PCT"
    if pct < 1.5:
        return "0.75_TO_1.5PCT"
    return "GE_1.5PCT"


def _print_structural_report(conn, since):
    rows = conn.execute(
        """
        SELECT s.id, s.entry_type, s.entry_price,
               a.verdict, a.stop_price, a.tp_price,
               a.rr, a.modeled_net_pct
        FROM structural_outcome_audits a
        JOIN signals s ON s.id=a.signal_id
        WHERE s.ts >= ? ORDER BY s.ts DESC LIMIT ?
        """, (since, _MAX_ROWS)
    ).fetchall()

    counts = Counter()
    buckets = defaultdict(lambda: [0, 0, 0, 0.0])
    rr_over_six = 0
    for sid, entry_type, entry, verdict, stop, tp, rr, modeled in rows:
        counts[verdict] += 1
        try:
            if rr is not None and float(rr) > 6.0:
                rr_over_six += 1
            e, s = float(entry), float(stop)
            if not (math.isfinite(e) and math.isfinite(s) and e > 0 and s > 0):
                continue
            pct = abs(e - s) / e * 100.0
        except (ValueError, TypeError):
            continue
        if not math.isfinite(pct):
            continue
        key = _stop_bucket(pct)
        item = buckets[key]
        item[0] += 1
        if verdict == "TP_FIRST":
            item[1] += 1
        elif verdict == "SL_FIRST":
            item[2] += 1
        if verdict in ("TP_FIRST", "SL_FIRST") and modeled is not None:
            try:
                value = float(modeled)
                if math.isfinite(value):
                    item[3] += value
            except (TypeError, ValueError):
                pass

    print(
        f"[STRUCT_EDGE] total={len(rows)} "
        f"legacy_no_levels={counts['LEVELS_NOT_RECORDED']} "
        f"invalid={counts['INVALID_LEVELS']} "
        f"tp={counts['TP_FIRST']} sl={counts['SL_FIRST']} "
        f"no_touch={counts['NO_TOUCH']} timeout={counts['TIMEOUT_NO_TOUCH']} "
        f"ambiguous={counts['ENTRY_MINUTE_AMBIGUOUS']+counts['SAME_MINUTE']} "
        f"rr_gt_6={rr_over_six} "
        f"status=EARLY_STRUCTURAL_RESULTS_NO_TRADING_INFERENCE",
        flush=True,
    )
    for bucket, values in sorted(buckets.items()):
        n, tp, sl, summed_net = values
        resolved = tp + sl
        mean_net = summed_net / resolved if resolved else None
        print(
            f"[STRUCT_EDGE_BUCKET] stop_width={bucket} n={n} "
            f"resolved={resolved} tp={tp} sl={sl} "
            f"mean_net={f'{mean_net:+.4f}%' if mean_net is not None else 'NA'}",
            flush=True,
        )


def print_edge_diagnostics():
    """Read-only, bounded SQL report. Does not gate trading or modify records."""
    try:
        with sqlite3.connect(DB_FILE, timeout=3) as conn:
            since = int(time.time()) - _WINDOW
            _print_factor_report(conn, since, _cost_pct())
            _print_structural_report(conn, since)
    except Exception as exc:
        print(
            f"[EDGE_DIAGNOSTIC_ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
