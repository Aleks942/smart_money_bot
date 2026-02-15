import os
import time
import json
import requests
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
COINGLASS_API_KEY = os.getenv("COINGLASS_API_KEY", "").strip()

# Настройки
SYMBOL_SPOT = "BTCUSDT"
COINGLASS_SYMBOL = "BTC"
INTERVAL = "5m"
CANDLES_LIMIT = 30

POLL_SECONDS = 600          # каждые 10 минут
HEARTBEAT_SECONDS = 6 * 3600  # раз в 6 часов "бот жив"
STATE_FILE = "state.json"

REQUEST_TIMEOUT = 12


# =========================
# UTILS
# =========================
def require_env():
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if not COINGLASS_API_KEY:
        missing.append("COINGLASS_API_KEY")
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")


def safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        # если не смогли сохранить — не критично, просто потеряем "память" до рестарта
        pass


# =========================
# TELEGRAM
# =========================
def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
    except Exception:
        # не роняем бота из-за Telegram
        pass


# =========================
# COINGLASS FUNDING RATE
# =========================
def get_funding_btc() -> Optional[float]:
    """
    Берём funding rate по BTC с Coinglass.
    В ответе обычно массив бирж. Берём "первую", но корректнее — усреднять.
    Здесь делаем: если есть несколько бирж с fundingRate — усредняем.
    """
    url = "https://open-api.coinglass.com/public/v2/futures/funding_rates"
    headers = {"coinglassSecret": COINGLASS_API_KEY}
    params = {"symbol": COINGLASS_SYMBOL}

    r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Coinglass HTTP {r.status_code}: {r.text}")

    data = r.json()
    arr = data.get("data")
    if not isinstance(arr, list) or not arr:
        raise RuntimeError(f"Coinglass unexpected response: {data}")

    rates: List[float] = []
    for item in arr:
        fr = safe_float(item.get("fundingRate"))
        if fr is not None:
            rates.append(fr)

    if not rates:
        return None

    return sum(rates) / len(rates)


# =========================
# BINANCE CANDLES
# =========================
def get_binance_candles() -> List[List[Any]]:
    """
    Binance klines:
    [
      [
        openTime, open, high, low, close, volume,
        closeTime, quoteAssetVolume, trades, ...
      ], ...
    ]
    """
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": SYMBOL_SPOT, "interval": INTERVAL, "limit": CANDLES_LIMIT}

    r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Binance HTTP {r.status_code}: {r.text}")

    data = r.json()
    if not isinstance(data, list) or len(data) < 12:
        raise RuntimeError("Binance candles: not enough data")
    return data


# =========================
# SMART MONEY PATTERN: COMPRESSION (пружина)
# =========================
def compression_ok(candles: List[List[Any]]) -> bool:
    """
    Идея:
    - Диапазон свечей сжимается (волатильность падает)
    - Объём не падает (значит это не "сон", а набор позиции)
    """
    if len(candles) < 20:
        return False

    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    ranges = [h - l for h, l in zip(highs, lows)]

    # последние 4 свечи vs предыдущие 8 свечей
    last_range = sum(ranges[-4:]) / 4.0
    prev_range = sum(ranges[-12:-4]) / 8.0

    # "пружина": диапазон упал минимум на 30%
    compression = last_range < prev_range * 0.70

    # объём держится (не провалился)
    avg_vol_prev = sum(volumes[-20:-4]) / 16.0
    avg_vol_last = sum(volumes[-4:]) / 4.0
    vol_ok = avg_vol_last >= avg_vol_prev * 0.90

    return compression and vol_ok


# =========================
# SCORE + STATE
# =========================
def build_signal(funding: Optional[float], candles: List[List[Any]]) -> Dict[str, Any]:
    """
    Собираем признаки и score.
    """
    score = 0
    reasons = []

    # 1) Толпа шортит (funding ниже 0)
    if funding is not None and funding < 0:
        score += 1
        reasons.append("funding<0 (толпа чаще в шорте)")

    # 2) Пружина
    comp = compression_ok(candles)
    if comp:
        score += 1
        reasons.append("compression (волатильность↓, объём держится)")

    # Можно расширять дальше: OI, ликвидации, ончейн — но пока держим просто и надёжно.
    alert = score >= 2

    # Цена для контекста
    last_close = float(candles[-1][4])

    return {
        "funding": funding,
        "score": score,
        "alert": alert,
        "reasons": reasons,
        "price": last_close,
        "ts": int(time.time()),
    }


def format_message(sig: Dict[str, Any]) -> str:
    funding = sig["funding"]
    funding_str = "None" if funding is None else f"{funding:.6f}"

    base = [
        "🧠 Smart Money Bot — BTC",
        f"💵 Price ({SYMBOL_SPOT}): {sig['price']:.2f}",
        f"💰 Funding (avg): {funding_str}",
        f"📊 Smart Score: {sig['score']}/2",
    ]

    if sig["reasons"]:
        base.append("Причины:")
        for r in sig["reasons"]:
            base.append(f"• {r}")

    if sig["alert"]:
        base.append("")
        base.append("⚡ PRE-PUMP STRUCTURE DETECTED")
        base.append("👉 Идея: рынок сжат + толпа против движения → шанс импульса выше.")

    return "\n".join(base)


def should_send(prev: Dict[str, Any], curr: Dict[str, Any], last_heartbeat_ts: int) -> Dict[str, Any]:
    """
    Решаем отправлять ли сообщение:
    - если изменился alert (OFF->ON или ON->OFF)
    - или изменился score
    - или пришло время heartbeat
    """
    now = curr["ts"]

    prev_alert = prev.get("alert")
    prev_score = prev.get("score")

    changed = (prev_alert != curr["alert"]) or (prev_score != curr["score"])

    heartbeat_due = (now - last_heartbeat_ts) >= HEARTBEAT_SECONDS

    return {"send": changed or heartbeat_due, "heartbeat_due": heartbeat_due}


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    require_env()

    state = load_state()
    last_heartbeat_ts = int(state.get("last_heartbeat_ts", 0))
    prev_signal = state.get("last_signal", {})

    send_telegram("🚀 Smart Money Bot started (funding + compression)")

    while True:
        try:
            funding = get_funding_btc()
            candles = get_binance_candles()
            curr_signal = build_signal(funding, candles)

            decision = should_send(prev_signal, curr_signal, last_heartbeat_ts)
            if decision["send"]:
                send_telegram(format_message(curr_signal))

                # если это был heartbeat — обновим метку
                if decision["heartbeat_due"]:
                    last_heartbeat_ts = curr_signal["ts"]

                prev_signal = curr_signal

                state["last_signal"] = prev_signal
                state["last_heartbeat_ts"] = last_heartbeat_ts
                save_state(state)

        except Exception as e:
            send_telegram(f"❌ Error:\n{str(e)}")

        time.sleep(POLL_SECONDS)
