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

    def test_h1_flat_h4_research_cohort_is_separate_and_not_automatic_alert(self):
        self.assertEqual((trend.MIN_N, trend.MIN_DAYS, trend.MIN_SYMBOLS),
                         (80, 5, 15))
        with patch.object(trend, "_fetch_trend",
                          side_effect=lambda symbol,interval,now:
                          "UP" if interval == 60 else "FLAT"):
            self.assertEqual(trend.trend("ETHUSDT", NOW), "UP_H4_FLAT")
        with patch.object(trend, "trend", return_value="UP_H4_FLAT"):
            self.assertEqual(trend.consider(signal(), NOW),
                             (False, "EDGE_UNPROVEN"))
        with study._connect() as db:
            self.assertEqual(db.execute(
                "SELECT trend FROM trend_gate_samples").fetchone()[0],
                "UP_H4_FLAT")
            for k in range(90):
                ts=NOW-15*86400+k*3*3600
                db.execute("""INSERT INTO trend_gate_samples
                    (symbol,side,bucket,ts,band,trend,status,verdict,net_pct,evaluated_ts)
                    VALUES (?,?,?,?,?,?,'DONE','TP_FIRST',1.28,?)""",
                    ("COIN"+str(k%25)+"USDT", "LONG", int(ts//5400),
                     ts, "GE20", "UP", ts+1800))
            self.assertTrue(trend._odds(
                db,"LONG","UP","GE20",NOW)["approved"])
            self.assertFalse(trend._odds(
                db,"LONG","UP_H4_FLAT","GE20",NOW)["approved"])

        # Unavailable or opposite H4 remains fail-closed.
        trend._trend_cache.clear()
        with patch.object(trend, "_fetch_trend",
                          side_effect=lambda symbol,interval,now:
                          "UP" if interval == 60 else "UNKNOWN"):
            self.assertEqual(trend.trend("ETHUSDT", NOW), "UNKNOWN")

    def test_forward_quality_report_keeps_aligned_and_flat_h4_distinct(self):
        from io import StringIO
        from contextlib import redirect_stdout
        with study._connect() as db:
            trend._setup(db)
            records=[
                ("UP", "LONG", "DONE", "TP_FIRST", 1.28),
                ("UP", "LONG", "DONE", "TIME_EXIT", -0.12),
                ("UP_H4_FLAT", "LONG", "DONE", "SL_FIRST", -1.22),
                ("UP_H4_FLAT", "LONG", "PENDING", None, None),
            ]
            for k, (state,side,status,verdict,net) in enumerate(records):
                ts=NOW-2*86400+k*5400
                db.execute("""INSERT INTO trend_gate_samples
                    (symbol,side,bucket,ts,band,trend,status,verdict,net_pct,evaluated_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    ("COIN"+str(k)+"USDT",side,int(ts//5400),ts,"GE20",
                     state,status,verdict,net,ts+1800 if status=="DONE" else None))
            db.commit()
            stats=trend.regime_quality_snapshot(db)
            self.assertEqual(len(stats),2)
            by={x["regime"]:x for x in stats}
            aligned=by["H1_H4_ALIGNED"]
            flat=by["H1_H4_FLAT"]
            self.assertEqual((aligned["seen"],aligned["done"],aligned["tp"],
                              aligned["positive"]),(2,2,1,1))
            self.assertAlmostEqual(aligned["mean_net"],(1.28-0.12)/2)
            self.assertEqual((flat["seen"],flat["done"],flat["pending"],
                              flat["sl"],flat["positive"]),(2,1,1,1,0))
            self.assertAlmostEqual(flat["mean_net"],-1.22)
            output=StringIO()
            with redirect_stdout(output):
                trend.report_regime_quality(db)
            self.assertIn("pooled_bands=true",output.getvalue())
            self.assertIn("no_trade_approval=true",output.getvalue())

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


class ReadyPlanTests(unittest.TestCase):
    def sample(self):
        return dict(instId="ETHUSDT",direction_code="UP",entry_type="EXPANSION_LONG",
                    score=22,acc_score=3,early_pressure_score=12,
                    signal_mode="EXPANSION",late=False,
                    flags=["PRESSURE_UP","STRUCTURE_HH_HL"],
                    entry_price=100,price=100,stop=99,
                    target=103,oi_change=.2)

    def test_levels_and_direction_required(self):
        sig=self.sample()
        plan=trend.ready_candidate(sig,NOW)
        self.assertEqual(plan["side"],"LONG")
        self.assertEqual(plan["tp1"],103)
        self.assertGreaterEqual(plan["rr"],1.8)
        for edits in ({"stop":101},{"target":99},{"direction_code":"DOWN"},
                      {"flags":["PRESSURE_UP"]},{"price":105},{"oi_change":None}):
            modified=dict(sig,**edits)
            self.assertIsNone(trend.ready_candidate(modified,NOW),edits)

    def test_ready_rejection_has_reason_and_no_delivery(self):
        import contextlib
        import io
        bad = self.sample()
        bad["stop"] = 105
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertIsNone(trend.ready_candidate(bad, NOW))
        self.assertIn("reason=risk_reward_or_level_order", output.getvalue())
        self.assertIn("symbol=ETHUSDT", output.getvalue())

    def test_visible_long_short_banners_in_ready_plans(self):
        long_plan = trend.ready_candidate(self.sample(), NOW)
        self.assertIsNotNone(long_plan)
        self.assertTrue(trend.ready_message(long_plan).startswith(
            "🟢 LONG — ПОКУПКА\\n"))
        short = self.sample()
        short.update(direction_code="DOWN", entry_type="EXPANSION_SHORT",
                     flags=["PRESSURE_DOWN", "STRUCTURE_LH_LL"],
                     stop=101, target=97)
        short_plan = trend.ready_candidate(short, NOW)
        self.assertIsNotNone(short_plan)
        self.assertTrue(trend.ready_message(short_plan).startswith(
            "🔴 SHORT — ПРОДАЖА\\n"))
        with self.assertRaises(ValueError):
            trend.ready_message(dict(long_plan, side="UNKNOWN"))

    def test_direction_banner_for_all_trade_notifications(self):
        # Import only this pure function, not the network-heavy scanner module.
        import ast
        from pathlib import Path
        source = Path(__file__).with_name("main.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        fn = next(n for n in module.body if isinstance(n, ast.FunctionDef)
                  and n.name == "trade_direction_banner")
        code = compile(ast.Module(body=[fn], type_ignores=[]),
                       "<direction-banner>", "exec")
        namespace = {}
        exec(code, namespace)
        banner = namespace["trade_direction_banner"]
        self.assertEqual(banner({"direction_code": "UP"}),
                         "🟢 LONG — ПОКУПКА")
        self.assertEqual(banner({"direction_code": "DOWN"}),
                         "🔴 SHORT — ПРОДАЖА")
        self.assertEqual(banner({"direction_code": "UNKNOWN"}), "")
        self.assertEqual(banner(None), "")

    def test_daily_cap_and_telegram_ack(self):
        import json
        from pathlib import Path
        sent=[]
        def send(msg):
            sent.append(msg)
            return True
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/"ready.json"
            with patch.dict(os.environ,{"READY_ALERTS_STATE_FILE":str(state)}):
                with patch.object(trend,"trend",return_value="UP"):
                    self.assertTrue(trend.dispatch_ready(self.sample(),send,NOW))
                    self.assertFalse(trend.dispatch_ready(self.sample(),send,NOW+3600))
                    self.assertEqual(len(sent),1)
                    self.assertEqual(json.loads(state.read_text())["count"],1)
                    sig=self.sample()
                    sig["instId"]="BTCUSDT"
                    self.assertTrue(trend.dispatch_ready(sig,lambda _:False,NOW+4000)==False)
                    self.assertEqual(json.loads(state.read_text())["count"],1)
                    self.assertTrue(trend.dispatch_ready(sig,send,NOW+4100))
                    self.assertEqual(json.loads(state.read_text())["count"],2)
                    for idx in range(3):
                        sig=self.sample()
                        sig["instId"]=f"COIN{idx}USDT"
                        self.assertTrue(trend.dispatch_ready(sig,send,NOW+7000+idx*3500))
                    sig=self.sample()
                    sig["instId"]="EXTRAUSDT"
                    self.assertFalse(trend.dispatch_ready(sig,send,NOW+19000))
                    self.assertEqual(json.loads(state.read_text())["count"],5)



if __name__=="__main__":
    unittest.main()
