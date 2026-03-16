# ==============================
# SIGNAL TIER CLASSIFIER
# ==============================

def get_signal_tier(score: int, acc_score: int):

    try:

        if score >= 7:
            return "CONFIRM_ENTRY"

        if score >= 5:
            return "PRE_TRIGGER"

        if score >= 3 and acc_score >= 2:
            return "RADAR"

        return None

    except Exception:
        return None
