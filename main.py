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
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": text
        }
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)


# =========================
# BINANCE FUTURES PRICE
# =========================
def get_btc_price():
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    params = {"symbol": "BTCUSDT"}

    r = requests.get(url, params=params, timeout=10)

    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {r.text}")

    try:
        data = r.json()
    except Exception:
        raise Exception(f"Invalid JSON response: {r.text}")

    if not isinstance(data, dict):
        raise Exception(f"Unexpected response format: {data}")

    if "price" not in data:
        raise Exception(f"No 'price' field in response: {data}")

    return data["price"]


# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    send_telegram("🚀 Smart Money Bot started")

    while True:
        try:
            price = get_btc_price()
            message = f"📊 BTCUSDT price: {price}"
            send_telegram(message)
        except Exception as e:
            send_telegram(f"❌ Binance error:\n{str(e)}")

        time.sleep(600)  # 10 минут
