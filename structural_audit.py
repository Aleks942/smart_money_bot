"""Non-trading structural TP/SL audit using frozen signal-time levels."""
import json
import math
import os
import sqlite3
import time
import requests

from signal_analyst import DB_FILE, save_structural_outcome_audit

_RETRY = ("NO_TOUCH", "FETCH_ERROR", "DATA_GAP", "INCOMPLETE_HISTORY", "NOT_READY")
_HORIZON = 14400


def evaluate(candles, entry, stop, tp, direction, created, checked):
    base = {"status": "INVALID_LEVELS", "first_ts": None, "bars": 0}
    try:
        entry, stop, tp = map(float, (entry, stop, tp))
        if not all(math.isfinite(x) and x > 0 for x in (entry, stop, tp)):
            return base
        side = str(direction).upper()
        if not ((side == "UP" and stop < entry < tp) or
                (side == "DOWN" and tp < entry < stop)):
            return base
        start = (int(created) // 60) * 60000
        first = start + 60000
        last = (int(checked) // 60 - 1) * 60000
        if last < first:
            return {**base, "status": "NOT_READY"}
        raw = {}
        for bar in candles:
            try:
                ts = int(bar[0])
                hi, lo = float(bar[2]), float(bar[3])
                if math.isfinite(hi) and math.isfinite(lo) and 0 < lo <= hi:
                    raw[ts] = (hi, lo)
            except (ValueError, TypeError, IndexError):
                continue
        if start not in raw:
            return {**base, "status": "INCOMPLETE_HISTORY"}
        hi, lo = raw[start]
        if hi >= (tp if side == "UP" else stop) or lo <= (stop if side == "UP" else tp):
            return {**base, "status": "ENTRY_MINUTE_AMBIGUOUS", "first_ts": start // 1000}
        ts = first
        bars = 0
        while ts <= last:
            if ts not in raw:
                return {**base, "status": "DATA_GAP", "bars": bars}
            hi, lo = raw[ts]
            bars += 1
            tp_hit = hi >= tp if side == "UP" else lo <= tp
            sl_hit = lo <= stop if side == "UP" else hi >= stop
            if tp_hit or sl_hit:
                verdict = "SAME_MINUTE" if tp_hit and sl_hit else "TP_FIRST" if tp_hit else "SL_FIRST"
                gross = (tp / entry - 1) * 100 if tp_hit and side == "UP" else (
                    (1 - tp / entry) * 100 if tp_hit else (
                        (stop / entry - 1) * 100 if side == "UP" else (1 - stop / entry) * 100))
                if verdict == "SAME_MINUTE":
                    gross = None
                return {"status": verdict, "first_ts": ts // 1000,
                        "bars": bars, "gross_pct": round(gross, 6) if gross is not None else None}
            ts += 60000
        return {"status": "NO_TOUCH", "first_ts": None, "bars": bars}
    except (TypeError, ValueError, OverflowError):
        return base


def run_structural_audit():
    now = int(time.time())
    try:
        with sqlite3.connect(DB_FILE, timeout=3) as conn:
            rows = conn.execute("""
                SELECT s.id, s.symbol, s.entry_price, s.direction, s.ts,
                       s.snapshot_json
                FROM signals s
                LEFT JOIN structural_outcome_audits a ON a.signal_id=s.id
                WHERE s.ts >= ? AND s.ts <= ?
                  AND (a.signal_id IS NULL OR (
                    a.verdict IN ('NO_TOUCH','FETCH_ERROR','DATA_GAP',
                                  'INCOMPLETE_HISTORY','NOT_READY')
                    AND a.checked_at <= ?))
                ORDER BY CASE WHEN a.signal_id IS NULL THEN 0 ELSE 1 END,
                         s.ts DESC LIMIT 1
            """, (now-86400, now-180, now-300)).fetchall()
    except Exception as exc:
        print(f"[STRUCTURAL_AUDIT_QUERY_ERROR] {exc}", flush=True)
        return
    for sid, symbol, entry, side, signal_ts, raw in rows:
        data = {"entry_price": entry}
        try:
            snap = json.loads(raw or "{}")
            # Old rows never contained original levels: do not invent history.
            if snap.get("level_snapshot_version") != "frozen-v1":
                data.update(status="LEVELS_NOT_RECORDED", reason="legacy_signal")
            else:
                stop = snap.get("stop")
                tp = snap.get("tp1") or snap.get("target")
                source = "tp1" if snap.get("tp1") else "target_3pct"
                data.update(stop_price=stop, tp_price=tp,
                            tp2_price=snap.get("tp2"), tp_source=source)
                try:
                    ep, st, tg = float(entry), float(stop), float(tp)
                    if not all(math.isfinite(x) for x in (ep,st,tg)) or ep <= 0:
                        raise ValueError("non-finite levels")
                    if not (st < ep < tg if side == "UP" else tg < ep < st):
                        raise ValueError("invalid direction/order")
                    data["rr"] = round(abs(tg-ep)/abs(ep-st), 4)
                except (ValueError, TypeError, ZeroDivisionError):
                    data.update(status="INVALID_LEVELS", reason="missing_or_wrong_side_stop")
                else:
                    created = int(snap.get("journal_saved_at") or signal_ts)
                    end = min(now, created+_HORIZON)
                    if end <= created:
                        data.update(status="NOT_READY")
                    else:
                        age = max(1, (end-created)//60)
                        try:
                            res = requests.get(
                                "https://api.bybit.com/v5/market/kline",
                                params={"category":"linear","symbol":symbol,
                                        "interval":"1","end":str(end*1000),
                                        "limit":str(min(1000,max(60,age+5)))},
                                timeout=4)
                            res.raise_for_status()
                            body = res.json()
                            if body.get("retCode") != 0:
                                raise ValueError(str(body.get("retMsg"))[:80])
                            candles = (body.get("result") or {}).get("list") or []
                            data.update(evaluate(candles,ep,st,tg,side,created,end))
                        except Exception as exc:
                            data.update(status="FETCH_ERROR",reason=str(exc)[:100])
                    if data.get("status") == "NO_TOUCH" and now >= created+_HORIZON+120:
                        data["status"] = "TIMEOUT_NO_TOUCH"
                    if data.get("gross_pct") is not None:
                        try:
                            fee=float(os.getenv("SHADOW_FEE_BPS_SIDE","6"))
                            slip=float(os.getenv("SHADOW_SLIPPAGE_BPS_SIDE","5"))
                            if not (0 <= fee <= 100 and 0 <= slip <= 100):
                                raise ValueError("cost")
                            data["modeled_net_pct"] = round(data["gross_pct"]-2*(fee+slip)/100,6)
                        except (TypeError,ValueError):
                            data["reason"]="INVALID_COST_ASSUMPTION"
        except Exception as exc:
            data.update(status="AUDIT_ERROR",reason=str(exc)[:100])
        saved=save_structural_outcome_audit(sid,data)
        print(f"[STRUCTURAL_AUDIT] {symbol} signal_id={sid} "
              f"status={data.get('status')} rr={data.get('rr')} "
              f"net={data.get('modeled_net_pct')} saved={saved}",flush=True)
