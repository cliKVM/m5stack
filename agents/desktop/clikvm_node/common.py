from __future__ import annotations

from dataclasses import dataclass
import io
import json
import time
from typing import Any
from urllib.parse import urlsplit

from PIL import Image


KEYS = ["HOME", "BACK", "ENTER", "TAB", "ESCAPE", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DEL", "APP_SWITCH"]


class BackendError(RuntimeError):
    """A diagnostic without credentials, command contents or upstream bodies."""


class ActionRejected(BackendError):
    """Validation failed before the backend sent any physical/API input."""


@dataclass
class Observation:
    kind: str
    png: bytes | None = None
    state: dict | None = None
    width: int = 0
    height: int = 0


def remaining(deadline: float, limit: float = 10.0) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("Execution deadline elapsed")
    return min(value, limit)


def png_observation(image: Image.Image, max_width: int, max_bytes: int) -> Observation:
    if image.width < 1 or image.height < 1 or image.width > 16384 or image.height > 16384:
        raise BackendError("Unsupported screen dimensions")
    image = image.convert("RGB")
    if image.width > max_width:
        image = image.resize((max_width, max(1, round(image.height * max_width / image.width))), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=6)
    data = out.getvalue()
    if len(data) > max_bytes:
        raise BackendError("Screenshot exceeds configured upload limit; lower max_width")
    return Observation("screenshot", png=data, width=image.width, height=image.height)


def validate_url(url: Any) -> str:
    if not isinstance(url, str) or not 8 <= len(url) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ActionRejected("URL must be ordinary ASCII/percent-encoded HTTP(S)")
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ActionRejected("URL must be ordinary HTTP(S) without embedded credentials")
    return url


def validate_action(action: Any, profile: dict, observation: Observation) -> None:
    if not isinstance(action, dict) or action.get("type") not in profile["action_types"]:
        raise ActionRejected("Action is not advertised by this backend")
    kind = action["type"]
    fields = {
        "tap": {"type", "x", "y"}, "swipe": {"type", "x", "y", "x2", "y2", "duration_ms"},
        "key": {"type", "key"}, "text": {"type", "text"}, "open_url": {"type", "url"},
        "wait": {"type", "duration_ms"}, "screenshot": {"type"}, "api": {"type", "operation", "arguments"},
    }[kind]
    if set(action) != fields:
        raise ActionRejected("Unexpected or missing action fields")
    if kind in {"tap", "swipe"}:
        for x_name, y_name in [("x", "y")] + ([("x2", "y2")] if kind == "swipe" else []):
            x, y = action[x_name], action[y_name]
            if type(x) is not int or type(y) is not int or not (0 <= x < observation.width and 0 <= y < observation.height):
                raise ActionRejected("Coordinates are outside the observed image")
    if kind in {"swipe", "wait"}:
        duration = action["duration_ms"]
        maximum = 3000 if kind == "swipe" else 5000
        if type(duration) is not int or not 50 <= duration <= maximum:
            raise ActionRejected("Invalid action duration")
    if kind == "key" and action["key"] not in profile["keys"]:
        raise ActionRejected("Key not advertised")
    if kind == "text":
        text = action["text"]
        if not isinstance(text, str) or not 1 <= len(text) <= 512 or "%" in text or any(not 32 <= ord(c) <= 126 for c in text):
            raise ActionRejected("Current device protocol supports printable ASCII without percent only")
    if kind == "open_url":
        validate_url(action["url"])
    if kind == "api":
        if not isinstance(action["operation"], str) or not isinstance(action["arguments"], dict):
            raise ActionRejected("Malformed API operation")
        if len(json.dumps(action, ensure_ascii=False).encode()) > 32768:
            raise ActionRejected("API arguments exceed limit")


class Backend:
    profile: dict
    capabilities: list[str]

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def observe(self) -> Observation:
        raise NotImplementedError

    def execute(self, action: dict, deadline: float) -> None:
        raise NotImplementedError

    def validate(self, action: dict, observation: Observation) -> None:
        validate_action(action, self.profile, observation)

    def diagnostic(self) -> dict:
        observation = self.observe()
        return {"status": "observation_available", "profile": self.profile, "capabilities": self.capabilities,
                "observation_kind": observation.kind, "width": observation.width, "height": observation.height,
                "input_probe": "NOT_EXECUTED", "hardware_validated": False}
