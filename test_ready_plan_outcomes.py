"""No-network tests for recorded, Telegram-acknowledged ready trade plans."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import ready_plan_outcomes as tracker


def candle(index, op=100, hi=100.4, lo=99.6, close=100):
    return (1800000000000 + index*60000,op,hi,lo,close)


def plan(symbol="ETHUSDT", side="LONG"):
    if side == "LONG":
        return dict(symbol=symbol,side=side,entry=100,stop=99,
                    tp1=102,tp2=102.5,score=24)
    return dict(symbol=symbol,side=side,entry=100,stop=101,
                tp1=98,tp2=97.5,score=24)


class ReadyEvaluationTests(unittest.TestCase):
    def test_long_tp_first_and_net(self):
        bars=[candle(0),candle(1,100,102.3,99.8,101.5)]
        x=tracker.evaluate("LONG",100,99,102,bars)
        self.assertEqual(x["verdict"],"TP1_FIRST")
        self.assertAlmostEqual(x["net_pct"],1.78,places=6)
        self.assertEqual(x["minutes_to_exit"],1)

    def test_short_tp_first(self):
        bars=[candle(0),candle(1,100,100.2,97.8,99)]
        x=tracker.evaluate("SHORT",100,101,98,bars)
        self.assertEqual(x["verdict"],"TP1_FIRST")
        self.assertAlmostEqual(x["net_pct"],1.78)

    def test_adverse_stop_gap(self):
        bars=[candle(0),candle(1,98.5,99,98,98.7)]
        x=tracker.evaluate("LONG",100,99,102,bars)
        self.assertEqual(x["verdict"],"SL_FIRST")
        self.assertEqual(x["exit_price"],98.5)
        self.assertAlmostEqual(x["net_pct"],-1.72)

    def test_entry_bar_exit_unknown(self):
        bars=[candle(0,100,102.3,99.7,100)]
        x=tracker.evaluate("LONG",100,99,102,bars)
        self.assertEqual(x["verdict"],"AMBIGUOUS_ENTRY_BAR")
        self.assertIsNone(x["net_pct"])

    def test_both_stop_and_tp_in_same_bar(self):
        bars=[candle(0),candle(1,100,102.5,98.5,101)]
        x=tracker.evaluate("LONG",100,99,102,bars)
        self.assertEqual(x["verdict"],"AMBIGUOUS_BOTH")
        self.assertIsNone(x["net_pct"])

    def test_no_fill_and_time_exit(self):
        never=[candle(i,103,103.5,102.5,103) for i in range(4)]
        self.assertEqual(tracker.evaluate("LONG",100,99,102,never)["verdict"],
                         "NOT_FILLED")
        sideways=[candle(0),candle(1,100,100.5,99.6,100.3)]
        x=tracker.evaluate("LONG",100,99,102,sideways)
        self.assertEqual(x["verdict"],"TIME_EXIT_6H")
        self.assertAlmostEqual(x["net_pct"],0.08)

    def test_strict_360_bar_history_validation(self):
        start=(1800000000//60+1)*60000
        bars=[[str(start+i*60000),"100","101","99","100","2"]
              for i in reversed(range(360))]
        payload={"retCode":0,"result":{"list":bars}}
        self.assertEqual(len(tracker._parse_bars(payload,start)),360)
        corrupt=json.loads(json.dumps(payload))
        corrupt["result"]["list"][150][0]=str(start+45000)
        with self.assertRaises(ValueError):
            tracker._parse_bars(corrupt,start)
        corrupt=json.loads(json.dumps(payload))
        corrupt["result"]["list"]=corrupt["result"]["list"][:-1]
        with self.assertRaises(ValueError):
            tracker._parse_bars(corrupt,start)


class ReadyJournalTests(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.db=Path(td.name)/"trades.db"
        p=patch.dict(os.environ,{"READY_OUTCOME_DB":str(self.db)})
        p.start()
        self.addCleanup(p.stop)
        self.sent=datetime(2026,10,16,10,0,
                           tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()

    def test_ack_journal_and_first_touch(self):
        self.assertTrue(tracker.record_sent(plan(),self.sent))
        self.assertFalse(tracker.record_sent(plan(),self.sent))
        self.assertEqual(tracker.summary()["counts"],{"PENDING":1})
        first=(int(self.sent//60)+1)*60000
        raw=[]
        for i in range(360):
            hi=102.5 if i==1 else 100.4
            lo=99.6
            raw.append([str(first+i*60000),"100",str(hi),str(lo),"100","10"])
        raw.reverse()
        class Response:
            def raise_for_status(self): return None
            def json(self): return {"retCode":0,"result":{"list":raw}}
        with patch.object(tracker.requests,"get",return_value=Response()) as request:
            self.assertFalse(tracker.advance(self.sent+5*3600))
            self.assertTrue(tracker.advance(self.sent+6*3600+240))
            self.assertEqual(request.call_count,1)
            self.assertEqual(request.call_args.kwargs["params"]["interval"],"1")
        data=tracker.summary()
        self.assertEqual(data["count"],1)
        self.assertEqual(data["counts"],{"TP1_FIRST":1})
        self.assertEqual(data["priced"],1)
        self.assertAlmostEqual(data["average_net_pct"],1.78)
        self.assertFalse(tracker.advance(self.sent+9*3600))

    def test_missing_candles_are_not_recorded_as_losses(self):
        tracker.record_sent(plan(),self.sent)
        class Response:
            def raise_for_status(self): return None
            def json(self): return {"retCode":0,"result":{"list":[]}}
        with patch.object(tracker.requests,"get",return_value=Response()):
            self.assertFalse(tracker.advance(self.sent+7*3600))
        self.assertEqual(tracker.summary()["counts"],{"PENDING":1})
        with tracker._connect() as db:
            self.assertEqual(db.execute(
                "SELECT attempts FROM ready_plan_outcomes").fetchone()[0],1)

    def test_daily_report_only_after_telegram_ack(self):
        tracker.record_sent(plan(),self.sent)
        after=datetime(2026,10,16,22,15,
                       tzinfo=ZoneInfo("Europe/Warsaw")).timestamp()
        delivered=[]
        with patch.object(tracker,"_last_summary_ts",after):
            self.assertFalse(tracker.report(after,lambda text:False))
            self.assertTrue(tracker.report(after,lambda text:delivered.append(text) or True))
            self.assertIn("Вход не достигнут",delivered[0])
            self.assertFalse(tracker.report(after+120,lambda text:True))
        self.assertEqual(len(delivered),1)

    def test_ready_dispatch_records_only_successful_telegram_delivery(self):
        import trend_confirmation as trend
        with tempfile.TemporaryDirectory() as td:
            alert_path=Path(td)/"limit.json"
            with patch.dict(os.environ,{"READY_ALERTS_STATE_FILE":str(alert_path)}):
                with patch.object(trend,"trend",return_value="UP"):
                    sample=dict(instId="BTCUSDT",direction_code="UP",
                        entry_type="EXPANSION_LONG",score=25,acc_score=3,
                        early_pressure_score=12,signal_mode="EXPANSION",late=False,
                        flags=["PRESSURE_UP","STRUCTURE_HH_HL"],
                        entry_price=100,price=100,stop=99,target=102,oi_change=.2)
                    self.assertFalse(trend.dispatch_ready(sample,lambda _:False,
                                                           self.sent))
                    self.assertEqual(tracker.summary()["count"],0)
                    self.assertTrue(trend.dispatch_ready(sample,lambda _:True,
                                                          self.sent))
                    self.assertEqual(tracker.summary()["count"],1)
                    self.assertEqual(tracker.summary()["counts"],{"PENDING":1})


if __name__=="__main__":
    unittest.main()
