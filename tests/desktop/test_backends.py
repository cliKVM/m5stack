from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from clikvm_node.common import ActionRejected, BackendError, Observation, validate_action
from clikvm_node.http_api import HttpAPIBackend
from clikvm_node.native import NativeBackend
from clikvm_node.rfb import RFBBackend


class RFBServer:
    def __init__(self, *, version=8, auth=False, malformed=False, resize=False, partial=False):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.version, self.auth, self.malformed, self.resize = version, auth, malformed, resize
        self.partial = partial
        self.pointer_events, self.key_events, self.errors = [], [], []
        self.requests = 0
        self.auth_ok = False
        self.client = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        if self.client:
            try:
                self.client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.client.close()
        self.listener.close()
        self.thread.join(timeout=2)

    def read(self, count):
        out = bytearray()
        while len(out) < count:
            data = self.client.recv(count - len(out))
            if not data:
                raise EOFError
            out.extend(data)
        return bytes(out)

    def fragmented(self, value):
        # Real TCP writes split the protocol at deliberately awkward boundaries.
        for start in range(0, len(value), 3):
            self.client.sendall(value[start:start + 3])

    def update(self, w=4, h=3):
        if self.partial and self.requests == 2:
            self.fragmented(b"\x00\x00\x00\x01" + struct.pack(">HHHHi", 0, 0, w, 1, 0) + bytes([31, 63, 127, 0]) * w)
            return
        if self.malformed:
            self.fragmented(b"\x00\x00\x00\x01" + struct.pack(">HHHHi", 0, 0, w + 1, h, 0))
            return
        if self.resize and self.requests == 2:
            self.fragmented(b"\x00\x00\x00\x01" + struct.pack(">HHHHi", 0, 0, 2, 2, -223))
            return
        if self.resize and self.requests > 2:
            w, h = 2, 2
        # Two rectangles verify that the first observation cannot be incomplete.
        self.fragmented(b"\x00\x00\x00\x02")
        for y, height in [(0, 1), (1, h - 1)]:
            raw = bytes([31, 63, 127, 0]) * w * height
            self.fragmented(struct.pack(">HHHHi", 0, y, w, height, 0) + raw)

    def run(self):
        try:
            self.client, _ = self.listener.accept()
            self.client.settimeout(2)
            self.fragmented(f"RFB 003.{self.version:03}\n".encode())
            assert self.read(12) == f"RFB 003.{self.version:03}\n".encode()
            security = 2 if self.auth else 1
            if self.version == 3:
                self.fragmented(struct.pack(">I", security))
            else:
                self.fragmented(bytes([1, security]))
                assert self.read(1) == bytes([security])
            if self.auth:
                self.fragmented(bytes(range(16)))
                from Crypto.Cipher import DES
                reverse = bytes(int(bin(i)[2:].zfill(8)[::-1], 2) for i in range(256))
                key = bytes(reverse[i] for i in b"testpass")
                self.auth_ok = self.read(16) == DES.new(key, DES.MODE_ECB).encrypt(bytes(range(16)))
                self.fragmented(struct.pack(">I", 0 if self.auth_ok else 1))
            elif self.version >= 8:
                self.fragmented(b"\x00" * 4)
            assert self.read(1) == b"\x01"
            pixel_format = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
            self.fragmented(struct.pack(">HH", 4, 3) + pixel_format + struct.pack(">I", 4) + b"test")
            while True:
                kind = self.read(1)[0]
                if kind == 0:
                    self.read(19)
                elif kind == 2:
                    header = self.read(3)
                    self.read(struct.unpack(">H", header[1:])[0] * 4)
                elif kind == 3:
                    self.read(9)
                    self.requests += 1
                    self.update()
                elif kind == 4:
                    down, _, symbol = struct.unpack(">BHI", self.read(7))
                    self.key_events.append((down, symbol))
                elif kind == 5:
                    self.pointer_events.append(struct.unpack(">BHH", self.read(5)))
                else:
                    raise AssertionError("Unexpected client message " + str(kind))
        except (EOFError, OSError):
            pass
        except BaseException as e:
            self.errors.append(e)


