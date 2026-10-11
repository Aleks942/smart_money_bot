"""Tests for fail-closed CoinGecko rate-limit handling, without bot startup."""
import ast
import os
import time
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from market_cap_backoff import remaining_seconds, register_429


def load_scoped_market_cap(requests_fake, cached=None, fallback=None):
    with open("main.py", "r", encoding="utf-8") as stream:
        tree = ast.parse(stream.read())
    keep = {"_chunked", "_get_coingecko_headers", "fetch_market_caps_usd"}
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in keep]
    if len(nodes) != 3:
        raise AssertionError("Missing production market-cap functions")
    namespace = {
        "os": os, "time": time, "requests": requests_fake,
        "COINGECKO_API_KEY": "", "MARKET_CAP_CACHE_TTL_SEC": 3600,
        "_market_cap_cache": {"ts": 0, "data": dict(cached or {}),
                              "last_fail_ts": 0},
        "_cg_cooldown": remaining_seconds, "_cg_note_429": register_429,
        "fetch_coinpaprika_caps": fallback or (lambda symbols: {}), "math": math,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py",
                 "exec"), namespace)
    return namespace


class MarketCapCooldownTests(unittest.TestCase):
    def test_cooldown_uses_retry_after_and_is_bounded(self):
        store = {}
        r = SimpleNamespace(headers={"Retry-After": "120"})
        delay = register_429(store, r, now=1000)
        self.assertEqual(delay, 120)
        self.assertEqual(remaining_seconds(store, now=1001), 119)
        self.assertEqual(register_429(store, SimpleNamespace(
            headers={"Retry-After": "99999"}), now=1002), 300)

    def test_one_429_not_retried_and_missing_caps_stay_unapproved(self):
        blocked = Mock(status_code=429, headers={"Retry-After": "120"})
        request = Mock(get=Mock(return_value=blocked))
        backup = Mock(return_value={"BTC": 100_000_000_000})
        ns = load_scoped_market_cap(request, fallback=backup)
        caps = ns["fetch_market_caps_usd"](["BTC", "ETH"])
        again = ns["fetch_market_caps_usd"](["BTC", "ETH"])
        self.assertEqual(caps, {"BTC": 100_000_000_000})
        self.assertEqual(again, caps)
        self.assertNotIn("ETH", caps)
        self.assertEqual(request.get.call_count, 1)
        backup.assert_called_once()
        self.assertGreater(remaining_seconds(ns["_market_cap_cache"]), 0)

    def test_success_after_cooldown_expires(self):
        good = Mock(status_code=200)
        good.json.return_value = [{"symbol": "eth", "market_cap": 300_000_000}]
        good.raise_for_status.return_value = None
        request = Mock(get=Mock(return_value=good))
        ns = load_scoped_market_cap(request)
        ns["_market_cap_cache"]["cg_cooldown_until"] = time.time()-1
        with patch.dict(os.environ, {"MARKET_CAP_REQUEST_SLEEP_SEC": "0"}):
            result = ns["fetch_market_caps_usd"](["ETH"])
        self.assertEqual(result["ETH"], 300_000_000)
        self.assertEqual(request.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
