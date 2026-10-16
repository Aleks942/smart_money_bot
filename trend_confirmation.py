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
READY_DAILY_MAX = 5  # hard cap for informational technical trade plans
if POLICY not in ("CONFIRMED_ONLY", "LEGACY"):
    POLICY = "CONFIRMED_ONLY"

HORIZON_MIN = 30
TP_PCT, SL_PCT, COST_PCT = 1.5, 1.0, 0.22
MIN_N, MIN_DAYS, MIN_SYMBOLS = 80, 5, 15
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
        if a == b and a in ("UP","DOWN"):
            result=a
        elif a in ("UP","DOWN") and b == "FLAT":
            # Modest relaxation: strong closed H1, genuinely flat closed H4.
            # Keep this regime's evidence separate from full H1/H4 alignment.
            result=f"{a}_H4_FLAT"
        else:
            result="UNKNOWN"
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
    if current not in (direction,f"{direction}_H4_FLAT"):
        print(f"[TREND_GATE_SKIP] {symbol} side={side} trend={current} "
              "reason=TREND_NOT_ALIGNED",flush=True)
        return False,"TREND_NOT_ALIGNED"
    band="GE20" if score>=20 else "LT20"
    if current==f"{direction}_H4_FLAT":
        print(f"[TREND_H4_FLAT_RESEARCH] {symbol} side={side} "
              "separate_cohort=true original_gate_preserved=true",flush=True)
    try:
        with _connect() as db:
            _setup(db)
            odds=_odds(db,side,current,band,now)
            bucket=int(now//(90*60))
            db.execute("""INSERT OR IGNORE INTO trend_gate_samples
                (symbol,side,bucket,ts,band,trend) VALUES (?,?,?,?,?,?)""",
                (symbol,side,bucket,now,band,current))
        # Side research never approves or blocks a Telegram signal.
        try:
            from horizon_audit import record as record_horizon_case
            record_horizon_case(sig, now)
        except Exception as audit_exc:
            print("[HORIZON_AUDIT_ERROR] stage=record_hook kind=" +
                  type(audit_exc).__name__,flush=True)
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


def regime_quality_snapshot(db):
    """Descriptive forward-sample statistics, never a Telegram permission.

    Bands are pooled for diagnostic presentation only. _odds continues to
    validate each exact side/trend/score-band cohort independently.
    """
    data=db.execute("""
        SELECT trend,side,COUNT(*),
               SUM(CASE WHEN status='DONE' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='MISSING' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='DONE' AND verdict='TP_FIRST' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='DONE' AND verdict='SL_FIRST' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='DONE' AND verdict='TIME_EXIT' THEN 1 ELSE 0 END),
               SUM(CASE WHEN status='DONE' AND net_pct>0 THEN 1 ELSE 0 END),
               AVG(CASE WHEN status='DONE' THEN net_pct END),
               COUNT(DISTINCT CASE WHEN status='DONE' THEN CAST(ts/86400 AS INTEGER) END),
               COUNT(DISTINCT CASE WHEN status='DONE' THEN symbol END)
        FROM trend_gate_samples
        WHERE trend IN ('UP','DOWN','UP_H4_FLAT','DOWN_H4_FLAT')
        GROUP BY trend,side
        ORDER BY trend,side
    """).fetchall()
    result=[]
    for (trend,side,seen,done,pending,missing,tp,sl,timeout,positive,
         avg_net,days,symbols) in data:
        result.append({
            "trend":trend, "side":side,
            "regime":"H1_H4_FLAT" if trend.endswith("_H4_FLAT") else "H1_H4_ALIGNED",
            "seen":int(seen), "done":int(done or 0),
            "pending":int(pending or 0), "missing":int(missing or 0),
            "tp":int(tp or 0), "sl":int(sl or 0),
            "time_exit":int(timeout or 0), "positive":int(positive or 0),
            "mean_net":float(avg_net) if avg_net is not None else None,
            "days":int(days), "symbols":int(symbols),
        })
    return result


def report_regime_quality(db):
    for x in regime_quality_snapshot(db):
        avg="NA" if x["mean_net"] is None else f'{x["mean_net"]:+.4f}%'
        print(f'[TREND_REGIME_QUALITY] regime={x["regime"]} '
              f'trend={x["trend"]} side={x["side"]} '
              f'seen={x["seen"]} done={x["done"]} '
              f'pending={x["pending"]} missing={x["missing"]} '
              f'tp={x["tp"]} sl={x["sl"]} time={x["time_exit"]} '
              f'positive_net={x["positive"]} mean_net={avg} '
              f'days={x["days"]} symbols={x["symbols"]} '
              'pooled_bands=true descriptive_only=true '
              'no_trade_approval=true model=first_touch_after_0p22pct_cost',
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
            report_regime_quality(db)
        print(f"[TREND_GATE_SUMMARY] counts={counts} max_rows={MAX_SAMPLES} "
              "reference_cost_pct=0.22 "
              "insufficient_odds_means_silence=true",flush=True)
    except (sqlite3.Error,OSError) as exc:
        print(f"[TREND_GATE_ERROR] stage=report kind={type(exc).__name__}",
              flush=True)


def _ready_skip(sig, reason):
    """Log why a technically valid research candidate is not yet a trade plan."""
    data = sig if isinstance(sig, dict) else {}
    print(
        "[SM_READY_REJECT] symbol={} reason={} score={} acc={} ep={} "
        "mode={} entry={} stop={} target={} oi={}".format(
            data.get("instId") or data.get("symbol") or "?",
            reason, data.get("score"), data.get("acc_score"),
            data.get("early_pressure_score"), data.get("signal_mode"),
            data.get("entry_type") or data.get("entry"),
            data.get("stop"), data.get("tp1") or data.get("target"),
            data.get("oi_change")),
        flush=True,
    )
    return None


def ready_candidate(sig, now=None):
    """Technical plan: valid levels and context, not proof of profit."""
    if not isinstance(sig, dict):
        return _ready_skip(sig, "bad_signal")
    symbol = str(sig.get("instId") or sig.get("symbol") or "")
    side = _side(sig)
    direction = "UP" if side == "LONG" else "DOWN" if side == "SHORT" else None
    if not symbol.endswith("USDT") or direction is None:
        return _ready_skip(sig, "no_symbol_or_side")
    if str(sig.get("direction_code") or "").upper() != direction:
        return _ready_skip(sig, "direction_conflict")
    kind = str(sig.get("entry_type") or sig.get("entry") or "").upper()
    if ("LONG" if side == "LONG" else "SHORT") not in kind:
        return _ready_skip(sig, "no_directional_entry")
    score = _finite(sig.get("score"))
    acc = _finite(sig.get("acc_score"))
    ep = _finite(sig.get("early_pressure_score"))
    if score is None or score < 14 or acc is None or ep is None:
        return _ready_skip(sig, "score_below_14_or_missing_features")
    if acc < 2 and ep < 10 or sig.get("late") is True:
        return _ready_skip(sig, "weak_pressure_or_late")
    flags = set(sig.get("flags") or [])
    pressure = {"PRESSURE_UP","BUYER_ABSORPTION"} if direction=="UP" else {"PRESSURE_DOWN","SELLER_ABSORPTION"}
    structure = {"STRUCTURE_HH_HL","MTF_LONG_ALIGN","BREAKOUT_CONFIRM_UP","CONTINUATION_UP"} if direction=="UP" else {"STRUCTURE_LH_LL","MTF_SHORT_ALIGN","BREAKOUT_CONFIRM_DOWN","CONTINUATION_DOWN"}
    conflict = {"PRESSURE_DOWN","BREAKOUT_CONFIRM_DOWN"} if direction=="UP" else {"PRESSURE_UP","BREAKOUT_CONFIRM_UP"}
    if not flags.intersection(pressure) or not flags.intersection(structure) or flags.intersection(conflict):
        return _ready_skip(sig, "pressure_structure_conflict")
    if sig.get("signal_mode") not in ("EXPANSION","PREMOVE","TRANSITION"):
        return _ready_skip(sig, "unsupported_stage")
    entry, last = _finite(sig.get("entry_price")), _finite(sig.get("price"))
    stop = _finite(sig.get("stop"))
    target = _finite(sig.get("tp1") or sig.get("target"))
    oi = _finite(sig.get("oi_change"))
    if any(v is None or v <= 0 for v in (entry,last,stop,target)):
        return _ready_skip(sig, "missing_levels")
    if oi is None or oi < -0.30 or abs(last/entry-1) > .008:
        return _ready_skip(sig, "oi_or_entry_distance")
    sign = 1 if direction == "UP" else -1
    risk, reward = sign*(entry-stop), sign*(target-entry)
    if risk <= 0 or reward <= 0 or not .3 <= risk/entry*100 <= 3 or reward/risk < 1.8:
        return _ready_skip(sig, "risk_reward_or_level_order")
    return dict(symbol=symbol,side=side,entry=entry,stop=stop,tp1=target,
                tp2=entry+2.5*sign*risk,rr=reward/risk,score=score,
                oi=oi,risk_pct=risk/entry*100)


def ready_message(p):
    return "\n".join((
        f"🎯 SMART MONEY | ГОТОВЫЙ ПЛАН {p['side']}",
        f"🪙 {p['symbol']} | Bybit USDT perpetual",
        f"Вход: {p['entry']:.9g}",
        f"Стоп: {p['stop']:.9g}",
        f"TP1: {p['tp1']:.9g}",
        f"TP2 (расчётные 2.5R): {p['tp2']:.9g}",
        f"R/R: {p['rr']:.2f} | Риск до стопа: {p['risk_pct']:.2f}%",
        f"Score: {p['score']:g} | OI: {p['oi']:+.2f}%",
        "⚠️ Это технический торговый план, не доказанная прибыльная стратегия.",
        "Позиция не открывается автоматически. Проверяй ликвидность и размер риска."
    ))


_ready_send_lock = __import__("threading").Lock()


def dispatch_ready(sig, sender, now=None):
    """Max five Warsaw-day plans, >=45m apart and one per symbol per 12h."""
    now = time.time() if now is None else float(now)
    plan = ready_candidate(sig, now)
    if plan is None:
        return False
    side = "UP" if plan["side"] == "LONG" else "DOWN"
    actual_trend = trend(plan["symbol"], now)
    if actual_trend not in (side, side+"_H4_FLAT"):
        print(f"[SM_READY_REJECT] symbol={plan['symbol']} "
              f"reason=trend_mismatch trend={actual_trend} side={side}",
              flush=True)
        return False
    if not callable(sender):
        return False
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import json
    from pathlib import Path
    path = Path(os.getenv("READY_ALERTS_STATE_FILE", "/data/smart_money_ready.json"))
    day = datetime.fromtimestamp(now, ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    with _ready_send_lock:
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            return False
        if state.get("day") != day:
            state = {"day":day,"count":0,"last":0,"symbols":{}}
        previous = state.get("symbols") or {}
        if int(state["count"]) >= READY_DAILY_MAX:
            return False
        if now-float(state["last"]) < 2700 or now-float(previous.get(plan["symbol"],0)) < 43200:
            return False
        try:
            accepted = sender(ready_message(plan))
        except Exception as exc:
            print("[SM_READY_TG_ERROR]",type(exc).__name__,flush=True)
            return False
        if accepted is not True:
            return False
        state["count"] += 1
        state["last"] = now
        previous[plan["symbol"]] = now
        state["symbols"] = previous
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state))
            os.replace(temp,path)
        except OSError as exc:
            print("[SM_READY_STATE_ERROR]",type(exc).__name__,flush=True)
        print(f"[SM_READY_SENT] {plan['symbol']} count={state['count']} edge_proven=false",flush=True)
        return True
