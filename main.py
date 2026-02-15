import os
import time
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY")

POLL_SECONDS = 600
STATE_FILE = "state.json"
HEARTBEAT_SECONDS = 6 * 3600

REQUEST_TIMEOUT = 12


# =========================
# TELEGRAM
# =========================
def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
    except:
        pass


# =========================
# COINGLASS FUNDING (FAIL SAFE)
# =========================
def get_funding():

    if not COINGLASS_API_KEY:
        return None

    url = "https://open-api.coinglass.com/public/v2/futures/funding_rates"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    params = {"symbol": "BTC"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None

        data = r.json().get("data", [])

        rates = []
        for x in data:
            if x.get("fundingRate") is not None:
                rates.append(float(x["fundingRate"]))

        if not rates:
            return None

        return sum(rates) / len(rates)

    except:
        return None


# =========================
# COINGLASS OPEN INTEREST
# =========================
def get_open_interest():

    if not COINGLASS_API_KEY:
        return None

    url = "https://open-api.coinglass.com/public/v2/futures/open_interest"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    params = {"symbol": "BTC"}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return None

        data = r.json().get("data", [])

        oi_values = []
        for x in data:
            if x.get("openInterest") is not None:
                oi_values.append(float(x["openInterest"]))

        if not oi_values:
            return None

        return sum(oi_values) / len(oi_values)

    except:
        return None


# =========================
# COINGECKO CANDLES (NO BLOCKS)
# =========================
def get_candles():

    url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
    params = {"vs_currency": "usd", "days": "1"}

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)

    if r.status_code != 200:
        raise RuntimeError("CoinGecko candles failed")

    data = r.json()

    candles = []
    for c in data:
        candles.append([c[0], c[1], c[2], c[3], c[4], 1])

    return candles[-30:]


# =========================
# COMPRESSION DETECTOR
# =========================
def compression_ok(candles):

    if len(candles) < 20:
        return False

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]

    ranges = [h - l for h, l in zip(highs, lows)]

    last_range = sum(ranges[-4:]) / 4
    prev_range = sum(ranges[-12:-4]) / 8

    return last_range < prev_range * 0.7


# =========================
# STATE
# =========================
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except:
        pass


# =========================
# MAIN
# =========================
if __name__ == "__main__":

    send_telegram("🚀 Smart Money PRO Bot started")

    state = load_state()

    prev_score = state.get("score")
    prev_oi = state.get("oi")
    last_heartbeat = state.get("hb", 0)

    while True:

        try:

            funding = get_funding()
            oi = get_open_interest()
            candles = get_candles()

            score = 0

            # Funding сигнал
            if funding is not None and funding < 0:
                score += 1

            # Compression сигнал
            if compression_ok(candles):
                score += 1

            # Open Interest Spike
            if prev_oi is not None and oi is not None:
                if oi > prev_oi * 1.01:
                    score += 1

            now = int(time.time())

            changed = (score != prev_score)
            heartbeat = (now - last_heartbeat) > HEARTBEAT_SECONDS

            if changed or heartbeat:

                msg = "🧠 Smart Money PRO\n"
                msg += f"📊 Score: {score}/3\n"

                if funding is None:
                    msg += "💰 Funding: N/A\n"
                else:
                    msg += f"💰 Funding: {funding}\n"

                if oi is not None:
                    msg += f"📈 Open Interest: {int(oi)}\n"

                if score >= 2:
                    msg += "⚡ PRE-PUMP STRUCTURE\n"

                if score == 3:
                    msg += "🔥 STRONG SMART MONEY SIGNAL\n"

                send_telegram(msg)

                prev_score = score
                prev_oi = oi

                state["score"] = score
                state["oi"] = oi

                if heartbeat:
                    last_heartbeat = now
                    state["hb"] = now

                save_state(state)

        except Exception as e:
            send_telegram(f"❌ Error:\n{str(e)}")

        time.sleep(POLL_SECONDS)
