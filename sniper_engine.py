# ==============================
# SNIPER ENTRY ENGINE
# ==============================

def sniper_signal(sig: dict) -> bool:

    try:

        score = sig.get("score", 0)
        acc = sig.get("acc_score", 0)
        flags = sig.get("flags", [])

        # ключевые признаки импульса
        impulse_flags = {
            "VOL_SPIKE",
            "ATR_EXPANSION",
            "BREAKOUT_UP",
            "BREAKOUT_DOWN"
        }

        impulse = any(f in impulse_flags for f in flags)

        # условия sniper
        if score >= 5 and acc >= 2 and impulse:
            return True

        return False

    except Exception:
        return False
