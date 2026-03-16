import requests


# =========================
# BTC TREND
# =========================
def get_btc_trend():

    try:
        url = "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT"
        data = requests.get(url, timeout=5).json()

        last = float(data["result"]["list"][0]["lastPrice"])
        prev = float(data["result"]["list"][0]["prevPrice24h"])

        change = (last - prev) / prev * 100

        if change > 1:
            return "BULL"

        if change < -1:
            return "BEAR"

        return "NEUTRAL"

    except:
        return "NEUTRAL"


# =========================
# MARKET CONTEXT
# =========================
def apply_market_context(sig):

    btc = get_btc_trend()

    direction = sig.get("direction")
    score = sig.get("score", 0)

    if btc == "BULL" and direction == "⬆️ ВВЕРХ":
        score *= 1.15

    elif btc == "BEAR" and direction == "⬇️ ВНИЗ":
        score *= 1.15

    elif btc == "BULL" and direction == "⬇️ ВНИЗ":
        score *= 0.85

    elif btc == "BEAR" and direction == "⬆️ ВВЕРХ":
        score *= 0.85

    sig["score"] = round(score, 2)

    return sig
