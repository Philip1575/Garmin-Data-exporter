"""Offline checks; never contact Garmin or access saved login tokens."""
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("history", Path(__file__).resolve().parent.parent / "payload/historical_import.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class API:
    def __init__(self):
        self.calls = 0

    def good(self):
        self.calls += 1
        return {"value": 0}

    def no_data(self):
        self.calls += 1
        return None

    def broken(self):
        self.calls += 1
        raise ConnectionError("Sensitive response should not be written")


class Checks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="history_test_", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temporary.name)
        self.api = API()
        self.importer = module.Importer(self.api, self.root, delay=0)

    def tearDown(self):
        assert self.root.resolve().parent == Path(__file__).resolve().parent
        self.temporary.cleanup()

    def test_resume_and_failures(self):
        self.assertEqual(self.importer.fetch("one", "good"), {"value": 0})
        self.importer.fetch("one", "good")
        self.importer.fetch("empty", "no_data")
        self.importer.fetch("empty", "no_data")
        self.assertEqual(self.api.calls, 2)
        self.importer.fetch("failure", "broken")
        self.importer.fetch("failure", "broken")
        self.assertEqual(self.api.calls, 4)
        saved = module.read_json(self.root / "archive/failure.json")
        self.assertEqual(saved["status"], "failed")
        self.assertNotIn("Sensitive", json.dumps(saved))

    def test_rate_limit_stops_and_checkpoints(self):
        class GarminConnectTooManyRequestsError(Exception):
            pass
        self.api.blocked = lambda: (_ for _ in ()).throw(GarminConnectTooManyRequestsError())
        with self.assertRaises(module.StopImport):
            self.importer.fetch("rate", "blocked")
        self.assertEqual(module.read_json(self.root / "archive/rate.json")["status"], "failed")

    def test_sleep_hrv_compaction_and_effort(self):
        sleep = {"dailySleepDTO": {"sleepScores": {"overall": {"value": 80}}},
                 "restingHeartRate": 49, "sleepMovement": [1, 2]}
        self.assertNotIn("sleepMovement", module.compact_sleep(sleep))
        self.assertEqual(module.compact_sleep(sleep)["restingHeartRate"], 49)
        self.assertNotIn("hrvReadings", module.compact_hrv({"hrvSummary": {"lastNightAvg": 52}, "hrvReadings": [1]}))
        self.assertEqual(module.reported_effort({"summaryDTO": {"directWorkoutRpe": 0, "perceivedEffort": None, "feelings": 3}})["summaryDTO.feelings"], 3)
        self.assertIsNone(module.reported_effort({"perceivedEffort": None})["perceivedEffort"])
        self.assertEqual(module.reported_effort({"directWorkoutRpe": 0})["directWorkoutRpe"], 0)

    def test_export_and_privacy(self):
        self.importer.save_summary({"activityId": 1, "startTimeLocal": "2026-04-01 07:00:00",
                                    "startLatitude": 1, "ownerId": 2, "distance": 5000,
                                    "eventType": {"typeKey": "race"}})
        module.atomic_json(self.root / "archive/activities/1/activity.json", {
            "status": "ok", "data": {"summaryDTO": {"perceivedEffort": 7, "feeling": None}}})
        module.atomic_json(self.root / "archive/activities/1/laps.json", {
            "status": "ok", "data": {"lapDTOs": [{"distance": 400, "startLongitude": 1}]}})
        self.importer.export(["1"], date(2026, 4, 1), date(2026, 4, 1), True, "finished")
        saved = module.read_json(self.root / "history_for_review.json")
        self.assertTrue(saved["activities"][0]["marked_as_race"])
        self.assertEqual(saved["activities"][0]["laps"]["data"]["lapDTOs"][0]["distance"], 400)
        self.assertNotIn("startLatitude", json.dumps(saved))
        self.assertNotIn("startLongitude", json.dumps(saved))
        self.assertNotIn("ownerId", json.dumps(saved))
        self.importer.export([], date(2026, 4, 1), date(2026, 4, 1), False, "interrupted")
        self.assertEqual(len(module.read_json(self.root / "history_for_review.json")["activities"]), 1)
        self.assertTrue((self.root / "history_partial_for_review.json").exists())

    def test_pagination_dedup_and_date_filter(self):
        rows = [{"activityId": 1, "startTimeLocal": "2026-04-02 07:00:00"},
                {"activityId": 1, "startTimeLocal": "2026-04-02 07:00:00"},
                {"activityId": 2, "startTimeLocal": "2026-03-31 07:00:00"}]
        self.api.get_activities = lambda *args: rows
        result = self.importer.discover(date(2026, 4, 1), date(2026, 4, 3))
        self.assertEqual([item["activityId"] for item in result], [1])

    def test_full_run_and_resume_without_real_garmin(self):
        calls = []
        class FakeGarmin:
            def __init__(self, **kwargs):
                pass
            def login(self, tokenstore):
                return None, None
            def get_activities(self, start, limit):
                calls.append("index")
                return [{"activityId": 1, "startTimeLocal": "2026-04-01 08:00:00",
                         "activityType": {"typeKey": "strength_training"}}] if start == 0 else []
            def __getattr__(self, name):
                def result(*args, **kwargs):
                    calls.append(name)
                    if name == "get_sleep_data":
                        return {"dailySleepDTO": {"calendarDate": "2026-04-01"}, "restingHeartRate": 50}
                    return {"test": True}
                return result
        args = ["--output", str(self.root / "output"), "--token-dir", str(self.root / "tokens"),
                "--since", "2026-04-01", "--until", "2026-04-01"]
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=FakeGarmin)}), patch.object(module.time, "sleep"):
            self.assertEqual(module.main(args), 0)
            first_count = len(calls)
            self.assertEqual(module.main(args), 0)
        self.assertEqual(len(calls) - first_count, 2)  # Only index pages refreshed.
        output = module.read_json(self.root / "output/history_for_review.json")
        self.assertEqual(output["status"], "finished")
        self.assertEqual(len(output["activities"]), 1)
        self.assertIn("exercise_sets", output["activities"][0])
        self.assertFalse((self.root / "output/.import.lock").exists())


if __name__ == "__main__":
    unittest.main()
