"""Bounded non-trading prospective study of FINAL_INVALID SmartMoney setups.

Strictly independent from the original scoring, alert decisions and trade plans.
One sampled rejection per symbol/side/30-minute bucket. Uses ONLY fully closed
1m Bybit candles after signal time. Results are close-to-close observations,
not realized PnL; no order placement or Telegram messages.
"""
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

import requests

DB = os.getenv("REJECTION_STUDY_DB", "/data/rejection_study.sqlite3")
API = "https://api.bybit.com/v5/market/kline"
HORIZONS = (5, 10, 30)
MAX_ROWS = 5000
TTL_SEC = 30 * 86400
BATCH_SIZE = 2
RETRY_LIMIT = 5
RECHECK_SEC = 120
_LOCK = threading.Lock()
_started = False
_recent = {}


@contextmanager
def _connect():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    db = sqlite3.connect(DB, timeout=4)
    try:
        db.execute("PRAGMA busy_timeout=4000")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rejects (
        symbol TEXT NOT NULL, bucket INTEGER NOT NULL, ts REAL NOT NULL,
        side TEXT NOT NULL, price REAL NOT NULL, score REAL NOT NULL,
        ep REAL NOT NULL, acc REAL NOT NULL, flow TEXT, smart_money TEXT,
        spot_conflict INTEGER NOT NULL, oi_wait INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING',
        p5 REAL, p10 REAL, p30 REAL, error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_ts REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(symbol,bucket,side)
    )""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(rejects)")}
    if "attempts" not in columns:
        db.execute("ALTER TABLE rejects ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    if "next_attempt_ts" not in columns:
        db.execute("ALTER TABLE rejects ADD COLUMN next_attempt_ts REAL NOT NULL DEFAULT 0")
    db.execute("CREATE INDEX IF NOT EXISTS idx_reject_pending ON rejects(state,next_attempt_ts,ts)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_reject_ts ON rejects(ts)")
    # A wholly independent study cohort: alerts that were sent AND journaled.
    # No changes to the existing rejection rows or their primary key.
    db.execute("""CREATE TABLE IF NOT EXISTS accepts (
        symbol TEXT NOT NULL, bucket INTEGER NOT NULL, ts REAL NOT NULL,
        side TEXT NOT NULL, price REAL NOT NULL, score REAL NOT NULL,
        entry_type TEXT, context TEXT,
        state TEXT NOT NULL DEFAULT 'PENDING',
        p5 REAL, p10 REAL, p30 REAL, error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_ts REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(symbol,bucket,side)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_accept_pending ON accepts(state,next_attempt_ts,ts)")


def record(symbol, side, price, score, ep, acc, flow, smart_money, flags):
    """Record decision context; NEVER influences whether a signal is sent."""
    global _started
    try:
        symbol, side = str(symbol).upper(), str(side).upper()
        numbers = [float(x) for x in (price, score, ep, acc)]
        if (side not in ("LONG", "SHORT") or
                not symbol.endswith("USDT") or
                not all(math.isfinite(x) for x in numbers) or numbers[0] <= 0):
            return False
        now = time.time()
        bucket = int(now // 1800)
        key = (symbol, side)
        with _LOCK:
            if _recent.get(key) == bucket:
                return False
            with _connect() as db:
                _setup(db)
                cursor = db.execute("""INSERT OR IGNORE INTO rejects (
                    symbol,bucket,ts,side,price,score,ep,acc,flow,smart_money,
                    spot_conflict,oi_wait
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    symbol, bucket, now, side, *numbers,
                    str(flow), str(smart_money),
                    int("SPOT_CONFLICT_LONG" in flags or "SPOT_CONFLICT_SHORT" in flags),
                    int("SMART_MONEY_WAIT_OI_CONFIRM" in flags),
                ))
                inserted = cursor.rowcount == 1
            if len(_recent) >= 2048:
                _recent.clear()
            _recent[key] = bucket
            if not _started:
                thread = threading.Thread(
                    target=_worker, name="rejection-study", daemon=True
                )
                thread.start()
                _started = True
        if inserted:
            print(f"[REJECT_STUDY_SAVED] {symbol} side={side} "
                  f"score={numbers[1]:g} no_orders=true", flush=True)
        return inserted
    except Exception as exc:
        print(f"[REJECT_STUDY_ERROR] stage=record kind={type(exc).__name__}", flush=True)
        return False


def start_worker():
    """Resume both research cohorts even after a container restart."""
    global _started
    with _LOCK:
        if _started:
            return
        with _connect() as db:
            _setup(db)
        thread = threading.Thread(target=_worker,
                                  name="candidate-forward-study", daemon=True)
        thread.start()
        _started = True


def record_accepted(signal, context="UNKNOWN"):
    """Log only *successfully sent and journaled* signals (not candidates).

    Sampling matches the existing rejection journal: one row per symbol,
    direction and 30-minute bucket. All calculations remain non-trading.
    """
    try:
        symbol = str(signal.get("instId") or signal.get("symbol") or "").upper()
        code = str(signal.get("direction_code") or signal.get("direction") or "").upper()
        side = {"UP":"LONG", "DOWN":"SHORT", "BUY":"LONG",
                "SELL":"SHORT"}.get(code, code)
        if side not in ("LONG","SHORT"):
            entry = str(signal.get("entry_type") or signal.get("entry") or "").upper()
            if "LONG" in entry or "BUY" in entry:
                side = "LONG"
            elif "SHORT" in entry or "SELL" in entry:
                side = "SHORT"
        price = float(signal.get("price"))
        score = float(signal.get("score") or 0)
        if (side not in ("LONG","SHORT") or not symbol.endswith("USDT")
                or not all(math.isfinite(x) for x in (price,score))
                or price <= 0):
            return False
        now = time.time()
        with _connect() as db:
            _setup(db)
            cursor = db.execute("""INSERT OR IGNORE INTO accepts
                (symbol,bucket,ts,side,price,score,entry_type,context)
                VALUES (?,?,?,?,?,?,?,?)""",
                (symbol,int(now//1800),now,side,price,score,
                 str(signal.get("entry_type") or signal.get("entry") or ""),
                 str(context)[:80]))
            created = cursor.rowcount == 1
        if created:
            print(f"[ACCEPT_STUDY_SAVED] {symbol} side={side} "
                  f"score={score:g} context={str(context)[:40]} "
                  "model=forward_only_no_orders",flush=True)
        return created
    except (ValueError,TypeError,sqlite3.Error,OverflowError) as exc:
        print(f"[ACCEPT_STUDY_ERROR] stage=record kind={type(exc).__name__}",
              flush=True)
        return False


def _parse_candles(payload, first_ms):
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise ValueError("BYBIT_BAD_RESPONSE")
    raw = (payload.get("result") or {}).get("list")
    if not isinstance(raw, list) or len(raw) != 30:
        raise ValueError("INCOMPLETE_HISTORY")
    bars = []
    for item in raw:
        if len(item) < 5:
            raise ValueError("MISSING_OHLC")
        ts = int(item[0])
        op, hi, lo, close = [float(item[i]) for i in range(1, 5)]
        if not (all(map(math.isfinite, (op, hi, lo, close)))
                and 0 < lo <= min(op, close) <= max(op, close) <= hi):
            raise ValueError("INVALID_OHLC")
        bars.append((ts, op, hi, lo, close))
    bars.sort(key=lambda r: r[0])
    if any(bar[0] != first_ms + i * 60000 for i, bar in enumerate(bars)):
        raise ValueError("GAPPED_HISTORY")
    return bars


def _forward_closes(side, bars):
    direction = 1 if side == "LONG" else -1
    first_open = bars[0][1]
    return [round(direction * (bars[i-1][4] / first_open - 1) * 100, 4)
            for i in HORIZONS]


def _evaluate(row):
    symbol, bucket, ts, side, attempts = row
    first_ms = (int(ts // 60) + 1) * 60000
    try:
        response = requests.get(
            API,
            params={"category": "linear", "symbol": symbol,
                    "interval": "1", "start": first_ms,
                    "end": first_ms + 30 * 60000 - 1, "limit": 30},
            timeout=8,
        )
        response.raise_for_status()
        bars = _parse_candles(response.json(), first_ms)
        p5, p10, p30 = _forward_closes(side, bars)
        with _connect() as db:
            db.execute("""UPDATE rejects
                SET state='DONE', p5=?, p10=?, p30=?, error=NULL
                WHERE symbol=? AND bucket=? AND side=? AND state='PENDING'""",
                       (p5, p10, p30, symbol, bucket, side))
        print(f"[REJECT_STUDY_OUTCOME] {symbol} side={side} "
              f"5m={p5:+.3f}% 10m={p10:+.3f}% 30m={p30:+.3f}% "
              f"model=close_only_no_orders_no_costs", flush=True)
    except Exception as exc:
        attempts += 1
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        permanent = isinstance(exc, requests.HTTPError) and (
            status_code in (400, 401, 403, 404)
        )
        status = "FAILED" if permanent or attempts >= RETRY_LIMIT else "PENDING"
        delay = min(3600, RECHECK_SEC * (2 ** min(attempts - 1, 4)))
        reason = f"{type(exc).__name__}:{status_code or ''}"[:80]
        with _connect() as db:
            db.execute("""UPDATE rejects
                SET state=?, attempts=?, next_attempt_ts=?, error=?
                WHERE symbol=? AND bucket=? AND side=? AND state='PENDING'""",
                       (status, attempts, time.time() + delay, reason,
                        symbol, bucket, side))
        print(f"[REJECT_STUDY_RETRY] {symbol} side={side} "
              f"state={status} attempts={attempts} error={reason}",
              flush=True)


def _evaluate_accepted(row):
    symbol,bucket,ts,side,attempts = row
    first_ms = (int(ts//60)+1)*60000
    try:
        response = requests.get(
            API,params={"category":"linear","symbol":symbol,
                        "interval":"1","start":first_ms,
                        "end":first_ms+30*60000-1,"limit":30}, timeout=8)
        response.raise_for_status()
        bars = _parse_candles(response.json(),first_ms)
        p5,p10,p30 = _forward_closes(side,bars)
        with _connect() as db:
            db.execute("""UPDATE accepts SET state='DONE',
                p5=?,p10=?,p30=?,error=NULL
                WHERE symbol=? AND bucket=? AND side=? AND state='PENDING'""",
                (p5,p10,p30,symbol,bucket,side))
        print(f"[ACCEPT_STUDY_OUTCOME] {symbol} side={side} "
              f"5m={p5:+.3f}% 10m={p10:+.3f}% 30m={p30:+.3f}% "
              "model=next_full_minute_open_no_orders_no_costs",flush=True)
    except Exception as exc:
        attempts += 1
        http = getattr(getattr(exc,"response",None),"status_code",None)
        permanent = isinstance(exc,requests.HTTPError) and http in (400,401,403,404)
        state = "FAILED" if permanent or attempts >= RETRY_LIMIT else "PENDING"
        delay = min(3600,RECHECK_SEC*(2**min(attempts-1,4)))
        with _connect() as db:
            db.execute("""UPDATE accepts SET state=?,attempts=?,
                next_attempt_ts=?,error=?
                WHERE symbol=? AND bucket=? AND side=? AND state='PENDING'""",
                (state,attempts,time.time()+delay,
                 f"{type(exc).__name__}:{http or ''}"[:80],
                 symbol,bucket,side))
        print(f"[ACCEPT_STUDY_RETRY] {symbol} side={side} "
              f"status={state} attempts={attempts} "
              f"reason={type(exc).__name__}",flush=True)


def _cohort_report(now):
    from statistics import mean, median
    with _connect() as db:
        groups = {}
        for table in ("rejects","accepts"):
            rows = db.execute(f"""SELECT ts,side,p5,p10,p30 FROM {table}
                WHERE state='DONE' AND ts>=? AND p30 IS NOT NULL""",
                (now-TTL_SEC,)).fetchall()
            for ts,side,p5,p10,p30 in rows:
                for h,value in zip(HORIZONS,(p5,p10,p30)):
                    if value is not None and math.isfinite(float(value)):
                        groups.setdefault((table,side,h),[]).append(
                            (float(value),int(ts//86400)))
    for side in ("LONG","SHORT"):
        for horizon in HORIZONS:
            reject = groups.get(("rejects",side,horizon),[])
            accept = groups.get(("accepts",side,horizon),[])
            n_a,n_r = len(accept),len(reject)
            if not (n_a or n_r):
                continue
            n_days=len({d for _,d in accept+reject})
            mean_a = f"{mean(x for x,_ in accept):+.4f}" if n_a else "NA"
            mean_r = f"{mean(x for x,_ in reject):+.4f}" if n_r else "NA"
            median_a = f"{median(x for x,_ in accept):+.4f}" if n_a else "NA"
            median_r = f"{median(x for x,_ in reject):+.4f}" if n_r else "NA"
            print(
                f"[CANDIDATE_COHORT] side={side} horizon={horizon}m "
                f"sent_n={n_a} blocked_n={n_r} "
                f"sent_mean_gross={mean_a}% blocked_mean_gross={mean_r}% "
                f"sent_median_gross={median_a}% blocked_median_gross={median_r}% "
                f"days={n_days} "
                f"status={'COLLECTING' if min(n_a,n_r)<30 or n_days<7 else 'NEEDS_MATCHED_OOS'} "
                "model=next_full_minute_open_no_orders_no_costs "
                "cohorts_not_matched=true no_causal_filter_claim=true",
                flush=True)


def _edge_report(now):
    """Descriptive forward outcomes of previously BLOCKED candidates.

    These are overlapping groups and observational paper moves. They do NOT
    establish that relaxing an individual filter would improve trading.
    """
    from statistics import mean, median
    with _connect() as db:
        rows = db.execute("""SELECT ts,side,score,spot_conflict,oi_wait,
            smart_money,p5,p10,p30 FROM rejects
            WHERE state='DONE' AND ts>=? AND p30 IS NOT NULL""",
            (now-TTL_SEC,)).fetchall()
    grouped = {}
    for ts, side, score, spot_conflict, oi_wait, smart_money, p5,p10,p30 in rows:
        tags = ["ALL"]
        if spot_conflict:
            tags.append("SPOT_CONFLICT")
        if oi_wait:
            tags.append("OI_WAIT")
        if "WEAK" in (smart_money or "").upper():
            tags.append("WEAK_SMART_MONEY")
        if score >= 20:
            tags.append("SCORE_GE_20")
        for name in tags:
            for horizon, gross in zip(HORIZONS, (p5,p10,p30)):
                if gross is not None and math.isfinite(float(gross)):
                    grouped.setdefault((name,side,horizon),[]).append(
                        (float(gross), int(ts//86400))
                    )
    for (name,side,horizon), pairs in sorted(grouped.items()):
        n = len(pairs)
        if n < 10:
            continue
        moves = [v for v,_day in pairs]
        days = len({day for _v,day in pairs})
        # Cost is a fixed reference model, NOT an execution estimate.
        cost=0.22
        positive=sum(1 for v in moves if v > cost)
        print(
            f"[REJECT_EDGE] group={name} side={side} horizon={horizon}m "
            f"n={n} days={days} mean_gross={mean(moves):+.4f}% "
            f"median_gross={median(moves):+.4f}% "
            f"positive_after_ref_cost={positive}/{n} "
            f"reference_roundtrip_cost={cost:.2f}% "
            f"status={'COLLECTING' if n<100 or days<7 else 'NEEDS_OOS_COMPARISON'} "
            f"model=NEXT_FULL_1M_CLOSE_NO_ORDERS overlapping_groups=true",
            flush=True,
        )


def _cleanup(now):
    """Only our own study rows; never the existing signal journal."""
    with _connect() as db:
        db.execute("DELETE FROM rejects WHERE ts < ?", (now - TTL_SEC,))
        db.execute("""DELETE FROM rejects WHERE rowid IN
            (SELECT rowid FROM rejects ORDER BY ts DESC LIMIT -1 OFFSET ?)""",
                   (MAX_ROWS,))
        db.execute("DELETE FROM accepts WHERE ts < ?",(now-TTL_SEC,))
        db.execute("""DELETE FROM accepts WHERE rowid IN
            (SELECT rowid FROM accepts ORDER BY ts DESC LIMIT -1 OFFSET ?)""",
            (MAX_ROWS,))
        counts = dict(db.execute(
            "SELECT state, COUNT(*) FROM rejects GROUP BY state").fetchall())
        accepted_counts = dict(db.execute(
            "SELECT state, COUNT(*) FROM accepts GROUP BY state").fetchall())
    print(f"[REJECT_STUDY_SUMMARY] counts={counts} "
          f"max_rows={MAX_ROWS} retention_days=30", flush=True)
    print(f"[ACCEPT_STUDY_SUMMARY] counts={accepted_counts} "
          f"max_rows={MAX_ROWS} retention_days=30",flush=True)
    _edge_report(now)
    _cohort_report(now)


def _tick():
    now = time.time()
    with _connect() as db:
        _setup(db)
        pending = db.execute("""SELECT symbol,bucket,ts,side,attempts
            FROM rejects WHERE state='PENDING'
              AND next_attempt_ts <= ? AND ts <= ?
            ORDER BY ts ASC LIMIT ?""",
            (now, now-1950, BATCH_SIZE)).fetchall()
        accepted = db.execute("""SELECT symbol,bucket,ts,side,attempts
            FROM accepts WHERE state='PENDING'
              AND next_attempt_ts <= ? AND ts <= ?
            ORDER BY ts ASC LIMIT 1""",(now,now-1950)).fetchall()
    for row in pending:
        _evaluate(row)
    for row in accepted:
        _evaluate_accepted(row)
    return len(pending)+len(accepted)


def _worker():
    last_cleanup = 0
    while True:
        try:
            now = time.time()
            if now - last_cleanup >= 3600:
                _cleanup(now)
                last_cleanup = now
            _tick()
        except Exception as exc:
            print(f"[REJECT_STUDY_ERROR] stage=worker "
                  f"kind={type(exc).__name__}", flush=True)
        time.sleep(RECHECK_SEC)
