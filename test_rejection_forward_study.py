"""Network-free regression coverage for the prospective rejection study."""
import io
import os
from contextlib import redirect_stdout
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import requests
import rejection_forward_study as study


def bybit_bars(first, close="100.2"):
    return {"retCode": 0, "result": {"list": [
        [str(first + i * 60000), "100", "101", "99", close]
        for i in reversed(range(30))
    ]}}


class RejectStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (("DB", os.path.join(self.tmp.name, "rejected.db")),
                            ("_started", True), ("_recent", {})):
            p = patch.object(study, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _record_due(self, side="LONG"):
        self.assertTrue(study.record("BTCUSDT", side, 100, 12, 8, 2,
                                     "WEAK", "EARLY", {"SPOT_CONFLICT_LONG"}))
        with study._connect() as db:
            db.execute("UPDATE rejects SET ts=?", (time.time()-2050,))
            return db.execute("""SELECT symbol,bucket,ts,side,attempts
                                 FROM rejects WHERE side=?""", (side,)).fetchone()

    def test_dedup_and_spot_metadata(self):
        self._record_due()
        self.assertFalse(study.record("BTCUSDT", "LONG", 100, 12, 8, 2,
                                      "WEAK", "EARLY", set()))
        with study._connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*), MAX(spot_conflict) FROM rejects").fetchone(),
                (1, 1))

    def test_invalid_side_or_price_is_ignored(self):
        self.assertFalse(study.record("BTCUSDT", "NEUTRAL", 100, 12, 8, 2,
                                      "WEAK", "EARLY", set()))
        self.assertFalse(study.record("BTCUSDT", "LONG", float("nan"), 12, 8, 2,
                                      "WEAK", "EARLY", set()))
        self.assertFalse(os.path.exists(study.DB))

    @patch("rejection_forward_study.requests.get")
    def test_forward_long_and_short_closed_bars(self, req):
        for side, expected in (("LONG", 0.2), ("SHORT", -0.2)):
            row = self._record_due(side)
            class FakeResponse:
                def raise_for_status(self):
                    pass
                def json(self):
                    return bybit_bars(req.call_args.kwargs["params"]["start"])
            req.return_value = FakeResponse()
            study._evaluate(row)
        with study._connect() as db:
            rows = db.execute("SELECT side,state,p5,p10,p30 FROM rejects ORDER BY side").fetchall()
        self.assertEqual(rows, [("LONG", "DONE", 0.2, 0.2, 0.2),
                                ("SHORT", "DONE", -0.2, -0.2, -0.2)])
        self.assertEqual(req.call_count, 2)

    def test_candle_gap_and_invalid_ohlc_are_rejected(self):
        payload = bybit_bars(60000)
        payload["result"]["list"][0][0] = "65000"
        with self.assertRaisesRegex(ValueError, "GAPPED_HISTORY"):
            study._parse_candles(payload, 60000)
        payload = bybit_bars(60000)
        payload["result"]["list"][0][3] = "-1"
        with self.assertRaisesRegex(ValueError, "INVALID_OHLC"):
            study._parse_candles(payload, 60000)

    @patch("rejection_forward_study.requests.get")
    def test_http_403_is_terminal_and_not_hammered(self, req):
        row = self._record_due()
        req.side_effect = requests.HTTPError(
            "blocked", response=SimpleNamespace(status_code=403))
        study._tick()
        with study._connect() as db:
            state, attempts = db.execute(
                "SELECT state, attempts FROM rejects").fetchone()
        self.assertEqual((state, attempts), ("FAILED", 1))
        study._tick()
        self.assertEqual(req.call_count, 1)

    @patch("rejection_forward_study.requests.get")
    def test_temporary_errors_back_off_then_fail(self, req):
        row = self._record_due()
        req.side_effect = requests.Timeout("offline")
        for n in range(study.RETRY_LIMIT):
            study._evaluate((*row[:4], n))
        with study._connect() as db:
            state, attempts, next_ts = db.execute(
                "SELECT state, attempts, next_attempt_ts FROM rejects").fetchone()
        self.assertEqual((state, attempts), ("FAILED", study.RETRY_LIMIT))
        self.assertGreater(next_ts, time.time())
        before = req.call_count
        study._tick()
        self.assertEqual(req.call_count, before)

    def test_edge_report_is_grouped_and_observational(self):
        now = time.time()
        with study._connect() as db:
            study._setup(db)
            for i in range(20):
                sample = 0.5 if i < 12 else -0.4
                db.execute("""INSERT INTO rejects (
                    symbol,bucket,ts,side,price,score,ep,acc,flow,
                    smart_money,spot_conflict,oi_wait,state,p5,p10,p30
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"T{i}USDT", int(now//1800), now-2000, "LONG",
                     100, 22 if i < 12 else 8, 5, 1, "WEAK",
                     "WEAK_SMART_MONEY" if i < 12 else "EARLY",
                     int(i < 12), int(i < 12),
                     "DONE", sample, sample, sample))
        output = io.StringIO()
        with redirect_stdout(output):
            study._edge_report(now)
        report=output.getvalue()
        self.assertIn("group=ALL side=LONG horizon=5m n=20", report)
        self.assertIn("group=SPOT_CONFLICT side=LONG horizon=30m n=12", report)
        self.assertIn("group=OI_WAIT side=LONG horizon=10m n=12", report)
        self.assertIn("positive_after_ref_cost=12/20", report)
        self.assertIn("overlapping_groups=true", report)
        with study._connect() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM rejects").fetchone()[0],20)

    def test_accepted_captured_only_for_valid_direction(self):
        candidate = {"instId":"BTCUSDT","price":100,
                     "score":19,"direction_code":"UP",
                     "entry_type":"LONG_CONFIRM"}
        self.assertTrue(study.record_accepted(candidate,context="SCALP"))
        self.assertFalse(study.record_accepted(candidate,context="SCALP"))
        invalid=dict(candidate,direction_code="FLAT",entry_type="UNKNOWN")
        self.assertFalse(study.record_accepted(invalid,context="SCALP"))
        with study._connect() as db:
            found=db.execute("""SELECT symbol,side,score,context
                                 FROM accepts""").fetchall()
        self.assertEqual(found,[("BTCUSDT","LONG",19,"SCALP")])
        with redirect_stdout(io.StringIO()):
            study._cleanup(time.time())

    @patch("rejection_forward_study.requests.get")
    def test_accepted_forward_outcome_uses_same_candle_math(self, req):
        candidate={"instId":"ETHUSDT","price":100,"score":22,
                   "direction_code":"DOWN","entry_type":"SHORT_CONFIRM"}
        self.assertTrue(study.record_accepted(candidate,"MAIN"))
        with study._connect() as db:
            stamp=time.time()-2050
            db.execute("UPDATE accepts SET ts=?",(stamp,))
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return bybit_bars(
                req.call_args.kwargs["params"]["start"])
        req.return_value=FakeResponse()
        study._tick()
        with study._connect() as db:
            status,p5,p10,p30=db.execute(
                "SELECT state,p5,p10,p30 FROM accepts").fetchone()
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM rejects").fetchone()[0],0)
        self.assertEqual((status,p5,p10,p30),("DONE",-0.2,-0.2,-0.2))
        self.assertEqual(req.call_count,1)

    def test_combined_cohort_reports_not_causal_comparison(self):
        now=time.time()
        with study._connect() as db:
            study._setup(db)
            for i in range(11):
                symbol=f"PAIR{i}USDT"
                record=(symbol,int(now//1800),now-2100,"LONG",100,15)
                db.execute("""INSERT INTO accepts
                    (symbol,bucket,ts,side,price,score,state,p5,p10,p30)
                    VALUES (?,?,?,?,?,?,'DONE',0.4,0.5,0.6)""",record)
                db.execute("""INSERT INTO rejects
                    (symbol,bucket,ts,side,price,score,ep,acc,flow,
                    smart_money,spot_conflict,oi_wait,state,p5,p10,p30)
                    VALUES (?,?,?,?,?,?,1,1,'NO','WEAK',0,0,'DONE',
                            -0.1,-0.2,-0.3)""",record)
        output=io.StringIO()
        with redirect_stdout(output):
            study._cohort_report(now)
        report=output.getvalue()
        self.assertIn("sent_n=11 blocked_n=11",report)
        self.assertIn("sent_mean_gross=+0.6000%",report)
        self.assertIn("blocked_mean_gross=-0.3000%",report)
        self.assertIn("cohorts_not_matched=true",report)
        self.assertIn("no_causal_filter_claim=true",report)

    def test_cleanup_bounded_does_not_touch_other_databases(self):
        with patch.object(study, "MAX_ROWS", 2):
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
                study.record(symbol, "LONG", 100, 10, 5, 1, "WEAK", "EARLY", set())
            study._cleanup(time.time())
        with study._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rejects").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
