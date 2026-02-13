import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")


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
# COINGLASS FUNDING RATE
# =========================
def get_funding():
    url = "https://open-api.coinglass.com/public/v2/futures/funding_rates"

    headers = {
        "coinglassSecret": COINGLASS_API_KEY
    }

    params = {
        "symbol": "BTC"
    }

    r = requests.get(url, headers=headers, params=params, timeout=10)

    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}: {r.text}")

    data = r.json()

    if "data" not in data:
        raise Exception(f"Unexpected response: {data}")

    # Берём первую биржу
    first_exchange = data["data"][0]
    funding = first_exchange.get("fundingRate")

    return funding


# =========================
# MAIN LOOP
# =========================
if __name__ == "__main__":
    send_telegram("🚀 Smart Money Bot (Coinglass funding) started")

    while True:
        try:
            funding = get_funding()
            message = f"💰 BTC Funding Rate: {funding}"
            send_telegram(message)
        except Exception as e:
            send_telegram(f"❌ Coinglass error:\n{str(e)}")

        time.sleep(600)
