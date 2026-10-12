"""Regression tests: invalid Spot CVD symbols must not trigger repeated Bybit calls."""
import ast
import time
import unittest
from unittest.mock import Mock, patch


def load_scoped_functions():
    with open("main.py", encoding="utf-8") as stream:
        tree = ast.parse(stream.read())
    wanted = {"bybit_get", "_spot_cvd_fetch_source", "_spot_symbol_plain"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    if {node.name for node in nodes} != wanted:
        raise AssertionError("Spot CVD production functions not found")
    namespace = {
        "requests": Mock(),
        "time": time,
        "_SPOT_CVD_UNSUPPORTED_UNTIL": {},
        "_SPOT_CVD_UNSUPPORTED_TTL_SEC": 600,
        "_SPOT_CVD_UNSUPPORTED_MAX": 1024,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), namespace)
    return namespace


class SpotCvdUnsupportedTests(unittest.TestCase):
    def test_bybit_known_unlisted_pair_has_no_retry(self):
        ns = load_scoped_functions()
        response = Mock()
        response.json.return_value = {
            "retCode": 10001, "retMsg": "Not supported symbols",
        }
        ns["requests"].get.return_value = response
        with patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "BYBIT_SPOT_SYMBOL_UNSUPPORTED_10001"):
                ns["bybit_get"]("https://api.bybit.com/v5/market/recent-trade",
                                {"category": "spot", "symbol": "NOTLISTEDUSDT"}, retries=5)
        self.assertEqual(ns["requests"].get.call_count, 1)
        sleep.assert_not_called()

    def test_bybit_transient_error_retries_then_succeeds(self):
        ns = load_scoped_functions()
        slow = Mock()
        slow.json.return_value = {"retCode": 10006, "retMsg": "Too frequent"}
        good = Mock()
        good.json.return_value = {"retCode": 0, "result": {"list": []}}
        ns["requests"].get.side_effect = [slow, good]
        with patch("time.sleep") as sleep:
            result = ns["bybit_get"](
                "https://api.bybit.com/v5/market/recent-trade",
                {"category": "spot", "symbol": "BTCUSDT"}, retries=2
            )
        self.assertEqual(result["retCode"], 0)
        self.assertEqual(ns["requests"].get.call_count, 2)
        sleep.assert_called_once()

    def test_unsupported_pair_is_cached_per_venue_then_rechecked(self):
        ns = load_scoped_functions()
        unsupported = Mock(return_value={
            "fetch_ok": False, "error": "BYBIT_SPOT_SYMBOL_UNSUPPORTED_10001",
            "trades": [],
        })
        def fetch(*args):
            return {"fetch_ok": True, "trades": [{"size": 1}]}
        with patch("time.monotonic", side_effect=[1000, 1001, 1002, 1601]):
            first = ns["_spot_cvd_fetch_source"]("ABCUSDT", "BYBIT", 500, unsupported)
            again = ns["_spot_cvd_fetch_source"]("ABCUSDT", "BYBIT", 500, unsupported)
            other = ns["_spot_cvd_fetch_source"]("ABCUSDT", "OKX", 300, fetch)
            expired = ns["_spot_cvd_fetch_source"]("ABCUSDT", "BYBIT", 500, unsupported)
        self.assertEqual(unsupported.call_count, 2)
        self.assertFalse(first["fetch_ok"])
        self.assertEqual(again["error"], "SPOT_SYMBOL_UNSUPPORTED_CACHED")
        self.assertTrue(other["fetch_ok"])
        self.assertFalse(expired["fetch_ok"])

    def test_transient_spot_failure_never_negative_cached(self):
        ns = load_scoped_functions()
        outage = Mock(return_value={"fetch_ok": False, "error": "timeout", "trades": []})
        with patch("time.monotonic", return_value=1000):
            ns["_spot_cvd_fetch_source"]("BTCUSDT", "BYBIT", 500, outage)
            ns["_spot_cvd_fetch_source"]("BTCUSDT", "BYBIT", 500, outage)
        self.assertEqual(outage.call_count, 2)


if __name__ == "__main__":
    unittest.main()
