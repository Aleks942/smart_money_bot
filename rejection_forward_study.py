"""Bounded research-only forward study of FINAL_INVALID candidates.
No trading decisions, alerts, or orders are changed.
"""
import os
import sqlite3
import threading
import time
import math
import requests

DB = os.getenv("REJECTION_STUDY_DB", "/data/rejection_study.sqlite3")
API = "https://api.bybit.com/v5/market/kline"
_lock = threading.Lock()
_started = False

def _connect():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS rejects (
        symbol TEXT NOT NULL, bucket INTEGER NOT NULL, ts REAL NOT NULL,
        side TEXT NOT NULL, price REAL NOT NULL, score REAL NOT NULL,
        ep REAL NOT NULL, acc REAL NOT NULL, flow TEXT, smart_money TEXT,
        spot_conflict INTEGER NOT NULL, oi_wait INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING',
        p5 REAL, p10 REAL, p30 REAL, error TEXT,
        PRIMARY KEY(symbol,bucket,side)
    )""")

def record(symbol, side, price, score, ep, acc, flow, smart_money, flags):
    """At most one rejected setup per symbol/side per 30 minutes."""
    global _started
    try:
        price = float(price)
        if side not in ("LONG", "SHORT") or not math.isfinite(price) or price <= 0:
            return
        now = time.time()
        with _lock:
            with _connect() as db:
                _setup(db)
                db.execute("""INSERT OR IGNORE INTO rejects
                    (symbol,bucket,ts,side,price,score,ep,acc,flow,
                     smart_money,spot_conflict,oi_wait)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    symbol, int(now // 1800), now, side, price,
                    float(score), float(ep), float(acc), str(flow),
                    str(smart_money), int("SPOT_CONFLICT_LONG" in flags or
                        "SPOT_CONFLICT_SHORT" in flags),
                    int("SMART_MONEY_WAIT_OI_CONFIRM" in flags)
                ))
                db.execute("""DELETE FROM rejects WHERE ts < ?""",
                           (now - 30 * 86400,))
                db.execute("""DELETE FROM rejects WHERE rowid IN (
                  SELECT rowid FROM rejects ORDER BY ts DESC LIMIT -1 OFFSET 5000
                )""")
            if not _started:
                threading.Thread(target=_worker, name="rejection-study",
                                 daemon=True).start()
                _started = True
    except Exception as exc:
        print(f"[REJECT_STUDY_ERROR] stage=record kind={type(exc).__name__}",
              flush=True)

def _evaluate(row):
    symbol, bucket, ts, side = row
    first = (int(ts // 60) + 1) * 60_000
    try:
        response = requests.get(API, params={
            "category": "linear", "symbol": symbol, "interval": "1",
            "start": first, "end": first + 30*60_000-1, "limit": 30
        }, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("retCode") != 0:
            raise ValueError("BYBIT_RESPONSE")
        rows = sorted(data["result"]["list"], key=lambda x: int(x[0]))
        if len(rows) != 30 or any(int(x[0]) != first+i*60000
                                   for i,x in enumerate(rows)):
            raise ValueError("INCOMPLETE_CANDLES")
        start_price = float(rows[0][1])
        sign = 1 if side == "LONG" else -1
        if start_price <= 0:
            raise ValueError("INVALID_ENTRY")
        results = [round(sign*(float(rows[i-1][4])/start_price-1)*100, 4)
                   for i in (5,10,30)]
        with _connect() as db:
            db.execute("""UPDATE rejects SET state='DONE',
                p5=?,p10=?,p30=?,error=NULL
                WHERE symbol=? AND bucket=? AND side=?""",
                (*results, symbol,bucket,side))
        print(f"[REJECT_STUDY_OUTCOME] {symbol} {side} "
              f"5m={results[0]:+.3f}% 10m={results[1]:+.3f}% "
              f"30m={results[2]:+.3f}% model=no_orders", flush=True)
    except Exception as exc:
        print(f"[REJECT_STUDY_RETRY] {symbol} kind={type(exc).__name__}",
              flush=True)

def _worker():
    while True:
        try:
            with _connect() as db:
                _setup(db)
                pending = db.execute("""SELECT symbol,bucket,ts,side FROM rejects
                    WHERE state='PENDING' AND ts <= ?
                    ORDER BY ts LIMIT 2""", (time.time()-1950,)).fetchall()
            for row in pending:
                _evaluate(row)
        except Exception as exc:
            print(f"[REJECT_STUDY_ERROR] stage=worker kind={type(exc).__name__}",
                  flush=True)
        time.sleep(120)
