"""Offline tests for starter kit. No real Garmin login or scheduled-task writes."""
from contextlib import redirect_stdout
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "payload"


def read_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="starter_test_", dir=ROOT)
        self.parent = Path(self.temp.name)
        self.base = self.parent / "Training"
        self.base.mkdir()
        (self.base / "DailySync").mkdir()
        for relative in ["historical_import.py", "tools.py", "DailySync/sync_daily.py"]:
            shutil.copyfile(PAYLOAD / relative, self.base / relative)
        (self.base / "history_v1/archive").mkdir(parents=True)
        self.tokens = self.parent / "Private" / "tokens"
        self.config = {"application": "garmin-training-starter", "install_root": str(self.base),
                       "token_dir": str(self.tokens), "task_name": "test-task", "daily_time": "12:00",
                       "windows_timezone": "test-local", "display_units": "km"}
        (self.base / "starter-kit.json").write_text(json.dumps(self.config))
        self.tools = read_module("starter_tools_test", self.base / "tools.py")
        self.daily = read_module("starter_daily_test", self.base / "DailySync/sync_daily.py")
    def tearDown(self):
        assert self.parent.resolve().parent == ROOT
        self.temp.cleanup()

    def test_config_uses_own_profile_and_rejects_tokens_in_workspace(self):
        self.assertEqual(self.tools.settings(self.base)["token_dir"], str(self.tokens))
        self.config["token_dir"] = str(self.base / "secrets")
        (self.base / "starter-kit.json").write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            self.tools.settings(self.base)

    def test_config_rejects_moved_installation(self):
        self.config["install_root"] = str(self.parent / "wrong")
        (self.base / "starter-kit.json").write_text(json.dumps(self.config))
        with self.assertRaises(ValueError):
            self.tools.settings(self.base)

    def test_history_default_is_current_year(self):
        command = self.tools.history_arguments(self.config, self.base, today=date(2026, 10, 18))
        self.assertEqual(command[-2:], ["--since", "2026-01-01"])

    def test_history_custom_date_reaches_import_command(self):
        self.config["history_since"] = "2026-04-01"
        command = self.tools.history_arguments(self.config, self.base, today=date(2026, 10, 18))
        self.assertEqual(command[-2:], ["--since", "2026-04-01"])
        self.assertIn(str(self.tokens), command)

    def test_history_all_omits_date_filter(self):
        for value in (None, "ALL"):
            self.config["history_since"] = value
            self.assertNotIn("--since", self.tools.history_arguments(self.config, self.base, today=date(2026, 10, 18)))

    def test_history_rejects_invalid_and_future_dates(self):
        for value in ("2026-02-30", "2026-10-19", "20260101", "not a date"):
            self.config["history_since"] = value
            with self.assertRaises(ValueError):
                self.tools.history_arguments(self.config, self.base, today=date(2026, 10, 18))

    def test_daily_main_uses_explicit_tokens_and_logs(self):
        login_paths = []
        class FakeGarmin:
            def __init__(self, **kwargs):
                pass
            def login(self, path):
                login_paths.append(path)
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=FakeGarmin)}), patch.object(self.daily, "sync", return_value=0):
            self.assertEqual(self.daily.main(["--base", str(self.base), "--token-dir", str(self.tokens)]), 0)
        self.assertEqual(login_paths, [str(self.tokens.resolve())])
        self.assertTrue(list((self.base / "DailySync/logs").glob("*.log")))

    def test_login_rate_limit_does_not_prompt_again(self):
        self.tokens.mkdir(parents=True)
        class GarminConnectTooManyRequestsError(Exception):
            pass
        class FakeGarmin:
            def __init__(self, **kwargs):
                pass
            def login(self, path):
                raise GarminConnectTooManyRequestsError("SENSITIVE DATA NOT TO BE LOGGED")
        stream = io.StringIO()
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=FakeGarmin)}), patch("builtins.input", side_effect=AssertionError("No prompt allowed")), redirect_stdout(stream):
            self.assertEqual(self.tools.login(self.config), 1)
        self.assertNotIn("SENSITIVE", stream.getvalue())

    def test_first_login_hidden_password_and_private_dump(self):
        dumps = []
        class FakeGarmin:
            def __init__(self, **kwargs):
                self.password = kwargs.get("password")
                self.client = SimpleNamespace(dump=lambda path: dumps.append(path))
            def login(self, path):
                return None, None
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=FakeGarmin)}), patch("builtins.input", return_value="person@example.invalid"), patch.object(self.tools, "getpass", return_value="not-a-real-password"), redirect_stdout(io.StringIO()):
            self.assertEqual(self.tools.login(self.config), 0)
        self.assertEqual(dumps, [str(self.tokens)])
        self.assertFalse(list(self.base.rglob("*token*")))

    def test_checkin_skip_and_save(self):
        with patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertEqual(self.tools.checkin(self.base), 0)
        self.assertFalse((self.base / "checkins").exists())
        with patch("builtins.input", side_effect=["none", "low", "moderate", "rested", "", "", "30 minutes"]), redirect_stdout(io.StringIO()):
            self.assertEqual(self.tools.checkin(self.base), 0)
        files = list((self.base / "checkins").rglob("*.json"))
        self.assertEqual(len(files), 1)
        entry = json.loads(files[0].read_text())
        self.assertEqual(entry["pain"], "none")
        self.assertIsNone(entry["fueling"])

    def test_publish_baseline_is_not_daily_sync(self):
        history = self.tools.module_at("test_hist", self.base / "historical_import.py")
        history.atomic_json(self.base / "history_v1/import_status.json", {
            "status": "finished", "activity_index_complete": True, "updated_at_utc": "2026-10-17T20:00:00+00:00",
            "sections": {"one": {"status": "ok"}}})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.tools.publish_baseline(self.base), 0)
        status = json.loads((self.base / "DailySync/data/sync_status.json").read_text())
        self.assertEqual(status["status"], "baseline_ready")
        self.assertIsNone(status["last_successful_sync_utc"])
        self.assertTrue((self.base / "DailySync/data/latest_training.json").exists())

    def test_failed_login_preserves_last_success(self):
        prior = "2026-10-17T12:00:00+00:00"
        path = self.base / "DailySync/data/sync_status.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"last_successful_sync_utc": prior}))
        class FakeGarmin:
            def __init__(self, **kwargs):
                pass
            def login(self, path):
                raise ValueError("SENSITIVE DO NOT LOG")
        with patch.dict(sys.modules, {"garminconnect": SimpleNamespace(Garmin=FakeGarmin)}):
            self.assertEqual(self.daily.main(["--base", str(self.base), "--token-dir", str(self.tokens)]), 1)
        result = json.loads(path.read_text())
        self.assertEqual(result["status"], "login_failed")
        self.assertEqual(result["last_successful_sync_utc"], prior)
        self.assertNotIn("SENSITIVE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
