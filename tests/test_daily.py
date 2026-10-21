"""Offline daily sync checks. No Garmin connection, real tokens, or task writes."""
from datetime import date
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("daily", ROOT.parent / "payload/DailySync/sync_daily.py")
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.rpe = 50
        self.fail = False
    def get_activities(self, start, limit):
        self.calls.append(("index", start))
        if start > 0:
            return []
        return [{"activityId": 1, "startTimeLocal": "2026-10-17 06:00:00", "distance": 5000,
                 "duration": 1800, "startLatitude": 1, "activityType": {"typeKey": "running"}},
                {"activityId": 2, "startTimeLocal": "2026-10-01 06:00:00", "distance": 3000,
                 "duration": 1000, "activityType": {"typeKey": "running"}}]
    def get_activity(self, activity_id):
        self.calls.append(("activity", activity_id))
        if self.fail:
            class GarminConnectTooManyRequestsError(Exception):
                pass
            raise GarminConnectTooManyRequestsError("Never disclose raw tokens or responses")
        return {"summaryDTO": {"directWorkoutRpe": self.rpe, "directWorkoutFeel": 0}}
    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args))
            if name == "get_sleep_data":
                return {"dailySleepDTO": {"sleepTimeSeconds": 27000}, "sleepMovement": [1, 2], "restingHeartRate": 50}
            if name == "get_hrv_data":
                return {"hrvSummary": {"lastNightAvg": 60}, "hrvReadings": [1]}
            if name == "get_activity_splits":
                return {"lapDTOs": [{"distance": 400, "duration": 120}]}
            return []
        return call


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="daily_test_", dir=ROOT)
        self.base = Path(self.temp.name)
        shutil.copyfile(ROOT.parent / "payload/historical_import.py", self.base / "historical_import.py")
        (self.base / "history_v1/archive").mkdir(parents=True)
        self.api = FakeAPI()
    def tearDown(self):
        assert self.base.resolve().parent == ROOT
        self.temp.cleanup()
    def read(self, name):
        return json.loads((self.base / "DailySync/data" / name).read_text())
    def run_sync(self):
        with patch("time.sleep"):
            return daily.sync(self.base, self.api, today=date(2026, 10, 17), delay=0)
    def test_refresh_and_dated_snapshot_and_retained_laps(self):
        self.assertEqual(self.run_sync(), 0)
        self.api.rpe = 70
        self.assertEqual(self.run_sync(), 0)
        data = self.read("latest_training.json")
        row = [x for x in data["activities"] if x["summary"]["activityId"] == 1][0]
        self.assertEqual(row["reported_effort_and_feeling"]["summaryDTO.directWorkoutRpe"], 70)
        self.assertEqual(row["reported_effort_and_feeling"]["summaryDTO.directWorkoutFeel"], 0)
        self.assertNotIn("startLatitude", str(data))
        self.assertNotIn("sleepMovement", str(data))
        self.assertNotIn("hrvReadings", str(data))
        self.assertEqual(len(list((self.base / "DailySync/data/daily").iterdir())), 1)
        detail = self.read("latest_workouts_details.json")
        recent_detail = [item for item in detail["activities"] if item["activityId"] == 1][0]
        self.assertTrue(recent_detail["laps"]["data"]["lapDTOs"])
        self.assertEqual(self.api.calls.count(("activity", "2")), 0)  # Daily mode does not backfill all history.
        self.assertEqual(self.api.calls.count(("activity", "1")), 2)  # Recent edits refreshed.
        self.assertTrue((self.base / "history_v1/archive/weight_daily/2026-10-17.json").exists())
    def test_rate_limit_preserves_success_and_outputs_partial(self):
        self.assertEqual(self.run_sync(), 0)
        old = self.read("sync_status.json")["last_successful_sync_utc"]
        original = self.read("latest_training.json")
        self.api.fail = True
        self.assertEqual(self.run_sync(), 1)
        status = self.read("sync_status.json")
        self.assertFalse(status["sync_complete"])
        self.assertEqual(status["last_successful_sync_utc"], old)
        self.assertEqual(self.read("latest_training.json"), original)
        self.assertTrue((self.base / "DailySync/data/partial_latest_training.json").exists())
        self.assertNotIn("Never disclose", json.dumps(status))
        self.assertFalse((self.base / "history_v1/.import.lock").exists())
    def test_shared_lock_prevents_overlapping_writes(self):
        lock = self.base / "history_v1/.import.lock"
        lock.touch()
        self.assertEqual(self.run_sync(), 2)
        self.assertEqual(self.api.calls, [])
        self.assertTrue(lock.exists())
    def test_empty_index_still_refreshes_wellness(self):
        self.api.get_activities = lambda *args: []
        self.assertEqual(self.run_sync(), 0)
        output = self.read("latest_training.json")
        self.assertEqual(output["activities"], [])
        self.assertEqual(len(output["daily_wellness"]), 7)

    def test_downtime_catches_new_activities_after_previous_record(self):
        history = daily.load_history(self.base)
        prior = self.base / "history_v1/archive/activities/3/summary.json"
        history.atomic_json(prior, {"status": "ok", "data": {
            "activityId": 3, "startTimeLocal": "2026-09-30 08:00:00", "activityType": {"typeKey": "running"}}})
        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.api.calls.count(("activity", "2")), 1)


if __name__ == "__main__":
    unittest.main()
