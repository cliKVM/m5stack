from __future__ import annotations

import socket
import ssl
import struct
import time

from PIL import Image

from .common import Backend, BackendError, KEYS, Observation, png_observation, remaining
from .config import env_secret


class RFBBackend(Backend):
    """Bounded RFB 3.3/3.7/3.8 client, raw encoding, optional outer TLS.

    Raw is mandatory in RFB. Compression, audio, clipboard and proprietary
    extensions are deliberately not advertised. A display resize invalidates
    the old pixel coverage until a complete new frame arrives.
    """

    def __init__(self, options: dict, max_width: int = 1920, max_bytes: int = 4194304):
        allowed = {"host", "port", "tls", "ca_file", "server_name", "allow_plaintext", "allow_none_auth", "password_env", "platform", "max_pixels"}
        if set(options) - allowed:
            raise ValueError("Unknown RFB option")
        self.options = options
        self.host, self.port = options["host"], int(options.get("port", 5900))
        if not isinstance(self.host, str) or not 1 <= len(self.host) <= 253 or not 1 <= self.port <= 65535:
            raise ValueError("Invalid RFB address")
        self.tls = options.get("tls", True)
        if type(self.tls) is not bool:
            raise ValueError("tls must be a boolean")
        if not self.tls and options.get("allow_plaintext") is not True:
            raise ValueError("Plain RFB requires explicit allow_plaintext; prefer an SSH tunnel or verified TLS")
        if options.get("ca_file") is False:
            raise ValueError("TLS certificate verification cannot be disabled")
        self.max_pixels = int(options.get("max_pixels", 8294400))
        if not 1 <= self.max_pixels <= 16777216:
            raise ValueError("RFB max_pixels must not exceed 16 megapixels")
        target_platform = options.get("platform", "unknown")
        if target_platform not in {"windows", "macos", "linux", "unknown"}:
            raise ValueError("Unsupported declared RFB target platform")
        self.profile = {"platform": target_platform, "transport": "rfb", "observation_kind": "screenshot",
                        "action_types": ["tap", "swipe", "key", "text", "wait", "screenshot"],
                        "keys": KEYS, "api_operations": []}
        self.capabilities = ["screenshot", "keyboard", "mouse", "rfb"]
        self.max_width, self.max_bytes = max_width, max_bytes
        self.socket = None
        self.width = self.height = 0
        self.image = None
        self.coverage = None
        self.covered = 0
        self.observed_size = None
        self.pointer = (0, 0)

    def _recv(self, count: int, deadline: float) -> bytes:
        if count < 0 or count > self.max_pixels * 4 + 4096:
            raise BackendError("RFB payload exceeds configured bound")
        output = bytearray()
        while len(output) < count:
            self.socket.settimeout(remaining(deadline))
            block = self.socket.recv(min(count - len(output), 65536))
            if not block:
                raise BackendError("RFB peer disconnected")
            output.extend(block)
        return bytes(output)

    def _send(self, data: bytes, deadline: float) -> None:
        self.socket.settimeout(remaining(deadline))
        self.socket.sendall(data)

    def _resize(self, width: int, height: int) -> None:
        if not 1 <= width <= 16384 or not 1 <= height <= 16384 or width * height > self.max_pixels:
            raise BackendError("RFB dimensions exceed configured bound")
        self.width, self.height = width, height
        self.image = Image.new("RGB", (width, height))
        self.coverage, self.covered = bytearray(width * height), 0
        self.observed_size = None

    def open(self) -> None:
        self.close()
        deadline = time.monotonic() + 15
        sock = socket.create_connection((self.host, self.port), timeout=10)
        try:
            if self.tls:
                context = ssl.create_default_context(cafile=self.options.get("ca_file"))
                sock = context.wrap_socket(sock, server_hostname=self.options.get("server_name", self.host))
            self.socket = sock
            version = self._recv(12, deadline)
            if not version.startswith(b"RFB 003.") or version[-1:] != b"\n":
                raise BackendError("Peer is not a supported RFB server")
            minor = int(version[8:11])
            negotiated = 8 if minor >= 8 else 7 if minor >= 7 else 3 if minor >= 3 else 0
            if not negotiated:
                raise BackendError("Unsupported RFB version")
            self._send(f"RFB 003.{negotiated:03}\n".encode(), deadline)
            password = env_secret(self.options.get("password_env", "CLIK_RFB_PASSWORD"), optional=True)
            if negotiated == 3:
                choices = [struct.unpack(">I", self._recv(4, deadline))[0]]
            else:
                count = self._recv(1, deadline)[0]
                if not count:
                    raise BackendError("RFB server refused security negotiation")
                choices = list(self._recv(count, deadline))
            if 2 in choices and password:
                security = 2
            elif 1 in choices and self.options.get("allow_none_auth") is True:
                security = 1
            else:
                raise BackendError("RFB authentication unavailable: configure password or explicitly permit None in a protected connection")
            if negotiated != 3:
                self._send(bytes([security]), deadline)
            if security == 2:
                try:
                    from Crypto.Cipher import DES
                except ImportError as e:
                    raise BackendError("Install clikvm-node[rfb] for legacy VNC password authentication") from e
                try:
                    secret = password.encode("latin-1")[:8].ljust(8, b"\0")
                except UnicodeEncodeError as e:
                    raise BackendError("VNC password must be representable as legacy Latin-1") from e
                key = bytes(int(f"{b:08b}"[::-1], 2) for b in secret)
                challenge = self._recv(16, deadline)
                self._send(DES.new(key, DES.MODE_ECB).encrypt(challenge), deadline)
            if security == 2 or negotiated >= 8:
                result = struct.unpack(">I", self._recv(4, deadline))[0]
                if result:
                    raise BackendError("RFB authentication failed")
            self._send(b"\x01", deadline)  # Shared desktop; do not evict another viewer.
            header = self._recv(24, deadline)
            width, height = struct.unpack(">HH", header[:4])
            name_length = struct.unpack(">I", header[20:24])[0]
            if name_length > 4096:
                raise BackendError("RFB desktop-name field exceeds limit")
            self._recv(name_length, deadline)  # Deliberately not logged.
            self._resize(width, height)
            pixel_format = struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
            self._send(b"\x00\x00\x00\x00" + pixel_format, deadline)
            self._send(struct.pack(">BBHii", 2, 0, 2, 0, -223), deadline)  # Raw, DesktopSize.
        except Exception:
            sock.close()
            self.socket = None
            raise

    def close(self) -> None:
        if self.socket:
            try:
                self.socket.close()
            finally:
                self.socket = None
        self.image = None
        self.observed_size = None

    def _request_frame(self, deadline: float) -> None:
        self._send(struct.pack(">BBHHHH", 3, 0, 0, 0, self.width, self.height), deadline)

    def _mark_coverage(self, x: int, y: int, width: int, height: int) -> None:
        if self.coverage is None:
            return
        filled = b"\x01" * width
        for row in range(y, y + height):
            start = row * self.width + x
            self.covered += self.coverage[start:start + width].count(0)
            self.coverage[start:start + width] = filled
        if self.covered == self.width * self.height:
            self.coverage = None

    def observe(self) -> Observation:
        if not self.socket:
            raise BackendError("RFB connection is not open")
        deadline = time.monotonic() + 15
        # Every published frame must be refreshed by this full request; old
        # cached pixels cannot turn a partial update into a fresh observation.
        self.coverage, self.covered = bytearray(self.width * self.height), 0
        self._request_frame(deadline)
        messages = 0
        while messages < 4096:
            messages += 1
            kind = self._recv(1, deadline)[0]
            if kind == 0:
                _, rectangles = struct.unpack(">BH", self._recv(3, deadline))
                if rectangles > 4096:
                    raise BackendError("Too many RFB rectangles")
                changed = False
                resized = False
                payload_pixels = 0
                for _ in range(rectangles):
                    x, y, width, height, encoding = struct.unpack(">HHHHi", self._recv(12, deadline))
                    if encoding == -223:
                        self._resize(width, height)
                        resized = True
                        continue
                    if encoding != 0:
                        raise BackendError("RFB server sent an unadvertised encoding")
                    if x + width > self.width or y + height > self.height:
                        raise BackendError("RFB rectangle is outside framebuffer")
                    payload_pixels += width * height
                    if payload_pixels > self.max_pixels * 4:
                        raise BackendError("RFB update byte budget exceeded")
                    if not width or not height:
                        continue
                    raw = self._recv(width * height * 4, deadline)
                    tile = Image.frombytes("RGB", (width, height), raw, "raw", "BGRX")
                    self.image.paste(tile, (x, y))
                    self._mark_coverage(x, y, width, height)
                    changed = True
                if changed and self.coverage is None:
                    observation = png_observation(self.image, self.max_width, self.max_bytes)
                    self.observed_size = (observation.width, observation.height)
                    return observation
                # A size-only or partial initial update is not a usable screen.
                if resized or self.coverage is not None or not changed:
                    self._request_frame(deadline)
            elif kind == 1:
                _, _, count = struct.unpack(">BHH", self._recv(5, deadline))
                self._recv(count * 6, deadline)
            elif kind == 2:
                continue
            elif kind == 3:
                length = struct.unpack(">I", self._recv(7, deadline)[3:])[0]
                if length > 1048576:
                    raise BackendError("RFB clipboard field exceeds limit")
                self._recv(length, deadline)  # Ignore clipboard contents entirely.
            else:
                raise BackendError("Unsupported RFB server message")
        raise BackendError("RFB message budget exhausted without a usable observation")

    def _point(self, x: int, y: int) -> tuple[int, int]:
        if not self.observed_size:
            raise BackendError("Observe before pointer input")
        return min(self.width - 1, x * self.width // self.observed_size[0]), min(self.height - 1, y * self.height // self.observed_size[1])

    def _pointer(self, x: int, y: int, mask: int, deadline: float) -> None:
        self.pointer = (x, y)
        self._send(struct.pack(">BBHH", 5, mask, x, y), deadline)

    def _key(self, symbol: int, down: bool, deadline: float) -> None:
        self._send(struct.pack(">BBHI", 4, int(down), 0, symbol), deadline)

    def _stroke(self, symbol: int, deadline: float, modifiers: tuple[int, ...] = ()) -> None:
        pressed = []
        try:
            for key in modifiers + (symbol,):
                # sendall may fail after the server received the key-down.
                pressed.append(key)
                self._key(key, True, deadline)
        finally:
            failure = None
            for key in reversed(pressed):
                try:
                    self._key(key, False, time.monotonic() + 0.5)
                except Exception as exc:
                    # Try to release all modifiers even if one release failed.
                    failure = failure or exc
            if failure:
                raise failure

    def execute(self, action: dict, deadline: float) -> None:
        try:
            self._execute(action, deadline)
        except BaseException:
            # A partially written RFB message or uncertain input invalidates the
            # transport. Do not keep capturing on a possibly desynchronized link.
            self.close()
            raise

    def _execute(self, action: dict, deadline: float) -> None:
        remaining(deadline)
        kind = action["type"]
        if kind in {"tap", "swipe"}:
            start = self._point(action["x"], action["y"])
            try:
                self._pointer(*start, 1, deadline)
                if kind == "swipe":
                    end = self._point(action["x2"], action["y2"])
                    duration = action["duration_ms"] / 1000
                    if remaining(deadline) < duration:
                        raise TimeoutError("Insufficient lease for drag")
                    steps = max(2, min(60, round(duration * 30)))
                    for step in range(1, steps + 1):
                        time.sleep(duration / steps)
                        x, y = (round(a + (b - a) * step / steps) for a, b in zip(start, end))
                        self._pointer(x, y, 1, deadline)
            finally:
                self._pointer(*self.pointer, 0, time.monotonic() + 0.5)
        elif kind == "key":
            key = action["key"]
            modifier = 0xFFE7 if self.profile["platform"] == "macos" else 0xFFE9
            if key == "APP_SWITCH":
                self._stroke(0xFF09, deadline, (modifier,))
            elif key == "BACK":
                self._stroke(ord("[") if self.profile["platform"] == "macos" else 0xFF51, deadline, (modifier,))
            else:
                symbol = {"HOME": 0xFF50, "ENTER": 0xFF0D, "TAB": 0xFF09, "ESCAPE": 0xFF1B,
                          "DPAD_UP": 0xFF52, "DPAD_DOWN": 0xFF54, "DPAD_LEFT": 0xFF51, "DPAD_RIGHT": 0xFF53, "DEL": 0xFF08}[key]
                self._stroke(symbol, deadline)
        elif kind == "text":
            shifted = "ABCDEFGHIJKLMNOPQRSTUVWXYZ~!@#$^&*()_+{}|:\"<>?"
            for char in action["text"]:
                self._stroke(ord(char), deadline, (0xFFE1,) if char in shifted else ())
        elif kind == "wait":
            duration = action["duration_ms"] / 1000
            if remaining(deadline) < duration:
                raise TimeoutError("Insufficient lease for wait")
            time.sleep(duration)
        elif kind == "screenshot":
            self.observe()
        else:
            raise BackendError("RFB action is not supported")
