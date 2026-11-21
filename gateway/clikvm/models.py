from __future__ import annotations

from typing import Annotated, Literal, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from .capabilities import TargetProfile, bounded_json


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SessionStart(Strict):
    boot_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    capabilities: list[Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]] = Field(max_length=32)
    profile: TargetProfile | None = None
    simulated: bool = False


class IntentRequest(Strict):
    text: str = Field(min_length=1, max_length=4000)
    frame_id: int = Field(ge=1, le=2**31 - 1)


class Tap(Strict):
    type: Literal["tap"]
    x: int = Field(ge=0, le=16383)
    y: int = Field(ge=0, le=16383)


class Swipe(Strict):
    type: Literal["swipe"]
    x: int = Field(ge=0, le=16383)
    y: int = Field(ge=0, le=16383)
    x2: int = Field(ge=0, le=16383)
    y2: int = Field(ge=0, le=16383)
    duration_ms: int = Field(default=300, ge=50, le=3000)


class Key(Strict):
    type: Literal["key"]
    key: Literal["HOME", "BACK", "ENTER", "TAB", "ESCAPE", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DEL", "APP_SWITCH"]


class Text(Strict):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=512)

    @field_validator("text")
    @classmethod
    def supported_text(cls, value: str) -> str:
        # Android's shell `input text` is not a Unicode IME. Never report silently
        # mangled Polish characters as success. An IME/clipboard adapter can extend it.
        if not all(32 <= ord(c) <= 126 for c in value) or "%" in value:
            raise ValueError("This firmware input-text profile supports printable ASCII without '%' only")
        return value


class OpenUrl(Strict):
    type: Literal["open_url"]
    url: str = Field(min_length=8, max_length=1024)

    @field_validator("url")
    @classmethod
    def web_only(cls, value: str) -> str:
        p = urlsplit(value)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password or any(ord(c) < 33 for c in value):
            raise ValueError("Only ordinary HTTP(S) URLs are supported")
        if any(ord(c) > 126 for c in value):
            raise ValueError("URL must be ASCII/percent encoded")
        return value


class Screenshot(Strict):
    type: Literal["screenshot"]


class Wait(Strict):
    type: Literal["wait"]
    duration_ms: int = Field(default=500, ge=50, le=5000)


class ApiAction(Strict):
    type: Literal["api"]
    operation: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value):
        return bounded_json(value, 8192)


class JsonObservation(Strict):
    frame_id: int = Field(ge=1, le=2**31-1)
    state: dict[str, JsonValue]

    @field_validator("state")
    @classmethod
    def bounded_state(cls, value):
        return bounded_json(value, 60000)


class ControllerGuard(Strict):
    expected_session_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


class ControllerIntent(ControllerGuard):
    text: str = Field(min_length=1, max_length=4000)


Action = Annotated[Union[Tap, Swipe, Key, Text, OpenUrl, Screenshot, Wait, ApiAction], Field(discriminator="type")]


class Plan(Strict):
    status: Literal["action", "done", "blocked"]
    action: Action | None = None
    reason: str = Field(default="", max_length=1000)
    requires_confirmation: bool = False


class ActionResult(Strict):
    lease_id: str = Field(min_length=1, max_length=64)
    status: Literal["succeeded", "failed", "uncertain"]
    detail: str = Field(default="", max_length=1000)


class Confirmation(Strict):
    approve: bool


class Resolution(Strict):
    resolution: Literal["executed", "not_executed", "abandon"]
    frame_id: int = Field(ge=1)
    note: str = Field(default="", max_length=1000)


class ContinueRequest(Strict):
    frame_id: int = Field(ge=1)


class SpeakRequest(Strict):
    text: str = Field(min_length=1, max_length=2000)


class RouteRequest(Strict):
    query: str = Field(min_length=1, max_length=4000)


class SessionCreated(Strict):
    session_id: str
    latest_frame: int
    expires_in_seconds: int
    simulated: bool


class FrameAccepted(Strict):
    frame_id: int
    width: int
    height: int
    resource_urn: str
    planning_intent: str | None


class IntentCreated(Strict):
    intent_id: str
    status: Literal["planning"]


class ActionLease(Strict):
    action_id: str
    lease_id: str
    based_on_frame: int
    valid_for_ms: int = Field(ge=0, le=120000)
    command: Action
    operation_uri: str
    after_action: Literal["screenshot", "observation"]


class NextActionResponse(Strict):
    session_id: str
    state: str
    latest_frame: int
    capture_requested: bool
    action: ActionLease | None


class ResultAccepted(Strict):
    action_id: str
    status: Literal["succeeded", "failed", "uncertain"]
    next: Literal["upload_frame", "review"]
