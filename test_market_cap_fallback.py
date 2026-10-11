"""Zero-network tests: safe market-cap fallback and disabled DEBUG bypass."""
import ast
import unittest
from unittest.mock import Mock, patch

from market_cap_fallback import parse_caps, fetch_coinpaprika_caps


class MarketCapFallbackTests(unittest.TestCase):
    def test_duplicate_symbol_not_approved(self):
        rows=[
            {"symbol":"BTC","quotes":{"USD":{"market_cap":2e12}}},
            {"symbol":"BTC","quotes":{"USD":{"market_cap":200}}},
            {"symbol":"ETH","quotes":{"USD":{"market_cap":3e11}}},
            {"symbol":"BAD","quotes":{"USD":{"market_cap":None}}},
        ]
        caps,ambiguous=parse_caps(rows,{"BTC","ETH","BAD"})
        self.assertEqual(caps,{"ETH":3e11})
        self.assertEqual(ambiguous,1)

    def test_invalid_caps_fail_closed(self):
        rows=[
            {"symbol":"ABC","quotes":{"USD":{"market_cap":"nan"}}},
            {"symbol":"DEF","quotes":{"USD":{"market_cap":-10}}},
        ]
        caps,_=parse_caps(rows,{"ABC","DEF"})
        self.assertEqual(caps,{})

    @patch("market_cap_fallback.requests.get")
    def test_free_provider_single_http_request(self,get):
        response=Mock()
        response.json.return_value=[
            {"symbol":"BTC","quotes":{"USD":{"market_cap":2e12}}}]
        get.return_value=response
        caps=fetch_coinpaprika_caps(["BTC","ETH"])
        self.assertEqual(caps["BTC"],2e12)
        self.assertNotIn("ETH",caps)
        self.assertEqual(get.call_count,1)
        self.assertEqual(get.call_args.kwargs["params"],{"quotes":"USD"})

    def test_no_market_cap_never_bypasses_filter(self):
        with open("main.py",encoding="utf-8") as handle:
            tree=ast.parse(handle.read())
        func=next(n for n in tree.body if isinstance(n,ast.FunctionDef)
                  and n.name=="get_market_candidates_bybit")
        context={
            "get_bybit_tickers_linear":lambda:[{"symbol":"ABCUSDT",
                 "turnover24h":"30000000","lastPrice":"1.3",
                 "prevPrice24h":"1"}],
            "is_bad_symbol":lambda s:False,
            "is_trend_candidate":lambda s,t:True,
            "get_base_coin":lambda s:s[:-4],
            "fetch_market_caps_usd":lambda _: {},
            "SCAN_MIN_VOL_USDT":1000000,
            "SCAN_MIN_PCT_24H":1,
            "PREBREAK_SCAN_MIN_PCT_24H":0,
            "PREBREAK_SCAN_MAX_PCT_24H":100,
            "ACCUMULATION_MODE":True,
            "MARKET_CAP_PREFETCH_MULT":3,
            "SCAN_BATCH":5,
            "SCAN_TOP_N":5,
            "os":__import__("os"),
        }
        exec(compile(ast.Module(body=[func],type_ignores=[]),
                     "main.py","exec"),context)
        self.assertEqual(context["get_market_candidates_bybit"](),[])

if __name__=="__main__":
    unittest.main()