class RFBTests(unittest.TestCase):
    def backend(self, server, **options):
        return RFBBackend({"host": "127.0.0.1", "port": server.port, "tls": False,
                           "allow_plaintext": True, "allow_none_auth": True, **options})

    def test_actual_tcp_fragmented_screenshot_click_keys_all_versions(self):
        for version in [3, 7, 8]:
            with self.subTest(version=version), RFBServer(version=version) as server:
                backend = self.backend(server)
                try:
                    backend.open()
                    observed = backend.observe()
                    self.assertEqual((observed.width, observed.height), (4, 3))
                    with Image.open(io.BytesIO(observed.png)) as im:
                        self.assertEqual(im.getpixel((3, 2)), (127, 63, 31))
                    backend.execute({"type": "tap", "x": 2, "y": 1}, time.monotonic() + 2)
                    backend.execute({"type": "key", "key": "ENTER"}, time.monotonic() + 2)
                    backend.observe()  # Round-trip barrier: server consumed preceding input.
                    self.assertEqual(server.pointer_events, [(1, 2, 1), (0, 2, 1)])
                    self.assertEqual(server.key_events, [(1, 0xFF0D), (0, 0xFF0D)])
                    self.assertFalse(server.errors)
                finally:
                    backend.close()

    def test_vnc_password_challenge_over_real_tcp(self):
        with patch.dict(os.environ, {"CLIK_RFB_PASSWORD": "testpass"}), RFBServer(auth=True) as server:
            backend = self.backend(server, allow_none_auth=False)
            try:
                backend.open()
                self.assertTrue(server.auth_ok)
                self.assertEqual(backend.observe().width, 4)
            finally:
                backend.close()

    def test_resize_requires_full_new_frame(self):
        with RFBServer(resize=True) as server:
            backend = self.backend(server)
            try:
                backend.open()
                self.assertEqual(backend.observe().width, 4)
                observed = backend.observe()
                self.assertEqual((observed.width, observed.height), (2, 2))
                self.assertEqual(server.requests, 3)
            finally:
                backend.close()

    def test_out_of_bounds_rectangle_rejected(self):
        with RFBServer(malformed=True) as server:
            backend = self.backend(server)
            try:
                backend.open()
                with self.assertRaises(BackendError):
                    backend.observe()
            finally:
                backend.close()

    def test_plaintext_and_none_require_explicit_configuration(self):
        with self.assertRaises(ValueError):
            RFBBackend({"host": "127.0.0.1", "tls": False})
        with RFBServer() as server:
            backend = self.backend(server, allow_none_auth=False)
            try:
                with self.assertRaises(BackendError):
                    backend.open()
            finally:
                backend.close()

    def test_rfb_cannot_claim_url_launcher_or_outside_coordinates(self):
        with self.assertRaises(ValueError):
            RFBBackend({"host": "example.com", "platform": "android"})
        backend = RFBBackend({"host": "example.com"})
        observation = Observation("screenshot", width=4, height=3)
        for action in [{"type": "open_url", "url": "https://example.com"}, {"type": "tap", "x": 4, "y": 2}]:
            with self.assertRaises(ActionRejected):
                backend.validate(action, observation)

    def test_each_observation_requires_fully_refreshed_pixels(self):
        with RFBServer(partial=True) as server:
            backend = self.backend(server)
            try:
                backend.open()
                backend.observe()
                backend.observe()
                self.assertEqual(server.requests, 3)
                self.assertEqual(server.errors, [])
            finally:
                backend.close()

    def test_partial_press_and_release_failures_attempt_cleanup_and_close_transport(self):
        class PartialWriteSocket:
            def __init__(self, fail_at):
                self.messages, self.closed, self.fail_at = [], False, fail_at
            def settimeout(self, _):
                pass
            def sendall(self, data):
                self.messages.append(data)
                if len(self.messages) == self.fail_at:
                    raise TimeoutError("Socket may have delivered input before timeout")
            def close(self):
                self.closed = True
        cases = [({"type": "tap", "x": 1, "y": 1}, 1, [1, 0]),
                 ({"type": "key", "key": "APP_SWITCH"}, 1, [1, 0]),
                 ({"type": "key", "key": "APP_SWITCH"}, 3, [1, 1, 0, 0])]
        for action, failure, expected in cases:
            with self.subTest(action=action["type"], failure=failure):
                backend = RFBBackend({"host": "example.invalid"})
                sock = PartialWriteSocket(failure)
                backend.socket = sock
                backend.width, backend.height = 4, 3
                backend.observed_size = (4, 3)
                with self.assertRaises(TimeoutError):
                    backend.execute(action, time.monotonic() + 5)
                self.assertEqual([message[1] for message in sock.messages], expected)
                self.assertTrue(sock.closed)
                self.assertIsNone(backend.socket)


