"""Declared, scoped device contracts; no operating system is inferred from a logo.

A connector probes its actual environment before registering. The gateway validates
and enforces its declared contract but does not certify physical hardware support.
"""
from __future__ import annotations

import json
from typing import Literal
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from fastapi import HTTPException

ACTION_TYPES = ("tap", "swipe", "key", "text", "open_url", "screenshot", "wait", "api")
KEYS = ("HOME", "BACK", "ENTER", "TAB", "ESCAPE", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DEL", "APP_SWITCH")
ActionKind = Literal["tap", "swipe", "key", "text", "open_url", "screenshot", "wait", "api"]
KeyKind = Literal["HOME", "BACK", "ENTER", "TAB", "ESCAPE", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DEL", "APP_SWITCH"]


def bounded_json(value, max_bytes=65536):
    def visit(item, depth=0):
        if depth > 16:
            raise ValueError("JSON nesting exceeds 16 levels")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON keys must be strings")
                visit(child, depth+1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth+1)
    visit(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > max_bytes:
        raise ValueError("JSON exceeds bounded size")
    return value


class ApiOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    description: str = Field(min_length=1, max_length=500)
    parameters_schema: dict
    requires_confirmation: bool = True

    @field_validator("parameters_schema")
    @classmethod
    def local_closed_schema(cls, value):
        bounded_json(value, 8192)
        if value.get("type") != "object" or value.get("additionalProperties") is not False:
            raise ValueError("API arguments need a closed object JSON Schema")
        def refs(item):
            if isinstance(item, dict):
                if any(key in item for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                    raise ValueError("References are not supported in API schemas; inline the bounded schema")
                for child in item.values(): refs(child)
            elif isinstance(item, list):
                for child in item: refs(child)
        refs(value)
        try:
            Draft202012Validator.check_schema(value)
        except Exception as exc:
            raise ValueError("Invalid operation JSON Schema") from exc
        return value


class TargetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    platform: Literal["android", "windows", "macos", "linux", "ios", "ipados", "network", "unknown"] = "android"
    os_version: str = Field(default="", max_length=128)
    label: str = Field(default="", max_length=80)
    transport: Literal["android-adb", "native-agent", "rfb", "http-api", "hid-bridge"] = "android-adb"
    observation_kind: Literal["screenshot", "json-state", "none"] = "screenshot"
    action_types: list[ActionKind] = Field(default_factory=lambda: list(ACTION_TYPES[:-1]), max_length=8)
    keys: list[KeyKind] = Field(default_factory=lambda: list(KEYS), max_length=11)
    api_operations: list[ApiOperation] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def contract(self):
        if len(set(self.action_types)) != len(self.action_types) or len(set(self.keys)) != len(self.keys):
            raise ValueError("Capabilities must be unique")
        if len({op.id for op in self.api_operations}) != len(self.api_operations):
            raise ValueError("API operation IDs must be unique")
        if ("api" in self.action_types) != bool(self.api_operations):
            raise ValueError("API action capability requires an explicit nonempty operation catalog")
        if self.observation_kind == "json-state" and any(a not in {"api", "wait"} for a in self.action_types):
            raise ValueError("JSON state cannot authorize pixel or keyboard commands")
        if self.transport == "http-api" and self.observation_kind != "json-state":
            raise ValueError("HTTP API transport reports JSON state, never a synthetic screenshot")
        if self.transport == "android-adb" and (self.platform != "android" or self.observation_kind != "screenshot"):
            raise ValueError("ADB profile requires Android screenshot observation")
        if "key" not in self.action_types and self.keys:
            # Irrelevant keys are normalized instead of advertised as available.
            self.keys = []
        return self


def profile_for(row) -> TargetProfile:
    raw = json.loads(row["profile"] or "{}")
    return TargetProfile.model_validate(raw)


def validate_command(profile: TargetProfile, command: dict) -> bool:
    """Return mandatory operation confirmation after contract validation."""
    kind = command["type"]
    if kind not in profile.action_types:
        raise HTTPException(502, "Planner requested a capability unavailable on this target")
    if profile.observation_kind == "none":
        raise HTTPException(409, "An autonomous action needs an observable target", headers={"X-Clik-Error-Code":"observation_required"})
    if kind == "key" and command["key"] not in profile.keys:
        raise HTTPException(502, "Planner requested an unavailable target key")
    if kind == "api":
        operation = next((op for op in profile.api_operations if op.id == command["operation"]), None)
        if operation is None:
            raise HTTPException(502, "Planner requested an unregistered API operation")
        try:
            Draft202012Validator(operation.parameters_schema).validate(command["arguments"])
        except Exception as exc:
            raise HTTPException(502, "Planner API arguments violate the target operation schema") from exc
        return operation.requires_confirmation
    return False
