"""Forward audit of actually Telegram-accepted Smart Money ready plans.

Observational ONLY: Bybit USDT perpetual, next fully closed 1m bars, 6h horizon,
entry must be touched, TP/SL evaluated with conservative intrabar ambiguity.
No brokerage fills, no claims of guaranteed executions or profitability.
"""
import math
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

DB = os.getenv("READY_OUTCOME_DB", "/data/smartmoney_ready_outcomes.db")
API = "https://api.bybit.com/v5/market/kline"
HORIZON_MIN = 360
COST_PCT = 0.22  # reference roundtrip fee estimate; excludes variable slippage
RETRY_LIMIT = 10
_lock = threading.Lock()
_started = False
_last_summary_ts = 0.0


@contextmanager
def _connect():
    path = Path(os.getenv("READY_OUTCOME_DB", DB))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=6)
    try:
        db.execute("PRAGMA busy_timeout=6000")
        db.execute("PRAGMA journal_mode=WAL")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _setup(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ready_plan_outcomes(
        symbol TEXT NOT NULL, side TEXT NOT NULL, sent_minute INTEGER NOT NULL,
        sent_at REAL NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL,
        tp1 REAL NOT NULL, tp2 REAL NOT NULL, score REAL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        verdict TEXT, filled_at REAL, resolved_at REAL, exit_price REAL,
        minutes_to_exit INTEGER, gross_pct REAL, net_pct REAL,
        attempts INTEGER NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
        error TEXT, PRIMARY KEY(symbol, side, sent_minute)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS idx_ready_pending
        ON ready_plan_outcomes(status,next_retry,sent_at)""")
    db.execute("""CREATE TABLE IF NOT EXISTS ready_plan_report_days(
        day TEXT PRIMARY KEY, delivered_at REAL NOT NULL
    )""")


def record_sent(plan, sent_at=None):
    """Journal only after Telegram ACK; never fabricate past alert levels."""
    sent_at = time.time() if sent_at is None else float(sent_at)
    symbol, side = str(plan["symbol"]), str(plan["side"])
    if (not symbol.endswith("USDT") or side not in ("LONG", "SHORT")
            or not math.isfinite(sent_at) or sent_at <= 0):
        raise ValueError("invalid trade identity")
    fields = [float(plan[k]) for k in ("entry", "stop", "tp1", "tp2")]
    if not all(math.isfinite(x) and x > 0 for x in fields):
        raise ValueError("invalid levels")
    sign = 1 if side == "LONG" else -1
    if not (sign * (fields[0]-fields[1]) > 0
            and sign * (fields[2]-fields[0]) > 0
            and sign * (fields[3]-fields[0]) > 0):
        raise ValueError("invalid level ordering")
    with _lock:
        with _connect() as db:
            _setup(db)
            cursor = db.execute("""INSERT OR IGNORE INTO ready_plan_outcomes
                (symbol,side,sent_minute,sent_at,entry,stop,tp1,tp2,score)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (symbol,side,int(sent_at//60),sent_at,*fields,
                 float(plan.get("score",0))))
    print(f"[SM_READY_JOURNAL] {symbol} side={side} "
          f"stored={bool(cursor.rowcount)} sent_at={int(sent_at)}", flush=True)
    return bool(cursor.rowcount)


def _parse_bars(payload, first_ms):
    if not isinstance(payload,dict) or payload.get("retCode") != 0:
        raise ValueError("BYBIT_BAD_RESPONSE")
    raw = (payload.get("result") or {}).get("list")
    if not isinstance(raw,list) or len(raw) != HORIZON_MIN:
        raise ValueError("INCOMPLETE_HISTORY")
    bars=[]
    for item in raw:
        if not isinstance(item,list) or len(item) < 5:
            raise ValueError("INVALID_KLINE")
        ts = int(item[0])
        op,hi,lo,close = (float(item[i]) for i in (1,2,3,4))
        if (not all(math.isfinite(v) for v in (op,hi,lo,close))
                or lo <= 0 or lo > min(op,close)
                or max(op,close) > hi):
            raise ValueError("INVALID_OHLC")
        bars.append((ts,op,hi,lo,close))
    bars.sort(key=lambda x:x[0])
    if any(b[0] != first_ms+i*60000 for i,b in enumerate(bars)):
        raise ValueError("GAPPED_HISTORY")
    return bars


def evaluate(side, entry, stop, tp, bars, cost_pct=COST_PCT):
    """First touch after an actually touched entry; unknown order is ambiguous."""
    if side not in ("LONG","SHORT") or not bars:
        raise ValueError("invalid evaluation")
    sign=1 if side=="LONG" else -1
    entered=None
    for idx,bar in enumerate(bars):
        ts,op,hi,lo,close=bar
        if entered is None:
            if lo <= entry <= hi:
                entered=idx
                # Entry and exit touched inside one minute: order is unknowable.
                hit_sl = lo <= stop if sign==1 else hi >= stop
                hit_tp = hi >= tp if sign==1 else lo <= tp
                if hit_sl or hit_tp:
                    return dict(verdict="AMBIGUOUS_ENTRY_BAR",
                                filled_at=ts/1000, exit_price=None,
                                minutes_to_exit=None, gross_pct=None,
                                net_pct=None)
            continue
        hit_sl = lo <= stop if sign==1 else hi >= stop
        hit_tp = hi >= tp if sign==1 else lo <= tp
        if hit_sl and hit_tp:
            return dict(verdict="AMBIGUOUS_BOTH",
                        filled_at=bars[entered][0]/1000,exit_price=None,
                        minutes_to_exit=None,gross_pct=None,net_pct=None)
        if hit_sl:
            # Gaps through stop are not granted an unrealistically better fill.
            price=min(op,stop) if sign==1 else max(op,stop)
            verdict="SL_FIRST"
        elif hit_tp:
            price=tp
            verdict="TP1_FIRST"
        else:
            continue
        gross=sign*(price/entry-1)*100
        return dict(verdict=verdict,filled_at=bars[entered][0]/1000,
                    exit_price=price,minutes_to_exit=idx-entered,
                    gross_pct=gross,net_pct=gross-cost_pct)
    if entered is None:
        return dict(verdict="NOT_FILLED",filled_at=None,exit_price=None,
                    minutes_to_exit=None,gross_pct=None,net_pct=None)
    price=bars[-1][4]
    gross=sign*(price/entry-1)*100
    return dict(verdict="TIME_EXIT_6H",filled_at=bars[entered][0]/1000,
                exit_price=price,minutes_to_exit=len(bars)-1-entered,
                gross_pct=gross,net_pct=gross-cost_pct)


def _fetch(symbol,sent_at):
    first_ms=(int(sent_at//60)+1)*60000
    response=requests.get(API,params={
        "category":"linear","symbol":symbol,"interval":"1",
        "start":first_ms,"end":first_ms+HORIZON_MIN*60000-1,
        "limit":HORIZON_MIN},timeout=12)
    response.raise_for_status()
    return _parse_bars(response.json(),first_ms)


def advance(now=None):
    """Process at most one matured plan per iteration; bounded exchange work."""
    now=time.time() if now is None else float(now)
    with _connect() as db:
        _setup(db)
        row=db.execute("""SELECT symbol,side,sent_minute,sent_at,entry,stop,tp1,
            attempts FROM ready_plan_outcomes
            WHERE status='PENDING' AND next_retry<=?
            AND sent_at+?<=? ORDER BY sent_at LIMIT 1""",
            (now,(HORIZON_MIN+3)*60,now)).fetchone()
    if row is None:
        return False
    symbol,side,bucket,ts,entry,stop,tp,attempts=row
    try:
        bars=_fetch(symbol,ts)
        outcome=evaluate(side,entry,stop,tp,bars)
        with _connect() as db:
            _setup(db)
            db.execute("""UPDATE ready_plan_outcomes SET status='DONE',
                verdict=?, filled_at=?, resolved_at=?, exit_price=?,
                minutes_to_exit=?, gross_pct=?, net_pct=?, error=NULL
                WHERE symbol=? AND side=? AND sent_minute=? AND status='PENDING'""",
                (outcome["verdict"],outcome["filled_at"],now,
                 outcome["exit_price"],outcome["minutes_to_exit"],
                 outcome["gross_pct"],outcome["net_pct"],symbol,side,bucket))
        net="NA" if outcome["net_pct"] is None else f'{outcome["net_pct"]:+.3f}%'
        print(f"[SM_READY_OUTCOME] symbol={symbol} side={side} "
              f"verdict={outcome['verdict']} net={net} "
              f"minutes={outcome['minutes_to_exit']} model=6h_first_touch_no_orders",
              flush=True)
        return True
    except (requests.RequestException,ValueError,TypeError,OSError,sqlite3.Error) as exc:
        tries=attempts+1
        status="MISSING" if tries>=RETRY_LIMIT or now-ts > 48*3600 else "PENDING"
        with _connect() as db:
            _setup(db)
            db.execute("""UPDATE ready_plan_outcomes SET status=?,
                attempts=?, next_retry=?, error=?
                WHERE symbol=? AND side=? AND sent_minute=? AND status='PENDING'""",
                (status,tries,now+min(3600,600*tries),
                 type(exc).__name__,symbol,side,bucket))
        print(f"[SM_READY_OUTCOME_RETRY] {symbol} status={status} "
              f"attempt={tries} error={type(exc).__name__}",flush=True)
        return False


def summary(day=None):
    """Day in Europe/Warsaw when alerts were sent, not outcome date."""
    with _connect() as db:
        _setup(db)
        if day is None:
            rows=db.execute("""SELECT status,verdict,net_pct FROM
                ready_plan_outcomes""").fetchall()
        else:
            from datetime import timedelta
            zone=ZoneInfo("Europe/Warsaw")
            local=datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=zone)
            start=local.timestamp()
            end=(local+timedelta(days=1)).timestamp()
            rows=db.execute("""SELECT status,verdict,net_pct FROM
                ready_plan_outcomes WHERE sent_at>=? AND sent_at<?""",
                (start,end)).fetchall()
    counts={}
    nets=[]
    for status,verdict,net in rows:
        key=verdict if status=="DONE" else status
        counts[key]=counts.get(key,0)+1
        if net is not None:
            nets.append(net)
    return {"count":len(rows),"counts":counts,
            "priced":len(nets),
            "average_net_pct":sum(nets)/len(nets) if nets else None,
            "profitable":sum(v>0 for v in nets),
            "unprofitable":sum(v<0 for v in nets),
            "cost_pct":COST_PCT}


def _daily_message(day,data):
    c=data["counts"]
    average=data["average_net_pct"]
    avg="ещё нет" if average is None else f"{average:+.3f}%"
    return "\n".join((
        "📊 SMART MONEY — ИТОГ ГОТОВЫХ ПЛАНОВ",
        f"Дата отправки: {day} (Польша)",
        f"Отправлено и записано: {data['count']}",
        f"🎯 TP1 раньше стопа: {c.get('TP1_FIRST',0)}",
        f"🛑 Стоп раньше TP1: {c.get('SL_FIRST',0)}",
        f"⏱ Завершено через 6ч: {c.get('TIME_EXIT_6H',0)}",
        f"⚪ Вход не достигнут: {c.get('NOT_FILLED',0)}",
        f"❔ Неоднозначные свечи: {c.get('AMBIGUOUS_ENTRY_BAR',0)+c.get('AMBIGUOUS_BOTH',0)}",
        f"⌛ Ожидают/нет данных: {c.get('PENDING',0)+c.get('MISSING',0)}",
        f"Средний моделируемый результат: {avg}",
        f"Прибыльные/убыточные закрытия: {data['profitable']}/{data['unprofitable']}",
        f"Учтены модельные расходы {COST_PCT:.2f}%, проскальзывание не учтено.",
        "Это ретроспективная проверка свечей, не реальные сделки.",
        "Данные прошлых планов без сохранённых уровней сюда не включены.",
    ))


def report(now=None,send=None):
    """Hourly logfile summary and at most one day report after 22:00 Warsaw."""
    global _last_summary_ts
    now=time.time() if now is None else float(now)
    if now-_last_summary_ts>=3600:
        _last_summary_ts=now
        all_data=summary()
        print(f"[SM_READY_OUTCOME_SUMMARY] sent={all_data['count']} "
              f"counts={all_data['counts']} priced={all_data['priced']} "
              f"avg_net={all_data['average_net_pct']} "
              "model=first_touch_next_full_1m_6h_after_0p22pct_cost",
              flush=True)
    local=datetime.fromtimestamp(now,ZoneInfo("Europe/Warsaw"))
    if local.hour < 22 or not callable(send):
        return False
    day=local.strftime("%Y-%m-%d")
    with _connect() as db:
        _setup(db)
        already=db.execute("SELECT 1 FROM ready_plan_report_days WHERE day=?",
                           (day,)).fetchone()
    if already:
        return False
    data=summary(day)
    if data["count"]==0:
        return False
    try:
        if send(_daily_message(day,data)) is not True:
            return False
        with _connect() as db:
            _setup(db)
            db.execute("INSERT OR IGNORE INTO ready_plan_report_days VALUES (?,?)",
                       (day,now))
        print(f"[SM_READY_DAILY_REPORT_SENT] day={day} "
              f"sent={data['count']} resolved={data['priced']}",flush=True)
        return True
    except Exception as exc:
        print("[SM_READY_DAILY_REPORT_ERROR]",
              type(exc).__name__,flush=True)
        return False


def _worker(sender):
    while True:
        try:
            advance()
            report(send=sender)
        except Exception as exc:
            print("[SM_READY_OUTCOME_WORKER_ERROR]",
                  type(exc).__name__,flush=True)
        time.sleep(120)


def start_worker(sender):
    global _started
    if _started:
        return False
    # Fail startup explicitly if persistent tracking DB cannot be prepared.
    with _connect() as db:
        _setup(db)
    _started=True
    thread=threading.Thread(target=_worker,args=(sender,),
                            name="smart-money-ready-audit",daemon=True)
    thread.start()
    print(f"[SM_READY_OUTCOME_STARTED] horizon={HORIZON_MIN}m "
          f"cost={COST_PCT}% recorded_sent_only=true",flush=True)
    return True
