from __future__ import annotations

import copy
import json
import re
import time
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator

from .common import ActionRejected, Backend, BackendError, Observation, remaining
from .config import env_secret, validate_endpoint


def _fixed_path(value: str) -> str:
    p = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or p.scheme or p.netloc or p.fragment or any(ord(c) < 32 for c in value):
        raise ValueError("API paths must be fixed origin-relative paths")
    return value


def _check_schema(schema: dict) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("Each API operation requires a closed object JSON Schema")
    if len(json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 8192:
        raise ValueError("Operation schema exceeds the gateway's 8192-byte bound")
    def check(value, depth=0):
        if depth > 16:
            raise ValueError("Operation schema nesting exceeds 16")
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$dynamicRef", "$recursiveRef")):
                raise ValueError("External/referenced schemas are not accepted in local operation profiles")
            for item in value.values():
                check(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                check(item, depth + 1)
    check(schema)
    Draft202012Validator.check_schema(schema)


def _render_template(value, arguments: dict):
    if isinstance(value, dict):
        if set(value) == {"$arg"}:
            name = value["$arg"]
            if not isinstance(name, str) or name not in arguments:
                raise ActionRejected("Required configured body argument is absent")
            return copy.deepcopy(arguments[name])
        if value == {"$arguments": True}:
            return copy.deepcopy(arguments)
        return {key: _render_template(item, arguments) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template(item, arguments) for item in value]
    return value


class HttpAPIBackend(Backend):
    """Only configured HTTP origin, paths and methods. Arguments affect JSON body only."""

    def __init__(self, options: dict, max_width: int = 1920, max_bytes: int = 4194304, *, client=None):
        allowed = {"base_url", "allow_http", "state_path", "headers_env", "operations", "max_state_bytes", "ca_file"}
        if set(options) - allowed:
            raise ValueError("Unknown HTTP API option")
        self.base_url = validate_endpoint(options["base_url"], options.get("allow_http") is True)
        self.state_path = _fixed_path(options["state_path"])
        self.header_names = options.get("headers_env", {})
        if not isinstance(self.header_names, dict):
            raise ValueError("headers_env must map header names to environment variable names")
        for name, env in self.header_names.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", name) or name.lower() in {"host", "connection", "content-length", "transfer-encoding"}:
                raise ValueError("Invalid API header name")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", env):
                raise ValueError("API header secrets must be environment variable references")
        self.operations = options["operations"]
        if not isinstance(self.operations, dict) or not 1 <= len(self.operations) <= 12:
            raise ValueError("Configure 1..12 API operations")
        advertised = []
        for op_id, op in self.operations.items():
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", op_id):
                raise ValueError("Invalid operation ID")
            if set(op) - {"method", "path", "description", "parameters_schema", "body", "requires_confirmation"}:
                raise ValueError("Unknown operation option")
            if op.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
                raise ValueError("API actions require a configured mutating HTTP method")
            _fixed_path(op["path"])
            _check_schema(op["parameters_schema"])
            if not isinstance(op.get("description"), str) or not 1 <= len(op["description"]) <= 500:
                raise ValueError("Operation needs a bounded description")
            if type(op.get("requires_confirmation", True)) is not bool:
                raise ValueError("requires_confirmation must be boolean")
            advertised.append({"id": op_id, "description": op["description"], "parameters_schema": op["parameters_schema"],
                               "requires_confirmation": op.get("requires_confirmation", True)})
        self.profile = {"platform": "network", "transport": "http-api", "observation_kind": "json-state",
                        "action_types": ["api", "wait"], "keys": [], "api_operations": advertised}
        self.capabilities = ["api_state", "api_operation"]
        self.max_state_bytes = int(options.get("max_state_bytes", 60000))
        if not 1024 <= self.max_state_bytes <= 60000:
            raise ValueError("State byte limit must be 1024..60000 bytes")
        self.client = client
        self._injected = client is not None
        self.ca_file = options.get("ca_file", True)
        if self.ca_file is False:
            raise ValueError("TLS verification cannot be disabled")

    def open(self) -> None:
        headers = {name: env_secret(env) for name, env in self.header_names.items()}
        if any("\r" in value or "\n" in value for value in headers.values()):
            raise ValueError("Invalid header secret")
        self.headers = headers
        if not self.client:
            # No retries: a timed-out API mutation may already have succeeded.
            self.client = httpx.Client(timeout=10, follow_redirects=False, trust_env=False, verify=self.ca_file,
                                       transport=None)

    def close(self) -> None:
        if self.client and not self._injected:
            self.client.close()
            self.client = None

    def _request(self, method: str, path: str, *, body=None, timeout: float = 10):
        if not self.client:
            raise BackendError("API backend is not open")
        with self.client.stream(method, self.base_url + path, json=body, headers=self.headers, timeout=timeout) as response:
            if not 200 <= response.status_code < 300:
                # Redirects are never followed; response bodies may contain secrets.
                raise BackendError("Configured API returned HTTP " + str(response.status_code))
            chunks = bytearray()
            for block in response.iter_bytes():
                if len(chunks) + len(block) > self.max_state_bytes:
                    raise BackendError("Configured API response exceeds byte limit")
                chunks.extend(block)
            return bytes(chunks)

    def observe(self) -> Observation:
        raw = self._request("GET", self.state_path)
        try:
            state = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")))
        except (ValueError, UnicodeDecodeError) as e:
            raise BackendError("Configured state endpoint did not return valid JSON") from e
        if not isinstance(state, dict):
            raise BackendError("Configured state endpoint must return a JSON object")
        def check_depth(value, depth=0):
            if depth > 16:
                raise BackendError("Configured JSON state nesting exceeds 16")
            if isinstance(value, dict):
                for item in value.values():
                    check_depth(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    check_depth(item, depth + 1)
        check_depth(state)
        if len(json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 60000:
            raise BackendError("Configured JSON state exceeds the gateway limit")
        return Observation("json-state", state=state)

    def validate(self, action: dict, observation: Observation) -> None:
        super().validate(action, observation)
        if action["type"] == "api":
            operation = self.operations.get(action["operation"])
            if not operation:
                raise ActionRejected("API operation is not configured")
            try:
                Draft202012Validator(operation["parameters_schema"]).validate(action["arguments"])
            except Exception as e:
                raise ActionRejected("API arguments do not match configured schema") from e
            _render_template(operation.get("body", {"$arguments": True}), action["arguments"])

    def execute(self, action: dict, deadline: float) -> None:
        if action["type"] == "wait":
            duration = action["duration_ms"] / 1000
            if remaining(deadline) < duration:
                raise TimeoutError("Insufficient lease for wait")
            time.sleep(duration)
            return
        op = self.operations[action["operation"]]
        body = _render_template(op.get("body", {"$arguments": True}), action["arguments"])
        self._request(op["method"], op["path"], body=body, timeout=remaining(deadline))