class APIHandler(BaseHTTPRequestHandler):
    changes = []
    other_calls = 0
    state_override = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/other")
            self.end_headers()
            return
        if self.path == "/other":
            type(self).other_calls += 1
        raw = json.dumps(type(self).state_override if type(self).state_override is not None else
                         {"state": "on", "brightness": type(self).changes[-1]["brightness"] if type(self).changes else 5}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).changes.append(body)
        raw = b"[]"
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class APITests(unittest.TestCase):
    def setUp(self):
        APIHandler.changes, APIHandler.other_calls, APIHandler.state_override = [], 0, None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), APIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.options = {"base_url": "http://127.0.0.1:" + str(self.server.server_port), "allow_http": True,
                        "state_path": "/state", "operations": {"light.on": {
                            "method": "POST", "path": "/fixed-action", "description": "A fixed test light",
                            "parameters_schema": {"type": "object", "properties": {"brightness": {"type": "integer", "minimum": 1, "maximum": 255}},
                                                  "required": ["brightness"], "additionalProperties": False},
                            "requires_confirmation": False, "body": {"brightness": {"$arg": "brightness"}, "entity_id": "light.office"}}}}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_real_http_state_action_and_observation(self):
        backend = HttpAPIBackend(self.options)
        try:
            backend.open()
            observed = backend.observe()
            self.assertEqual(observed.kind, "json-state")
            self.assertIsNone(observed.png)
            action = {"type": "api", "operation": "light.on", "arguments": {"brightness": 70}}
            backend.validate(action, observed)
            backend.execute(action, time.monotonic() + 2)
            self.assertEqual(backend.observe().state["brightness"], 70)
            self.assertEqual(APIHandler.changes, [{"brightness": 70, "entity_id": "light.office"}])
            self.assertNotIn("base_url", json.dumps(backend.profile))
        finally:
            backend.close()

    def test_schema_operation_and_url_injection_rejected_before_mutation(self):
        backend = HttpAPIBackend(self.options)
        try:
            backend.open()
            observed = backend.observe()
            bad = [
                {"type": "api", "operation": "https://evil.invalid", "arguments": {}},
                {"type": "api", "operation": "light.on", "arguments": {"brightness": 500}},
                {"type": "api", "operation": "light.on", "arguments": {"brightness": 10, "url": "https://evil.invalid"}},
            ]
            for action in bad:
                with self.assertRaises(ActionRejected):
                    backend.validate(action, observed)
            self.assertFalse(APIHandler.changes)
        finally:
            backend.close()

    def test_redirect_is_not_followed(self):
        options = {**self.options, "state_path": "/redirect"}
        backend = HttpAPIBackend(options)
        try:
            backend.open()
            with self.assertRaises(BackendError):
                backend.observe()
            self.assertEqual(APIHandler.other_calls, 0)
        finally:
            backend.close()

    def test_gateway_profile_and_observation_bounds_fail_locally(self):
        operation = self.options["operations"]["light.on"]
        with self.assertRaises(ValueError):
            HttpAPIBackend({**self.options, "operations": {"op" + str(i): operation for i in range(13)}})
        for forbidden in ("$ref", "$dynamicRef", "$recursiveRef"):
            schema = {**operation["parameters_schema"], forbidden: "https://outside.invalid/schema"}
            with self.assertRaises(ValueError):
                HttpAPIBackend({**self.options, "operations": {"light.on": {**operation, "parameters_schema": schema}}})
        with self.assertRaises(ValueError):
            HttpAPIBackend({**self.options, "operations": {"light.on": {**operation, "parameters_schema": {
                **operation["parameters_schema"], "description": "x" * 8200}}}})
        backend = HttpAPIBackend(self.options)
        try:
            backend.open()
            APIHandler.state_override = {"payload": "x" * 60001}
            with self.assertRaises(BackendError):
                backend.observe()
            state = {}
            for _ in range(18):
                state = {"nested": state}
            APIHandler.state_override = state
            with self.assertRaises(BackendError):
                backend.observe()
        finally:
            APIHandler.state_override = None
            backend.close()

    def test_external_paths_and_schema_references_are_rejected(self):
        for path in ["//evil.invalid/steal", "https://evil.invalid/steal"]:
            with self.assertRaises(ValueError):
                HttpAPIBackend({**self.options, "state_path": path})
        operation = self.options["operations"]["light.on"]
        operation["parameters_schema"]["properties"]["brightness"] = {"$ref": "https://evil.invalid/schema"}
        with self.assertRaises(ValueError):
            HttpAPIBackend(self.options)


