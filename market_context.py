import requests


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
