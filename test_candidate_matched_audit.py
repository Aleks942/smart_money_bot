"""Network-free regression checks for no-lookahead, descriptive cohort matching."""
import io
import sqlite3
import unittest
from contextlib import redirect_stdout
from candidate_matched_audit import compute_comparison, print_comparison

DAY = 86400


def record(cohort, day, hour, side, score, pct, symbol=""):
    ts = day * DAY + hour * 3600 + 600
    return (cohort, ts, side, score, pct, pct, pct)


class MatchedAuditTests(unittest.TestCase):
    def test_matches_day_four_hour_session_side_and_score(self):
        rows = [
            record("ACCEPT", 100, 1, "LONG", 25, 1.2),
            record("REJECT", 100, 1, "LONG", 22, 0.5),
            record("ACCEPT", 101, 1, "LONG", 27, 0.3),
            record("ACCEPT", 101, 1, "LONG", 21, 0.1),
            record("REJECT", 101, 1, "LONG", 29, -0.2),
            record("REJECT", 101, 1, "SHORT", 25, -30.0),
            record("REJECT", 101, 9, "LONG", 25, -30.0),
            record("REJECT", 101, 1, "LONG", 42, -30.0),
            record("ACCEPT", 102, 1, "LONG", 25, 50.0),
        ]
        v=compute_comparison(rows)[("LONG",30)]
        self.assertEqual((v["sent_n"],v["blocked_n"],v["strata"],v["days"]),
                         (3,2,2,2))
        self.assertAlmostEqual(v["sent_gross"], (1.2+0.3+0.1)/3)
        self.assertAlmostEqual(v["blocked_gross"], (0.5-0.2-0.2)/3)
        self.assertAlmostEqual(v["gross_difference"],0.5)
        self.assertEqual(v["status"],"COLLECTING")
        self.assertEqual(v["sent_positive_ref"],2)

    def test_no_matched_control_not_assumed_to_be_zero(self):
        v=compute_comparison([record("REJECT",100,1,"LONG",20,2.0)])[("LONG",5)]
        self.assertEqual((v["sent_n"],v["blocked_n"]),(0,0))
        self.assertIsNone(v["sent_gross"])
        self.assertIsNone(v["gross_difference"])

    def test_missing_and_nonfinite_bars_excluded(self):
        rows=[
            record("ACCEPT",100,1,"SHORT",15,-1.0),
            record("REJECT",100,1,"SHORT",15,0.5),
            ("ACCEPT",100*DAY+3600,"SHORT",15,float("nan"),None,0.8),
            ("REJECT",100*DAY+3600,"SHORT",15,None,None,None),
        ]
        out=compute_comparison(rows)
        self.assertEqual(out[("SHORT",5)]["sent_n"],1)
        self.assertEqual(out[("SHORT",10)]["sent_n"],1)
        self.assertEqual(out[("SHORT",30)]["sent_n"],2)

    def test_sqlite_report_is_read_only_and_labels_limitations(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE accepts (
                    ts REAL, side TEXT, score REAL, p5 REAL,p10 REAL,p30 REAL,
                    state TEXT);
                CREATE TABLE rejects (
                    ts REAL, side TEXT, score REAL, p5 REAL,p10 REAL,p30 REAL,
                    state TEXT);
            """)
            record_tuple=(100*DAY+3600, "LONG", 25, 1.0, 1.0, 1.0, "DONE")
            db.execute("INSERT INTO accepts VALUES (?,?,?,?,?,?,?)", record_tuple)
            db.execute("INSERT INTO rejects VALUES (?,?,?,?,?,?,?)",
                       (100*DAY+3600,"LONG",25,0.4,0.4,0.4,"DONE"))
            captured=io.StringIO()
            with redirect_stdout(captured):
                print_comparison(db,100*DAY+3700,ttl_sec=3*DAY)
            txt=captured.getvalue()
            self.assertIn("sent_n=1 blocked_n=1",txt)
            self.assertIn("no_causal_claim=true",txt)
            self.assertIn("selection_bias_possible=true",txt)
            self.assertIn("status=COLLECTING",txt)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM accepts").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
