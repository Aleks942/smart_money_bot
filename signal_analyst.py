import sqlite3
import time
from datetime import datetime

DB_FILE = "signals.db"


# ==============================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ
# ==============================

def init_db():

    conn = sqlite3.connect("signals.db")
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        ts INTEGER,
        time_str TEXT,
        entry_price REAL,
        entry_type TEXT,
        direction TEXT,
        score INTEGER,
        acc_score INTEGER,
        stage TEXT,
        setup TEXT,
        expected_move_min REAL,
        expected_move_max REAL,
        result TEXT,
        move_pct REAL
    )
    """)

    try:
        cur.execute("ALTER TABLE signals ADD COLUMN setup TEXT")
    except:
        pass

    try:
        cur.execute("ALTER TABLE signals ADD COLUMN entry_type TEXT")
    except:
        pass

    conn.commit()
    conn.close()


# ==============================
# СОХРАНЕНИЕ СИГНАЛА
# ==============================

def save_signal(signal):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    ts = signal.get("ts", int(time.time()))
    time_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    cur.execute("""
    INSERT INTO signals (
        symbol, ts, time_str,
        entry_price,entry_type, direction,
        score, acc_score,
        stage,
        expected_move_min,
        expected_move_max,
        result,
        move_pct
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
        signal["instId"],
        ts,
        time_str,
        signal.get("entry_price", signal["price"]),
        signal.get("entry_type", signal.get("entry", "UNKNOWN")),
        signal.get("direction_code", signal["direction"]),
        signal["score"],
        signal["acc_score"],
        signal["stage"],
        signal["exp_move_min"],
        signal["exp_move_max"],
        "OPEN",
        0.0
    ))

    conn.commit()
    conn.close()


# ==============================
# ОБНОВЛЕНИЕ РЕЗУЛЬТАТА
# ==============================

def update_signal_result(symbol, entry_price, current_price):

    move_pct = (current_price - entry_price) / entry_price * 100

    result = "NEUTRAL"

    if move_pct >= 0.5:
        result = "HIT"

    elif move_pct <= -0.5:
        result = "FAIL"

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    UPDATE signals
    SET result=?, move_pct=?
    WHERE symbol=? AND entry_price=? AND result='OPEN'
    """, (result, move_pct, symbol, entry_price))

    conn.commit()
    conn.close()


# ==============================
# СТАТИСТИКА
# ==============================

def get_stats():

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM signals")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM signals WHERE result='HIT'")
    hits = cur.fetchone()[0]

    winrate = 0

    if total > 0:
        winrate = hits / total * 100

    conn.close()

    return {
        "total": total,
        "hits": hits,
        "winrate": round(winrate, 2)
    }

# ==============================
# ПОЛУЧИТЬ ОТКРЫТЫЕ СИГНАЛЫ
# ==============================

def get_open_signals(older_than_sec=300):

    now = int(time.time())

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, symbol, entry_price, direction, ts
        FROM signals
        WHERE result='OPEN'
    """)

    rows = cur.fetchall()
    conn.close()

    signals = []

    for r in rows:

        signal_id, symbol, entry, direction, ts = r

        if now - ts >= older_than_sec:

            signals.append({
                "id": signal_id,
                "symbol": symbol,
                "entry": entry,
                "direction": direction,
                "created_at": ts
            })

    return signals


# ==============================
# ЗАКРЫТЬ СИГНАЛ
# ==============================

def close_signal(signal_id, move_pct, result):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        UPDATE signals
        SET result=?, move_pct=?
        WHERE id=?
    """, (result, move_pct, signal_id))

    conn.commit()
    conn.close()



 
