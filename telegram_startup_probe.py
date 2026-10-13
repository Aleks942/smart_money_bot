"""One safe startup Telegram connectivity probe (never a trade alert).

Before sending, verify that the configured BOT_TOKEN identifies the bot
the user actually opened in Telegram. Do not print API tokens or chat IDs.
"""
from datetime import datetime, timezone
import requests

EXPECTED_BOT_USERNAME = "CryptoMarketState_bot"


def verify_and_ping(token, send_message, policy, expected_username=EXPECTED_BOT_USERNAME):
    if not token:
        print("[SM_STARTUP_PING] status=SKIPPED reason=NO_BOT_TOKEN", flush=True)
        return False
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=8,
        )
        if response.status_code != 200:
            print(f"[SM_STARTUP_PING] status=FAILED stage=getMe http={response.status_code}",
                  flush=True)
            return False
        payload = response.json()
        bot = payload.get("result") if isinstance(payload, dict) and payload.get("ok") else None
        username = bot.get("username") if isinstance(bot, dict) else None
        if not isinstance(username, str) or username.lower() != expected_username.lower():
            # Explicitly avoid sending to the wrong bot. No tokens logged.
            safe_name = username if isinstance(username, str) and username.isascii() and username.replace("_","").isalnum() and len(username)<=80 else "UNKNOWN"
            print(f"[SM_STARTUP_PING] status=BLOCKED reason=BOT_USERNAME_MISMATCH "
                  f"actual=@{safe_name} expected=@{expected_username}", flush=True)
            return False
    except (requests.RequestException, ValueError, TypeError) as exc:
        print(f"[SM_STARTUP_PING] status=FAILED stage=getMe kind={type(exc).__name__}",
              flush=True)
        return False

    print(f"[SM_TELEGRAM_IDENTITY] status=VERIFIED bot=@{expected_username}", flush=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    message = (
        "🧪 SMARTMONEY MONITOR — ПРОВЕРКА СВЯЗИ\n\n"
        "✅ Бот запущен на Railway.\n"
        f"Время: {now}\n"
        f"Режим сигналов: {policy}\n\n"
        "Это технический тест доставки Telegram, НЕ торговый сигнал. "
        "Торговые фильтры остались включены."
    )
    try:
        accepted = bool(send_message(message))
    except Exception as exc:
        print(f"[SM_STARTUP_PING] status=FAILED stage=send kind={type(exc).__name__}",
              flush=True)
        return False
    print(f"[SM_STARTUP_PING] status={'ACCEPTED' if accepted else 'FAILED'} "
          f"bot=@{expected_username} trade_alert=false", flush=True)
    return accepted
