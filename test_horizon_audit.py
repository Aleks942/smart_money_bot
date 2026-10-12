"""No-network 48h SmartMoney deferred observation regressions."""
import os
import tempfile
import unittest
from unittest.mock import patch

import horizon_audit as audit
import rejection_forward_study as root
import trend_confirmation as trend

NOW=1800000000.0


def signal(price=100):
    return {"instId":"TESTUSDT","price":price,
            "score":25,"direction_code":"UP",
            "entry_type":"CONTINUATION_LONG"}


def history(ts,due,high=100.2,low=99.8,close=100,both=False):
    first=(int(ts//900)+1)*900
    last=int(due//900)*900-900
    out=[]
    for i,at in enumerate(range(first,last+1,900)):
        hi=102 if both and i==1 else high
        lo=98 if both and i==1 else low
        out.append([str(at*1000),str(close),str(hi),str(lo),
                    str(close),"1","1","1","1"])
    return list(reversed(out))


class SmartMoneyHorizonTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for obj,name,val in (
            (root,"DB",os.path.join(tmp.name,"reject.sqlite3")),
            (audit,"_last_request",0.0),
            (audit,"_last_cleanup",0.0),
            (audit,"_last_report",0.0),
            (trend,"POLICY","CONFIRMED_ONLY")):
            p=patch.object(obj,name,val);p.start();self.addCleanup(p.stop)

    def test_strict_gate_journals_without_sending(self):
        with patch.object(trend,"trend",return_value="UP"):
            self.assertEqual(trend.consider(signal(),NOW),
                             (False,"EDGE_UNPROVEN"))
            self.assertEqual(trend.consider(signal(),NOW+10),
                             (False,"EDGE_UNPROVEN"))
        with audit._conn() as db:
            cases=db.execute("""SELECT symbol,side,entry_price
                FROM horizon_cases""").fetchall()
        self.assertEqual(cases,[("TESTUSDT","LONG",100)])
        self.assertEqual(audit.HORIZONS_H,(1,4,24,48))
        with patch.object(trend,"trend",return_value="DOWN"):
            self.assertEqual(trend.consider(signal(),NOW+60),
                             (False,"TREND_NOT_ALIGNED"))
        with audit._conn() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM horizon_cases").fetchone()[0],1)

    def test_bybit_bounds_fetch_to_due_time(self):
        ts=NOW+17
        due=ts+48*3600
        first=(int(ts//900)+1)*900
        class Response:
            def raise_for_status(self): return None
            def json(self):
                return {"retCode":0,"result":{"list":[
                    ["1800000000000","100","100.2","99.8","100","0","0"]]}}
        with patch.object(audit.requests,"get",return_value=Response()) as get:
            rows=audit._bybit("TESTUSDT",ts,due)
        kw=get.call_args.kwargs["params"]
        self.assertEqual(kw["interval"],"15")
        self.assertEqual(kw["start"],first*1000)
        self.assertLessEqual(kw["end"],int(due*1000))
        self.assertEqual(kw["limit"],200)
        self.assertEqual(rows[0][-1],"1")

    def test_intrabar_ambiguity_and_gaps_are_not_wins(self):
        ts=NOW+17
        due=ts+3600
        both=audit._analyze(history(ts,due,both=True),ts,due,"LONG",100)
        self.assertEqual(both[0],"AMBIGUOUS_SAME_BAR")
        self.assertIsNone(audit._analyze(history(ts,due)[1:],
                                          ts,due,"LONG",100))
        self.assertEqual(audit._analyze(history(ts,due,high=102),
                                         ts,due,"LONG",100)[0],
                         "TP_OBSERVED_AFTER_GAP")

    def test_no_week_and_bounded_due_worker(self):
        ts=NOW+17
        self.assertTrue(audit.record(signal(),ts))
        with patch.object(audit,"_bybit",return_value=history(ts,ts+3600)) as query:
            self.assertEqual(audit.advance(ts+3600+120),1)
            self.assertEqual(audit.advance(ts+3600+150),0)
            self.assertEqual(query.call_count,1)
        for h in (4,24,48):
            self.assertEqual(audit.advance(ts+h*3600+3700),1)
        with audit._conn() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM horizon_cases").fetchone()[0],1)
            horizons=db.execute("""SELECT horizon_h,status
                FROM horizon_outcomes ORDER BY horizon_h""").fetchall()
        self.assertEqual(horizons,[(1,"OK"),(4,"MISSING_LATE"),
                                   (24,"MISSING_LATE"),(48,"MISSING_LATE")])

    def test_48h_observes_only_summarized_results(self):
        ts=NOW+17
        self.assertTrue(audit.record(signal(),ts))
        for h in (1,4,24):
            self.assertEqual(audit.advance(ts+h*3600+3700),1)
        with patch.object(audit,"_bybit",return_value=history(ts,ts+48*3600)):
            self.assertEqual(audit.advance(ts+48*3600+120),1)
        with audit._conn() as db:
            row=db.execute("""SELECT horizon_h,status,verdict,net_close_pct
                FROM horizon_outcomes WHERE horizon_h=48""").fetchone()
            self.assertEqual(row[:3],(48,"OK","NO_OBSERVED_TOUCH"))
            self.assertAlmostEqual(row[-1],-0.22)
            names=[x[1] for x in db.execute("PRAGMA table_info(horizon_cases)")]
            self.assertNotIn("candles",names)


if __name__=="__main__":
    unittest.main()
