import os
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
    r = requests.post(url, data=payload, timeout=10)
    r.raise_for_status()


# =========================
# BINANCE FUTURES PRICE
# =========================
def get_btc_price():
    url = "https://fapi.binance.com/fapi/v1/ticker/price"
    params = {
        "symbol": "BTCUSDT"
    }

    r = requests.get(url, params=params, timeout=10)

    if r.status_code != 200:
        raise Exception(f"Binance error: {r.status_code} - {r.text}")

    data = r.json()
    return data["price"]


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        price = get_btc_price()
        message = f"✅ Smart Money Bot ONLINE\nBTCUSDT price: {price}"
    except Exception as e:
        message = f"❌ Bot error:\n{str(e)}"

    send_telegram(message)

