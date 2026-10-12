"""SmartMoney trend-first, evidence-verified trade Telegram gate.

Strict default: if the trend or prospective first-touch odds are unproven,
DO NOT SEND. Research remains in the existing SQLite rejection-study DB.
One candidate per symbol+side+90m; no new worker, no heavy ML, no orders.
H1/H4 trends use only *fully CLOSED* consecutive Bybit futures candles.
Historical accepted/rejected close returns are NOT used as TP/SL odds.
"""
import math
import os
import sqlite3
import time
from collections import OrderedDict
from statistics import NormalDist

import requests

from rejection_forward_study import _connect, API, _parse_candles

POLICY = os.getenv("SMARTMONEY_TELEGRAM_POLICY", "CONFIRMED_ONLY").strip().upper()
if POLICY not in ("CONFIRMED_ONLY", "LEGACY"):
    POLICY = "CONFIRMED_ONLY"

HORIZON_MIN = 30
TP_PCT, SL_PCT, COST_PCT = 1.5, 1.0, 0.22
MIN_N, MIN_DAYS, MIN_SYMBOLS = 100, 7, 20
MIN_PROB_BUFFER = 0.03
MAX_SAMPLES = 2400
RETENTION_SEC = 30 * 86400
MAX_WORK = 1
MAX_RETRIES = 5
CACHE_TTL = 15 * 60
MAX_CACHED = 128
_trend_cache = OrderedDict()
_last_report_ts = 0.0


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS trend_gate_samples (
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        bucket INTEGER NOT NULL,
        ts REAL NOT NULL,
        band TEXT NOT NULL,
        trend TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        verdict TEXT,
        net_pct REAL,
        evaluated_ts REAL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_retry REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (symbol,side,bucket)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_trend_gate_pending
        ON trend_gate_samples(status,next_retry,ts)""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_trend_gate_cohort
        ON trend_gate_samples(side,trend,band,status,ts)""")


def _finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _side(sig):
    code = str(sig.get("direction_code") or "").upper()
    side = {"UP":"LONG","LONG":"LONG","BUY":"LONG",
            "DOWN":"SHORT","SHORT":"SHORT","SELL":"SHORT"}.get(code)
    if side:
        return side
    entry = str(sig.get("entry_type") or sig.get("entry") or "").upper()
    if "LONG" in entry and "SHORT" not in entry:
        return "LONG"
    if "SHORT" in entry and "LONG" not in entry:
        return "SHORT"
    return None


def _ema(closes, period):
    if len(closes) < period:
        return None
    prev = sum(closes[:period])/period
    values = [prev]
    alpha = 2/(period+1)
    for close in closes[period:]:
        prev += alpha*(close-prev)
        values.append(prev)
    return values


def _closed_trend(payload, minutes, now):
    try:
        if payload.get("retCode") != 0:
            return "UNKNOWN"
        rows = (payload.get("result") or {}).get("list") or []
        period_ms = minutes * 60000
        candles = {}
        for row in rows:
            ts = int(row[0])
            close = _finite(row[4])
            if close is not None and close > 0 and ts + period_ms <= now*1000:
                candles[ts] = close
        stamps = sorted(candles)
        if (len(stamps) < 57 or
                now*1000 - (stamps[-1]+period_ms) > 2*period_ms+120000):
            return "UNKNOWN"
        if any(stamps[k]-stamps[k-1] != period_ms
               for k in range(len(stamps)-56,len(stamps))):
            return "UNKNOWN"
        closes = [candles[ts] for ts in stamps[-95:]]
        fast, slow = _ema(closes,20), _ema(closes,50)
        if fast is None or slow is None or len(fast) < 4:
            return "UNKNOWN"
        if closes[-1] > fast[-1] > slow[-1] and fast[-1] > fast[-4]:
            return "UP"
        if closes[-1] < fast[-1] < slow[-1] and fast[-1] < fast[-4]:
            return "DOWN"
        return "FLAT"
    except (AttributeError,TypeError,ValueError,IndexError,OverflowError):
        return "UNKNOWN"


def _fetch_trend(symbol, interval, now):
    response=requests.get(
        API,
        params={"category":"linear","symbol":symbol,
                "interval":str(interval),"limit":100},
        timeout=8,
    )
    response.raise_for_status()
    return _closed_trend(response.json(),interval,now)


def trend(symbol, now=None):
    now=time.time() if now is None else float(now)
    cached=_trend_cache.get(symbol)
    if cached is not None and 0 <= now-cached[0] < CACHE_TTL:
        _trend_cache.move_to_end(symbol)
        return cached[1]
    try:
        a=_fetch_trend(symbol,60,now)
        b=_fetch_trend(symbol,240,now)
        result=a if a == b and a in ("UP","DOWN") else "UNKNOWN"
    except (requests.RequestException,ValueError,TypeError,OverflowError):
        result="UNKNOWN"
    _trend_cache[symbol]=(now,result)
    _trend_cache.move_to_end(symbol)
    while len(_trend_cache)>MAX_CACHED:
        _trend_cache.popitem(last=False)
    return result


def _lower_win_bound(wins,n):
    if n <= 0:
        return 0.
    z=NormalDist().inv_cdf(0.95)   # one-sided Wilson lower bound
    p=wins/n
    z2=z*z
    return max(0.,(p+z2/(2*n)-z*math.sqrt((p*(1-p)+z2/(4*n))/n))
               /(1+z2/n))


def _odds(db,side,trend,band,now):
    # Same-day results are never used to justify another same-day alert.
    cutoff=min(now-86400, int(now//86400)*86400)
    count,wins,days,symbols=db.execute("""SELECT COUNT(*),
        COALESCE(SUM(verdict='TP_FIRST'),0),
        COUNT(DISTINCT CAST(ts/86400 AS INTEGER)),
        COUNT(DISTINCT symbol)
        FROM trend_gate_samples
        WHERE side=? AND trend=? AND band=? AND status='DONE'
          AND ts >= ? AND evaluated_ts < ?""",
        (side,trend,band,now-RETENTION_SEC,cutoff)).fetchone()
    bound=_lower_win_bound(wins,count)
    threshold=(SL_PCT+COST_PCT)/(TP_PCT+SL_PCT)
    lower_ev=bound*(TP_PCT-COST_PCT)-(1-bound)*(SL_PCT+COST_PCT)
    approved=(count>=MIN_N and days>=MIN_DAYS and symbols>=MIN_SYMBOLS
              and bound>threshold+MIN_PROB_BUFFER and lower_ev>0)
    return {"n":count,"wins":wins,"days":days,"symbols":symbols,
            "lower_p":bound,"threshold":threshold,"lower_ev":lower_ev,
            "approved":approved}


def consider(sig, now=None):
    """Return (allowed, reason); record only valid trend-aligned candidates."""
    if POLICY=="LEGACY":
        return True,"LEGACY"
    now=time.time() if now is None else float(now)
    symbol=str(sig.get("instId") or sig.get("symbol") or "").upper()
    side=_side(sig)
    score=_finite(sig.get("score"))
    if (not symbol.endswith("USDT") or len(symbol)>35 or not side
            or score is None or score < 0):
        return False,"INVALID_CANDIDATE"
    direction="UP" if side=="LONG" else "DOWN"
    current=trend(symbol,now)
    if current!=direction:
        print(f"[TREND_GATE_SKIP] {symbol} side={side} trend={current} "
              "reason=TREND_NOT_ALIGNED",flush=True)
        return False,"TREND_NOT_ALIGNED"
    band="GE20" if score>=20 else "LT20"
    try:
        with _connect() as db:
            _setup(db)
            odds=_odds(db,side,direction,band,now)
            bucket=int(now//(90*60))
            db.execute("""INSERT OR IGNORE INTO trend_gate_samples
                (symbol,side,bucket,ts,band,trend) VALUES (?,?,?,?,?,?)""",
                (symbol,side,bucket,now,band,direction))
        print(f"[TREND_GATE_ODDS] {symbol} side={side} band={band} "
              f"n={odds['n']} wins={odds['wins']} days={odds['days']} "
              f"symbols={odds['symbols']} lower_p={odds['lower_p']:.4f} "
              f"needed_p={odds['threshold']:.4f} "
              f"lower_ev={odds['lower_ev']:+.4f}% "
              f"approved={odds['approved']} model=first_touch_no_orders",flush=True)
        return (True,"CONFIRMED") if odds["approved"] else (False,"EDGE_UNPROVEN")
    except (sqlite3.Error,OSError,TypeError,ValueError) as exc:
        print(f"[TREND_GATE_ERROR] stage=consider kind={type(exc).__name__}",
              flush=True)
        return False,"RESEARCH_UNAVAILABLE"


def _first_touch(bars,side):
    if len(bars)!=HORIZON_MIN:
        return None,None
    sign=1 if side=="LONG" else -1
    entry=bars[0][1]
    tp=entry*(1+sign*TP_PCT/100)
    sl=entry*(1-sign*SL_PCT/100)
    for _ts,_op,hi,lo,_close in bars:
        if (lo<=sl if sign==1 else hi>=sl):
            return "SL_FIRST", -SL_PCT-COST_PCT
        if (hi>=tp if sign==1 else lo<=tp):
            return "TP_FIRST", TP_PCT-COST_PCT
    value=sign*(bars[-1][4]/entry-1)*100 - COST_PCT
    return "TIME_EXIT",value


def advance(now=None):
    """Runs within existing rejection-study worker; max 1 extra Bybit call/2m."""
    now=time.time() if now is None else float(now)
    try:
        with _connect() as db:
            _setup(db)
            db.execute("DELETE FROM trend_gate_samples WHERE ts < ?",
                       (now-RETENTION_SEC,))
            db.execute("""DELETE FROM trend_gate_samples WHERE rowid IN
                (SELECT rowid FROM trend_gate_samples ORDER BY ts DESC
                 LIMIT -1 OFFSET ?)""",(MAX_SAMPLES,))
            rows=db.execute("""SELECT symbol,side,bucket,ts,attempts FROM
                trend_gate_samples WHERE status='PENDING' AND ts < ?
                AND next_retry <= ? ORDER BY ts LIMIT ?""",
                (now-(HORIZON_MIN+3)*60,now,MAX_WORK)).fetchall()
        for symbol,side,bucket,ts,attempts in rows:
            if now-ts>5*3600:
                with _connect() as db:
                    db.execute("""UPDATE trend_gate_samples SET status='MISSING'
                        WHERE symbol=? AND side=? AND bucket=? AND status='PENDING'""",
                        (symbol,side,bucket))
                continue
            first=(int(ts//60)+1)*60000
            try:
                response=requests.get(API,params={
                    "category":"linear","symbol":symbol,"interval":"1",
                    "start":first,"end":first+HORIZON_MIN*60000-1,
                    "limit":HORIZON_MIN},timeout=8)
                response.raise_for_status()
                bars=_parse_candles(response.json(),first)
                verdict,net=_first_touch(bars,side)
                with _connect() as db:
                    db.execute("""UPDATE trend_gate_samples SET status='DONE',
                        verdict=?,net_pct=?,evaluated_ts=?
                        WHERE symbol=? AND side=? AND bucket=? AND status='PENDING'""",
                        (verdict,net,now,symbol,side,bucket))
                print(f"[TREND_GATE_OUTCOME] {symbol} side={side} "
                      f"verdict={verdict} net={net:+.4f}% "
                      "model=first_touch_next_full_1m_no_orders",flush=True)
            except Exception as exc:
                attempts+=1
                is_permanent=(isinstance(exc,requests.HTTPError)
                              and getattr(getattr(exc,"response",None),"status_code",None)
                                  in (400,401,403,404))
                state="MISSING" if is_permanent or attempts>=MAX_RETRIES else "PENDING"
                with _connect() as db:
                    db.execute("""UPDATE trend_gate_samples SET status=?,
                        attempts=?,next_retry=?
                        WHERE symbol=? AND side=? AND bucket=? AND status='PENDING'""",
                        (state,attempts,now+min(3600,120*2**min(attempts,4)),
                         symbol,side,bucket))
                print(f"[TREND_GATE_RETRY] {symbol} attempts={attempts} "
                      f"status={state} kind={type(exc).__name__}",flush=True)
    except (sqlite3.Error,OSError) as exc:
        print(f"[TREND_GATE_ERROR] stage=advance kind={type(exc).__name__}",
              flush=True)


def report(now=None):
    global _last_report_ts
    now=time.time() if now is None else float(now)
    if now-_last_report_ts<3600:
        return
    _last_report_ts=now
    try:
        with _connect() as db:
            _setup(db)
            counts=dict(db.execute("""SELECT status,COUNT(*) FROM
                trend_gate_samples GROUP BY status""").fetchall())
        print(f"[TREND_GATE_SUMMARY] counts={counts} max_rows={MAX_SAMPLES} "
              "reference_cost_pct=0.22 "
              "insufficient_odds_means_silence=true",flush=True)
    except (sqlite3.Error,OSError) as exc:
        print(f"[TREND_GATE_ERROR] stage=report kind={type(exc).__name__}",
              flush=True)
