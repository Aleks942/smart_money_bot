import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ===== Telegram =====
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    r = requests.post(url, data=payload, timeout=10)
    r.raise_for_status()

# ===== Bybit: цена BTC =====
def get_btc_price():
    url = "https://api.bybit.com/v5/market/tickers"
    params = {
        "category": "linear",
        "symbol": "BTCUSDT"
    }
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    return data["result"]["list"][0]["lastPrice"]

if __name__ == "__main__":
    price = get_btc_price()
    message = f"✅ Smart Money Bot ONLINE\nBTCUSDT price: {price}"
    send_telegram(message)
