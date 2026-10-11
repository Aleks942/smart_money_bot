"""Observational, score/time-matched audit of sent vs rejected SmartMoney.

Never feeds any scan filter, signal score, trade entry, or Telegram alert.
Matching variables are known at decision time (UTC day, four-hour block,
direction and 10-point score band). Each blocked stratum is weighted by
the number of sent signals in that stratum; overlapping samples are not
independent and selection bias is NOT eliminated by this procedure.
"""
from collections import defaultdict
from statistics import mean

REFERENCE_COST_PCT = 0.22
HORIZONS = (5, 10, 30)


def _cell(ts, side, score):
    """Coarse pre-decision matching cell: same day, session, side, score."""
    return (
        int(float(ts) // 86400),
        int((float(ts) % 86400) // (4 * 3600)),
        str(side).upper(),
        min(4, max(0, int(float(score) // 10))),
    )


def compute_comparison(rows):
    """Input tuples: cohort, timestamp, side, score, p5, p10, p30.

    Return one descriptive snapshot per (direction, horizon), with sent
    distribution standardized to matched cells. NULL/nonfinite values are
    excluded without backfilling.
    """
    import math

    cells = defaultdict(lambda: {"ACCEPT": [], "REJECT": []})
    for row in rows:
        try:
            cohort, ts, side, score, p5, p10, p30 = row
            cohort = str(cohort).upper()
            if cohort not in ("ACCEPT", "REJECT") or side not in ("LONG", "SHORT"):
                continue
            nums = (float(ts), float(score))
            if not all(math.isfinite(v) for v in nums):
                continue
            values = []
            for value in (p5, p10, p30):
                if value is None:
                    values.append(None)
                else:
                    v = float(value)
                    values.append(v if math.isfinite(v) else None)
            cells[_cell(ts, side, score)][cohort].append(values)
        except (ValueError, TypeError, OverflowError, IndexError):
            continue

    aggregates = {}
    for side in ("LONG", "SHORT"):
        for col, horizon in enumerate(HORIZONS):
            sent_values, blocked_reference = [], []
            matched_days, matched_strata = set(), 0
            n_blocked = 0
            for (day, session, cell_side, band), group in cells.items():
                if side != cell_side:
                    continue
                sent = [v[col] for v in group["ACCEPT"] if v[col] is not None]
                blocked = [v[col] for v in group["REJECT"] if v[col] is not None]
                if not sent or not blocked:
                    continue
                # Only matched cells contribute; do not compare unlike regimes.
                sent_values.extend(sent)
                blocked_reference.extend([mean(blocked)] * len(sent))
                matched_days.add(day)
                matched_strata += 1
                n_blocked += len(blocked)
            n_sent = len(sent_values)
            if n_sent:
                sent_avg = mean(sent_values)
                blocked_avg = mean(blocked_reference)
                delta = sent_avg - blocked_avg
                hit = sum(v > REFERENCE_COST_PCT for v in sent_values)
            else:
                sent_avg = blocked_avg = delta = None
                hit = 0
            aggregates[(side, horizon)] = {
                "sent_n": n_sent, "blocked_n": n_blocked,
                "days": len(matched_days), "strata": matched_strata,
                "sent_gross": sent_avg, "blocked_gross": blocked_avg,
                "sent_net_ref": (sent_avg - REFERENCE_COST_PCT)
                                if sent_avg is not None else None,
                "blocked_net_ref": (blocked_avg - REFERENCE_COST_PCT)
                                if blocked_avg is not None else None,
                "gross_difference": delta,
                "sent_positive_ref": hit,
                "status": (
                    "COLLECTING" if n_sent < 30 or len(matched_days) < 7
                    or matched_strata < 5 else "DESCRIPTIVE_ONLY_NEEDS_OOS"
                ),
            }
    return aggregates


def print_comparison(db, now, ttl_sec=30 * 86400):
    """Read only existing research tables, never write or fetch external data."""
    start = float(now) - ttl_sec
    accepted = db.execute("""SELECT 'ACCEPT',ts,side,score,p5,p10,p30
        FROM accepts WHERE state='DONE' AND ts>=? AND p30 IS NOT NULL""",
        (start,)).fetchall()
    rejected = db.execute("""SELECT 'REJECT',ts,side,score,p5,p10,p30
        FROM rejects WHERE state='DONE' AND ts>=? AND p30 IS NOT NULL""",
        (start,)).fetchall()
    compared = compute_comparison(accepted + rejected)
    for (side, horizon), row in compared.items():
        def fmt(x):
            return "NA" if x is None else f"{x:+.4f}"
        print(
            f"[MATCHED_COHORT] side={side} horizon={horizon}m "
            f"sent_n={row['sent_n']} blocked_n={row['blocked_n']} "
            f"days={row['days']} strata={row['strata']} "
            f"sent_mean_gross={fmt(row['sent_gross'])}% "
            f"blocked_standardized_mean_gross={fmt(row['blocked_gross'])}% "
            f"sent_mean_after_reference_cost={fmt(row['sent_net_ref'])}% "
            f"blocked_mean_after_reference_cost={fmt(row['blocked_net_ref'])}% "
            f"gross_difference={fmt(row['gross_difference'])}% "
            f"sent_positive_after_reference_cost={row['sent_positive_ref']}/{row['sent_n']} "
            f"status={row['status']} "
            f"match=UTC_DAY_4H_DIRECTION_SCORE_BAND "
            f"reference_roundtrip_cost={REFERENCE_COST_PCT:.2f}% "
            "model=next_full_minute_open_no_orders "
            "selection_bias_possible=true no_causal_claim=true",
            flush=True,
        )
    return compared