class NativeContractTests(unittest.TestCase):
    def test_wayland_is_not_misrepresented_as_full_desktop_control(self):
        with patch("platform.system", return_value="Linux"), patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}):
            backend = NativeBackend({}, 1920, 4194304)
            with self.assertRaisesRegex(BackendError, "Wayland"):
                backend.open()

    def test_scaled_coordinates_match_logical_primary_display(self):
        backend = NativeBackend({}, 1920, 4194304)
        backend.logical_size = (1920, 1080)
        backend.observation_size = (960, 540)
        backend.monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        class GUI:
            @staticmethod
            def size(): return (1920, 1080)
        backend.gui = GUI()
        self.assertEqual(backend._point(480, 270), (960, 540))

    def test_x11_multi_monitor_root_mismatch_blocks_registration_and_input(self):
        monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        capture = SimpleNamespace(monitors=[{"left": 0, "top": 0, "width": 3840, "height": 1080}, monitor], close=lambda: None)
        gui = Mock()
        gui.size.return_value = (3840, 1080)
        with patch("platform.system", return_value="Linux"), patch.dict(os.environ, {
                "DISPLAY": ":0", "WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}), patch.dict(sys.modules, {
                "mss": SimpleNamespace(mss=lambda: capture), "pyautogui": gui}):
            backend = NativeBackend({}, 1920, 4194304)
            with self.assertRaisesRegex(BackendError, "root size differs"):
                backend.open()
            backend.observation_size = (1920, 1080)
            with self.assertRaisesRegex(BackendError, "root size differs"):
                backend.execute({"type": "tap", "x": 960, "y": 540}, time.monotonic() + 5)
            gui.click.assert_not_called()

    def test_unknown_action_fields_and_unsupported_text_are_rejected(self):
        backend = NativeBackend({}, 1920, 4194304)
        for action in [{"type": "tap", "x": 1, "y": 1, "shell": "anything"},
                       {"type": "text", "text": "żółw"}, {"type": "text", "text": "%s"},
                       {"type": "open_url", "url": "file:///etc/passwd"}]:
            with self.assertRaises(ActionRejected):
                backend.validate(action, Observation("screenshot", width=10, height=10))


if __name__ == "__main__":
    unittest.main()
