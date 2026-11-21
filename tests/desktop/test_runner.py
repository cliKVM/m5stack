from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from clikvm_node.common import Backend, KEYS, png_observation
from clikvm_node.config import NodeConfig
from clikvm_node.runner import GatewayClient, Journal, NodeRunner


class BackendFixture(Backend):
    profile = {"platform": "linux", "transport": "native-agent", "observation_kind": "screenshot",
               "action_types": ["key", "wait", "screenshot"], "keys": KEYS, "api_operations": []}
    capabilities = ["screenshot", "keyboard", "native_agent"]

    def __init__(self, crash=False):
        self.executions = 0
        self.observations = 0
        self.crash = crash

    def open(self):
        pass

    def observe(self):
        self.observations += 1
        return png_observation(Image.new("RGB", (10, 10), (255, 87, 51)), 1920, 4194304)

    def execute(self, action, deadline):
        self.executions += 1
        if self.crash:
            raise KeyboardInterrupt("Simulated process termination, not a backend failure response")


class GatewayFixture(BaseHTTPRequestHandler):
    counters = {}
    receipts = {}
    requests_seen = []
    disconnect_first_ack = False
    disconnected = False
    action_sent = False
    session_counter = 0
    valid_ms = 60000
    next_delay = 0
    stale_frame = False
    duplicate = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        key = self.headers.get("Idempotency-Key")
        cls = type(self)
        cls.requests_seen.append((self.path, key, hashlib.sha256(raw).hexdigest()))
        if self.path.endswith("/sessions"):
            cls.session_counter += 1
            body = json.loads(raw)
            assert body["simulated"] is False and body["profile"]["transport"] == "native-agent"
            response = {"session_id": "session-" + str(cls.session_counter), "latest_frame": 0}
        elif self.path.endswith("/frames"):
            response = {"frame_id": int(self.headers["X-Frame-Id"])}
        elif self.path.endswith("/actions/next"):
            time.sleep(cls.next_delay)
            response = {"state": "idle", "action": None, "capture_requested": False}
            if not cls.action_sent or cls.duplicate:
                cls.action_sent = True
                response["action"] = {"action_id": "action-1", "lease_id": "lease-1", "based_on_frame": 9 if cls.stale_frame else 1,
                                      "valid_for_ms": cls.valid_ms, "command": {"type": "key", "key": "HOME"}, "after_action": "screenshot"}
        elif self.path.endswith("/result"):
            body = json.loads(raw)
            if key in cls.receipts:
                assert cls.receipts[key] == body, "Retry changed command result"
            cls.receipts[key] = body
            if cls.disconnect_first_ack and not cls.disconnected:
                cls.disconnected = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            response = {"status": body["status"]}
        else:
            self.send_error(404)
            return
        content = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        for field, value in {"receipts": {}, "requests_seen": [], "disconnect_first_ack": False,
                             "disconnected": False, "action_sent": False, "session_counter": 0,
                             "valid_ms": 60000, "next_delay": 0, "stale_frame": False, "duplicate": False}.items():
            setattr(GatewayFixture, field, value)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayFixture)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = NodeConfig("http://127.0.0.1:" + str(self.server.server_port), "test-node", "CLIK_NODE_TOKEN",
                                 Path(self.temp.name), "native", {})
        self.env = patch.dict(os.environ, {"CLIK_NODE_TOKEN": "test-node-token-only-not-real-1234567890"})
        self.env.start()
        self.runners = []

    def tearDown(self):
        for runner in self.runners:
            runner.close()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def runner(self, backend=None):
        runner = NodeRunner(self.config, backend or BackendFixture(), GatewayClient(self.config), logger=lambda *a, **k: None)
        self.runners.append(runner)
        return runner

    def test_lost_http_ack_retries_same_result_without_repeating_input(self):
        GatewayFixture.disconnect_first_ack = True
        runner = self.runner()
        runner.step()
        self.assertEqual(runner.backend.executions, 1)
        results = [(key, digest) for path, key, digest in GatewayFixture.requests_seen if path.endswith("/result")]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertFalse(runner.journal.pending)
        self.assertGreaterEqual(runner.frame, 2)

    def test_request_roundtrip_reduces_remaining_lease_before_input(self):
        GatewayFixture.valid_ms = 20
        GatewayFixture.next_delay = 0.05
        runner = self.runner()
        runner.step()
        self.assertEqual(runner.backend.executions, 0)
        self.assertEqual(runner.journal.last["status"], "failed")
        self.assertIn("Lease expired", runner.journal.last["detail"])

    def test_restart_marks_unfinished_execution_uncertain_never_reexecutes(self):
        runner = self.runner(BackendFixture(crash=True))
        with self.assertRaises(KeyboardInterrupt):
            runner.step()
        self.assertEqual(json.loads((Path(self.temp.name) / "pending.json").read_text())["status"], "executing")
        runner.close()
        self.runners.remove(runner)
        recovered = self.runner()
        recovered.step()
        self.assertEqual(recovered.backend.executions, 0)
        self.assertEqual(next(iter(GatewayFixture.receipts.values()))["status"], "uncertain")
        self.assertEqual(recovered.sid, "session-2")

    def test_expired_lease_is_failed_before_backend_input(self):
        GatewayFixture.valid_ms = 0
        runner = self.runner()
        runner.step()
        self.assertEqual(runner.backend.executions, 0)
        self.assertEqual(next(iter(GatewayFixture.receipts.values()))["status"], "failed")

    def test_stale_frame_is_failed_before_backend_input(self):
        GatewayFixture.stale_frame = True
        runner = self.runner()
        runner.step()
        self.assertEqual(runner.backend.executions, 0)
        self.assertEqual(next(iter(GatewayFixture.receipts.values()))["status"], "failed")

    def test_duplicate_completed_lease_is_acknowledged_without_input(self):
        GatewayFixture.duplicate = True
        runner = self.runner()
        runner.step()
        runner.step()
        self.assertEqual(runner.backend.executions, 1)
        self.assertEqual(len(GatewayFixture.receipts), 1)

    def test_single_owner_and_target_binding(self):
        runner = self.runner()
        with self.assertRaisesRegex(RuntimeError, "Another node"):
            Journal(self.config)
        runner.step()
        runner.close()
        self.runners.remove(runner)
        other = NodeConfig(self.config.gateway_url, "different-target", self.config.token_env, self.config.state_dir, "native", {})
        with self.assertRaisesRegex(RuntimeError, "another target"):
            Journal(other)


if __name__ == "__main__":
    unittest.main()
