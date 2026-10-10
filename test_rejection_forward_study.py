import os
import tempfile
import unittest
from unittest.mock import patch
import rejection_forward_study as study

class RejectStudyTests(unittest.TestCase):
    def test_record_deduplicated_and_no_trades(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(study, "DB", os.path.join(d, "study.db")), patch.object(study, "_started", True):
                study.record("BTCUSDT", "LONG", 100, 12, 8, 2,
                             "WEAK", "EARLY", {"SPOT_CONFLICT_LONG"})
                study.record("BTCUSDT", "LONG", 100, 12, 8, 2,
                             "WEAK", "EARLY", {"SPOT_CONFLICT_LONG"})
                with study._connect() as db:
                    count, conflict = db.execute(
                        "SELECT COUNT(*), MAX(spot_conflict) FROM rejects").fetchone()
                self.assertEqual((count, conflict), (1, 1))
    def test_invalid_side_does_not_record(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(study, "DB", os.path.join(d, "study.db")), patch.object(study, "_started", True):
                study.record("BTCUSDT", "NEUTRAL", 100, 12, 8, 2, "WEAK", "EARLY", set())
                self.assertFalse(os.path.exists(study.DB))
if __name__ == "__main__":
    unittest.main()
