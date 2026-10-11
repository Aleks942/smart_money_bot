"""Conservative zero-cost backup for SmartMoney's market-cap verification.

CoinPaprika public tickers provide USD market capitalization. Tickers with
duplicate symbols are deliberately rejected: a ticker symbol is NOT a coin ID.
Absence/ambiguity remains unverified; never assume market cap is sufficient.
"""
import math
import requests

API = "https://api.coinpaprika.com/v1/tickers"


def parse_caps(rows, wanted):
    wanted = {str(s).upper() for s in wanted}
    if not isinstance(rows, list):
        raise ValueError("INVALID_PROVIDER_FORMAT")
    caps = {}
    duplicates = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper().strip()
        if sym not in wanted:
            continue
        usd = (row.get("quotes") or {}).get("USD") or {}
        try:
            cap = float(usd.get("market_cap"))
        except (ValueError, TypeError, OverflowError):
            continue
        if not math.isfinite(cap) or cap <= 0:
            continue
        if sym in caps:
            duplicates.add(sym)
        else:
            caps[sym] = cap
    for symbol in duplicates:
        caps.pop(symbol, None)
    return caps, len(duplicates)


def fetch_coinpaprika_caps(wanted, timeout=18):
    response = requests.get(API, params={"quotes":"USD"}, timeout=timeout)
    response.raise_for_status()
    caps, ambiguous = parse_caps(response.json(), wanted)
    print(f"[MARKET_CAP_BACKUP] provider=CoinPaprika verified={len(caps)} "
          f"ambiguous_symbols={ambiguous} method=UNIQUE_SYMBOL_ONLY",
          flush=True)
    return caps
