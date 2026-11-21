"""Real TCP integration; model and endpoint fixtures, NOT native OS/HIL evidence."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import httpx
import uvicorn

from clikvm.app import create_app
from clikvm.config import Settings
from clikvm_node.config import NodeConfig
from clikvm_node.http_api import HttpAPIBackend
from clikvm_node.native import NativeBackend
from clikvm_node.rfb import RFBBackend
from clikvm_node.runner import GatewayClient, NodeRunner

from test_backends import APIHandler, RFBServer


@contextmanager
def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def gateway_server(settings):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = "http://127.0.0.1:" + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        limit = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() > limit:
                raise RuntimeError("Integration gateway did not start")
            time.sleep(0.01)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


class PlannerFixture(BaseHTTPRequestHandler):
    contexts = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = request["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        type(self).contexts.append(context)
        if context["history"]:
            plan = {"status": "done", "reason": "Scripted integration fixture completed", "action": None,
                    "requires_confirmation": False}
        else:
            action = ({"type": "api", "operation": "light.on", "arguments": {"brightness": 70}}
                      if context["target"]["transport"] == "http-api"
                      else {"type": "tap", "x": 1, "y": 1})
            plan = {"status": "action", "action": action, "reason": "Scripted test action", "requires_confirmation": False}
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(plan)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class GatewayIntegrationTests(unittest.TestCase):
    def run_loop(self, backend):
        PlannerFixture.contexts = []
        token = "test-node-credential-not-a-secret-123456789"
        operator = "test-operator-credential-not-a-secret-123456789"
        with tempfile.TemporaryDirectory() as tmp, http_server(PlannerFixture) as model:
            settings = Settings(state_dir=Path(tmp) / "gateway", device_tokens={"test-node": token},
                                operator_token=operator, model_endpoint=model + "/v1/chat/completions",
                                model_name="scripted-test-fixture", allow_http_upstreams=True)
            with gateway_server(settings) as base, patch.dict(os.environ, {"CLIK_NODE_TOKEN": token}):
                config = NodeConfig(base, "test-node", "CLIK_NODE_TOKEN", Path(tmp) / "node", "native", {})
                runner = NodeRunner(config, backend, GatewayClient(config), logger=lambda *a, **k: None)
                try:
                    runner.step()
                    first_frame = runner.frame
                    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + operator}, timeout=5, trust_env=False) as client:
                        response = client.post(f"/v1/sessions/{runner.sid}/intent",
                                               headers={"Idempotency-Key": str(uuid.uuid4())},
                                               json={"text": "Run the owned-device integration fixture", "frame_id": first_frame})
                        self.assertEqual(response.status_code, 200, response.text)
                        limit = time.monotonic() + 5
                        while time.monotonic() < limit:
                            runner.step()
                            status = client.get(f"/v1/sessions/{runner.sid}/status").json()
                            if status["state"] == "done":
                                break
                            time.sleep(0.02)
                        self.assertEqual(status["state"], "done", status)
                        self.assertGreater(runner.frame, first_frame)
                        self.assertEqual(status["profile"]["transport"], backend.profile["transport"])
                        self.assertEqual(runner.journal.last["status"], "succeeded")
                        self.assertEqual(runner.journal.last["disposition"], "acknowledged")
                        self.assertFalse(runner.journal.pending)
                        if backend.profile["observation_kind"] == "json-state":
                            data = client.get(f"/v1/sessions/{runner.sid}/observations/{runner.frame}").json()
                            self.assertEqual(data["state"]["brightness"], 70)
                            self.assertEqual(PlannerFixture.contexts[-1]["device_state"]["brightness"], 70)
                        else:
                            png = client.get(f"/v1/sessions/{runner.sid}/frames/{runner.frame}")
                            self.assertEqual(png.status_code, 200)
                            self.assertTrue(png.content.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertEqual(len(PlannerFixture.contexts), 2)
                finally:
                    runner.close()

    def test_native_code_path_with_gui_boundary_fixture_real_gateway_tcp(self):
        # NativeBackend capture/mapping/execute logic is real. OS APIs are fixtures.
        # This test must never be presented as Windows/macOS/X11 hardware validation.
        backend = NativeBackend({}, 1920, 4194304)
        events = []
        gui = SimpleNamespace(size=lambda: (10, 10), click=lambda x, y: events.append((x, y)))
        shot = SimpleNamespace(size=(10, 10), rgb=bytes([255, 87, 51]) * 100)
        capture = SimpleNamespace(grab=lambda region: shot, close=lambda: None)
        def open_fixture():
            backend.gui, backend.sct = gui, capture
            backend.logical_size = (10, 10)
            backend.monitor = {"left": 0, "top": 0, "width": 10, "height": 10}
        with patch.object(backend, "open", side_effect=open_fixture):
            self.run_loop(backend)
        self.assertEqual(events, [(1, 1)])

    def test_rfb_tcp_to_gateway_tcp_roundtrip(self):
        with RFBServer() as server:
            backend = RFBBackend({"host": "127.0.0.1", "port": server.port, "tls": False,
                                  "allow_plaintext": True, "allow_none_auth": True, "platform": "linux"})
            self.run_loop(backend)
            self.assertEqual(server.pointer_events, [(1, 1, 1), (0, 1, 1)])
            self.assertGreaterEqual(server.requests, 2)
            self.assertEqual(server.errors, [])

    def test_http_api_tcp_to_gateway_json_observation_roundtrip(self):
        APIHandler.changes, APIHandler.other_calls = [], 0
        with http_server(APIHandler) as api:
            backend = HttpAPIBackend({"base_url": api, "allow_http": True, "state_path": "/state",
                "operations": {"light.on": {"method": "POST", "path": "/fixed-action", "description": "Set one fixture light",
                    "requires_confirmation": False, "parameters_schema": {"type": "object",
                        "properties": {"brightness": {"type": "integer", "minimum": 1, "maximum": 255}},
                        "required": ["brightness"], "additionalProperties": False},
                    "body": {"brightness": {"$arg": "brightness"}, "entity_id": "light.office"}}}})
            self.run_loop(backend)
            self.assertEqual(APIHandler.changes, [{"brightness": 70, "entity_id": "light.office"}])


if __name__ == "__main__":
    unittest.main()
