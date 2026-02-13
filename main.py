import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# =========================
# TELEGRAM
# =========================
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text
    }
    requests.post(url, data=payload, timeout=10)


# =========================
# BINANCE FUTURES PRICE
# =========================
def get_btc_price():
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    params = {"symbol": "BTCUSDT"}
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    return data["price"]


# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    send_telegram("🚀 Smart Money Bot started")

    while True:
        try:
            price = get_btc_price()
            message = f"BTCUSDT price: {price}"
            send_telegram(message)
        except Exception as e:
            send_telegram(f"❌ Error: {str(e)}")

        time.sleep(600)  # 10 минут
