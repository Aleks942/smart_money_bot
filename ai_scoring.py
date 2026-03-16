import sqlite3

DB_FILE = "signals.db"


def get_setup_stats():

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT setup,
               COUNT(*) as total,
               SUM(CASE WHEN result='HIT' THEN 1 ELSE 0 END) as hits
        FROM signals
        WHERE result != 'OPEN'
        GROUP BY setup
    """)

    rows = cur.fetchall()
    conn.close()

    stats = {}

    for setup, total, hits in rows:

        winrate = 0

        if total > 0:
            winrate = hits / total

        stats[setup] = {
            "total": total,
            "hits": hits,
            "winrate": winrate
        }

    return stats


def get_ai_multiplier(setup):

    stats = get_setup_stats()

    if setup not in stats:
        return 1.0

    winrate = stats[setup]["winrate"]

    if winrate > 0.65:
        return 1.25

    if winrate > 0.55:
        return 1.1

    if winrate > 0.45:
        return 1.0

    return 0.8
