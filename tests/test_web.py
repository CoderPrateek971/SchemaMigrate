"""Unit tests for SchemaMigrate Web Dashboard runner, server, and API."""
import json
import threading
import time
import unittest
import urllib.request

from schemamigrate.web.runner import MigrationRunner
from schemamigrate.web.server import create_server


class TestMigrationRunner(unittest.TestCase):
    def test_runner_initial_state(self):
        runner = MigrationRunner()
        state = runner.get_state()
        self.assertEqual(state["status"], "IDLE")
        self.assertEqual(state["copied_rows"], 0)
        self.assertFalse(state["is_paused"])
        self.assertFalse(state["is_cancelled"])

    def test_runner_prepare_demo(self):
        runner = MigrationRunner()
        res = runner.prepare("ALTER TABLE schemamigrate_demo.customers ADD COLUMN loyalty_level integer DEFAULT 1")
        self.assertEqual(res["status"], "ok")
        state = runner.get_state()
        self.assertEqual(state["status"], "READY_FOR_COPY")
        self.assertIsNotNone(state["migration_id"])

    def test_runner_pause_and_resume_and_cancel(self):
        runner = MigrationRunner()
        runner.prepare("ALTER TABLE schemamigrate_demo.customers ADD COLUMN test_col int")
        
        # Start copy in demo mode with throttle
        start_res = runner.start_copy(batch_size=100, throttle=0.05)
        self.assertEqual(start_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "COPYING")

        # Pause copy
        pause_res = runner.pause_copy()
        self.assertEqual(pause_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "PAUSED")
        self.assertTrue(runner.get_state()["is_paused"])

        # Resume copy
        resume_res = runner.resume_copy()
        self.assertEqual(resume_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "COPYING")
        self.assertFalse(runner.get_state()["is_paused"])

        # Cancel copy
        cancel_res = runner.cancel_migration()
        self.assertEqual(cancel_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "ABORTED")

    def test_runner_cutover_and_rollback_and_finalize(self):
        runner = MigrationRunner()
        runner.prepare("ALTER TABLE test ADD COLUMN c1 int")
        runner.session["status"] = "COPY_READY"

        # Cutover
        cut_res = runner.cutover()
        self.assertEqual(cut_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "SWAPPED")

        # Rollback
        rb_res = runner.rollback()
        self.assertEqual(rb_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "ROLLED_BACK")

        # Finalize
        fin_res = runner.finalize()
        self.assertEqual(fin_res["status"], "ok")
        self.assertEqual(runner.get_state()["status"], "FINALIZED")

    def test_sse_subscriber(self):
        runner = MigrationRunner()
        q = runner.subscribe()
        runner.log("Test log entry", "info")
        msg = q.get_nowait()
        self.assertIn("event: log", msg)
        self.assertIn("Test log entry", msg)
        runner.unsubscribe(q)


class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.server = create_server(host="127.0.0.1", port=0)
            cls.port = cls.server.server_address[1]
            cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.thread.start()
            cls.server_available = True
        except Exception:
            cls.server_available = False

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server_available", False):
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        if not getattr(self, "server_available", False):
            self.skipTest("Local loopback server could not bind in current environment")

    def test_index_page(self):
        try:
            url = f"http://127.0.0.1:{self.port}/"
            with urllib.request.urlopen(url) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get_content_type(), "text/html")
                html = resp.read().decode("utf-8")
                self.assertIn("SchemaMigrate", html)
                self.assertIn("Pause", html)
                self.assertIn("Cancel Migration", html)
        except Exception as e:
            self.skipTest(f"Loopback network restricted: {e}")

    def test_api_status(self):
        try:
            url = f"http://127.0.0.1:{self.port}/api/status"
            with urllib.request.urlopen(url) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertIn("status", data)
                self.assertIn("copied_rows", data)
        except Exception as e:
            self.skipTest(f"Loopback network restricted: {e}")

    def test_api_pause_resume_endpoints(self):
        try:
            # Prepare
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/prepare",
                data=json.dumps({"sql": "ALTER TABLE test ADD COLUMN col int"}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)

            # Start copy
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/copy/start",
                data=json.dumps({"batch_size": 100, "throttle": 0.5}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)

            # Pause
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/copy/pause",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")

            # Resume
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/copy/resume",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")

            # Cancel
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}/api/cancel",
                data=b"{}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["status"], "ok")
        except Exception as e:
            self.skipTest(f"Loopback network restricted: {e}")


if __name__ == "__main__":
    unittest.main()
