from __future__ import annotations

import ctypes
import os
import platform
import subprocess
import time

from PIL import Image

from .common import Backend, BackendError, KEYS, Observation, png_observation, remaining, validate_url


class NativeBackend(Backend):
    """Primary-display session capture/input. No login/secure-desktop bypass."""

    def __init__(self, options: dict, max_width: int, max_bytes: int):
        if options:
            raise ValueError("Native backend has no additional options in 0.2")
        self.platform = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "unknown")
        self.profile = {"platform": self.platform, "transport": "native-agent", "observation_kind": "screenshot",
                        "action_types": ["tap", "swipe", "key", "text", "open_url", "wait", "screenshot"],
                        "keys": KEYS, "api_operations": []}
        self.capabilities = ["screenshot", "keyboard", "mouse", "open_url", "native_agent"]
        self.max_width, self.max_bytes = max_width, max_bytes
        self.sct = None
        self.gui = None
        self.logical_size = None
        self.observation_size = None
        self.monitor = None

    def open(self) -> None:
        if self.platform == "unknown":
            raise BackendError("Native executor supports Windows, macOS and Linux X11")
        if self.platform == "linux":
            if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
                raise BackendError("Wayland needs an explicitly approved portal/remote-desktop service; use its supported RFB server")
            if not os.environ.get("DISPLAY"):
                raise BackendError("No interactive X11 DISPLAY; start the node in the user's desktop session")
        if self.platform == "windows":
            # Set DPI awareness before importing libraries which initialize GUI state.
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except (AttributeError, OSError):
                    pass
        if self.platform == "macos":
            framework = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            framework.AXIsProcessTrusted.restype = ctypes.c_bool
            if not framework.AXIsProcessTrusted():
                raise BackendError("Grant Accessibility permission to the node terminal/application in macOS System Settings")
            graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            if hasattr(graphics, "CGPreflightScreenCaptureAccess"):
                graphics.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
                if not graphics.CGPreflightScreenCaptureAccess():
                    raise BackendError("Grant Screen Recording permission to the node terminal/application, then restart it")
        try:
            import mss
            import pyautogui
        except ImportError as e:
            raise BackendError("Install clikvm-node[native] and the operating-system dependencies") from e
        except Exception as e:
            raise BackendError("Unable to initialize desktop access; check active user session and permissions") from e
        self.gui = pyautogui
        self.gui.FAILSAFE = True
        self.gui.PAUSE = 0.05
        try:
            self.sct = mss.mss()
            self.monitor = next((dict(m) for m in self.sct.monitors[1:] if m["left"] == 0 and m["top"] == 0), None)
            if not self.monitor:
                raise BackendError("No monitor at the desktop origin; primary-display mapping cannot be verified")
            if self.monitor["width"] * self.monitor["height"] > 16777216:
                raise BackendError("Primary display exceeds the 16-megapixel capture limit")
            self.logical_size = tuple(self.gui.size())
            if min(self.logical_size) <= 0:
                raise BackendError("Invalid logical primary-display dimensions")
            self._check_geometry()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.sct:
            self.sct.close()
        self.sct = None

    def observe(self) -> Observation:
        if not self.sct or not self.gui:
            raise BackendError("Native desktop is not open")
        self._check_geometry()
        shot = self.sct.grab(self.monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        observed = png_observation(image, self.max_width, self.max_bytes)
        self.observation_size = (observed.width, observed.height)
        return observed

    def _check_geometry(self) -> None:
        if not self.gui or not self.monitor or not self.logical_size:
            raise BackendError("Display geometry has not been probed")
        if tuple(self.gui.size()) != self.logical_size:
            raise BackendError("Display geometry changed; reconnect the node before using cached coordinates")
        # X11 PyAutoGUI reports the root, potentially spanning several monitors.
        # Scaling one captured monitor to that size would click another screen.
        if self.platform == "linux" and self.logical_size != (self.monitor["width"], self.monitor["height"]):
            raise BackendError("X11 root size differs from captured primary display; multi-monitor mapping is unsupported")

    def _point(self, x: int, y: int) -> tuple[int, int]:
        if not self.observation_size or not self.logical_size:
            raise BackendError("Observe before sending pointer input")
        self._check_geometry()
        return tuple(min(logical - 1, int(value * logical / observed)) for value, logical, observed
                     in zip((x, y), self.logical_size, self.observation_size))

    def execute(self, action: dict, deadline: float) -> None:
        remaining(deadline)
        self._check_geometry()
        kind = action["type"]
        if kind == "tap":
            self.gui.click(*self._point(action["x"], action["y"]))
        elif kind == "swipe":
            duration = action["duration_ms"] / 1000
            if remaining(deadline) < duration + 0.2:
                raise TimeoutError("Insufficient lease for drag")
            self.gui.moveTo(*self._point(action["x"], action["y"]))
            try:
                self.gui.dragTo(*self._point(action["x2"], action["y2"]), duration=duration, button="left")
            finally:
                self.gui.mouseUp(button="left")
        elif kind == "key":
            key = action["key"]
            if key == "APP_SWITCH":
                self.gui.hotkey("command" if self.platform == "macos" else "alt", "tab")
            elif key == "BACK":
                self.gui.hotkey(*(("command", "[") if self.platform == "macos" else ("alt", "left")))
            else:
                self.gui.press({"HOME": "home", "ENTER": "enter", "TAB": "tab", "ESCAPE": "esc",
                                "DPAD_UP": "up", "DPAD_DOWN": "down", "DPAD_LEFT": "left", "DPAD_RIGHT": "right", "DEL": "backspace"}[key])
        elif kind == "text":
            for character in action["text"]:
                remaining(deadline)
                self.gui.write(character)
        elif kind == "open_url":
            url = validate_url(action["url"])
            if self.platform == "windows":
                os.startfile(url)
            else:
                command = ["/usr/bin/open", "--", url] if self.platform == "macos" else ["xdg-open", url]
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        elif kind == "wait":
            duration = action["duration_ms"] / 1000
            if remaining(deadline) < duration:
                raise TimeoutError("Insufficient lease for wait")
            time.sleep(duration)
        elif kind == "screenshot":
            self.observe()
        else:
            raise BackendError("Unsupported native action")
