"""Isolated, no-network trend-first Telegram audit regressions."""
import math
import os
import tempfile
import unittest
from unittest.mock import patch

import trend_confirmation as trend
import rejection_forward_study as study


NOW = 1800000000.0


def hist(frame_min=60, sign=1, now=NOW, stale=False):
    period=frame_min*60000
    last=math.floor(now*1000/period)*period-period
    if stale:
        last-=period*5
    return {"retCode":0,"result":{"list":[
        [str(last-k*period),"100","101","99",
         str(100*(1.001**(sign*(100-k)))),"1","1"]
        for k in range(100)]}}


def signal():
    return {"instId":"ETHUSDT","direction_code":"UP",
            "entry_type":"CONTINUATION_LONG","score":25,"price":100}


class TrendConfirmationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        p=patch.object(study,"DB",os.path.join(temp.name,"study.db"))
        p.start();self.addCleanup(p.stop)
        p=patch.object(trend,"_trend_cache",trend.OrderedDict())
        p.start();self.addCleanup(p.stop)
        p=patch.object(trend,"POLICY","CONFIRMED_ONLY")
        p.start();self.addCleanup(p.stop)

    def test_two_consecutive_closed_higher_timeframes(self):
        with patch.object(trend,"_fetch_trend",
                          side_effect=lambda s,i,n: trend._closed_trend(
                              hist(i),i,n)):
            self.assertEqual(trend.trend("ETHUSDT",NOW),"UP")
        trend._trend_cache.clear()
        with patch.object(trend,"_fetch_trend",
                          side_effect=lambda s,i,n: trend._closed_trend(
                              hist(i,1 if i==60 else -1),i,n)):
            self.assertEqual(trend.trend("ETHUSDT",NOW),"UNKNOWN")
        self.assertEqual(trend._closed_trend(hist(60,stale=True),60,NOW),
                         "UNKNOWN")
        missing=hist()
        missing["result"]["list"].pop(12)
        self.assertEqual(trend._closed_trend(missing,60,NOW),"UNKNOWN")

    def test_fails_closed_and_journals_without_telegram(self):
        with patch.object(trend,"trend",return_value="UP"):
            self.assertEqual(trend.consider(signal(),NOW),
                             (False,"EDGE_UNPROVEN"))
            self.assertEqual(trend.consider(signal(),NOW+40),
                             (False,"EDGE_UNPROVEN"))
        with study._connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM trend_gate_samples").fetchone()[0],1)
        with patch.object(trend,"trend",return_value="DOWN"):
            self.assertEqual(trend.consider(signal(),NOW+100),
                             (False,"TREND_NOT_ALIGNED"))
        self.assertEqual(trend.consider(
            dict(signal(),direction_code="FLAT",entry_type="UNKNOWN"),NOW),
            (False,"INVALID_CANDIDATE"))

    def test_probability_needs_earlier_days_and_diversity(self):
        with study._connect() as db:
            trend._setup(db)
            for k in range(120):
                ts=NOW-(12-(k//10))*86400+(k%10)*5400
                db.execute("""INSERT INTO trend_gate_samples
                    (symbol,side,bucket,ts,band,trend,status,verdict,net_pct,evaluated_ts)
                    VALUES (?,?,?,?,?,?,'DONE','TP_FIRST',1.28,?)""",
                    ("COIN"+str(k%30)+"USDT","LONG",int(ts//5400),ts,
                     "GE20","UP",ts+1800))
            edge=trend._odds(db,"LONG","UP","GE20",NOW)
            self.assertTrue(edge["approved"])
            self.assertGreater(edge["lower_ev"],0)
            db.execute("UPDATE trend_gate_samples SET verdict='SL_FIRST' "
                       "WHERE bucket%2=0")
            weakened=trend._odds(db,"LONG","UP","GE20",NOW)
            self.assertFalse(weakened["approved"])

    def test_first_touch_and_no_entry_bar_lookahead(self):
        first=(int(NOW//60)+1)*60000
        raw=[[str(first+k*60000),"100","101","99","100"]
             for k in range(30)]
        payload={"retCode":0,"result":{"list":list(reversed(raw))}}
        bars=study._parse_candles(payload,first)
        self.assertEqual(trend._first_touch(bars,"LONG"),
                         ("SL_FIRST",-1.22))
        self.assertEqual(trend._first_touch(bars,"SHORT"),
                         ("SL_FIRST",-1.22))
        self.assertEqual(trend._first_touch(bars[:20],"LONG"),(None,None))
        payload["result"]["list"][-1][0]=str(first-60000)
        with self.assertRaises(ValueError):
            study._parse_candles(payload,first)

    def test_shared_worker_fetches_bounded_candles(self):
        with patch.object(trend,"trend",return_value="UP"):
            trend.consider(signal(),NOW)
        first=(int(NOW//60)+1)*60000
        candles=[[str(first+k*60000),"100","102","99.8","100.4"]
                 for k in range(30)]
        class Response:
            def raise_for_status(self): return None
            def json(self):
                return {"retCode":0,"result":{"list":list(reversed(candles))}}
        with patch.object(trend.requests,"get",return_value=Response()) as get:
            trend.advance(NOW+40*60)
            self.assertEqual(get.call_count,1)
        with study._connect() as db:
            result=db.execute("SELECT status,verdict,net_pct "
                              "FROM trend_gate_samples").fetchone()
            self.assertEqual(result[0],"DONE")
            self.assertEqual(result[1],"TP_FIRST")
            self.assertAlmostEqual(result[2],1.28)

    def test_policy_setting_never_invents_odds(self):
        with patch.object(trend,"POLICY","LEGACY"):
            self.assertEqual(trend.consider(signal(),NOW),(True,"LEGACY"))
        with study._connect() as db:
            trend._setup(db)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM trend_gate_samples").fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
