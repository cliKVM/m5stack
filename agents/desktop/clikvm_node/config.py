from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


def validate_endpoint(url: str, allow_http: bool = False) -> str:
    p = urlsplit(url)
    if not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("Endpoint must be an absolute URL without credentials/query/fragment")
    if p.scheme == "https":
        return url.rstrip("/")
    if p.scheme == "http" and allow_http:
        try:
            ip = ipaddress.ip_address(p.hostname)
        except ValueError:
            if p.hostname == "localhost":
                return url.rstrip("/")
        else:
            if ip.is_loopback or ip.is_private:
                return url.rstrip("/")
    raise ValueError("HTTPS is required; explicit HTTP test mode allows private IP/localhost only")


def env_secret(name: str, *, optional: bool = False) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name):
        raise ValueError("Invalid secret environment variable name")
    value = os.environ.get(name, "")
    if not value and not optional:
        raise ValueError("Required secret environment variable is empty: " + name)
    return value


@dataclass(frozen=True)
class NodeConfig:
    gateway_url: str
    device_id: str
    token_env: str
    state_dir: Path
    backend: str
    options: dict
    poll_seconds: float = 1.0
    max_frame_bytes: int = 4 * 1024 * 1024
    max_width: int = 1920

    @classmethod
    def load(cls, path: Path) -> "NodeConfig":
        if path.stat().st_size > 65536:
            raise ValueError("Node config is too large")
        raw = json.loads(path.read_text())
        allowed = {"gateway_url", "device_id", "token_env", "state_dir", "backend", "options", "poll_seconds", "max_frame_bytes", "max_width", "allow_http_gateway"}
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("Unknown node configuration field")
        device = raw["device_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device):
            raise ValueError("Invalid device ID")
        backend = raw["backend"]
        if backend not in {"native", "rfb", "http-api"}:
            raise ValueError("Backend must be native, rfb or http-api")
        options = raw.get("options", {})
        if not isinstance(options, dict):
            raise ValueError("Backend options must be an object")
        config = cls(validate_endpoint(raw["gateway_url"], raw.get("allow_http_gateway") is True), device,
                     raw.get("token_env", "CLIK_NODE_TOKEN"),
                     (path.parent / raw.get("state_dir", ".state/" + device)).resolve(),
                     backend, options, float(raw.get("poll_seconds", 1)),
                     int(raw.get("max_frame_bytes", 4194304)), int(raw.get("max_width", 1920)))
        if not 0.1 <= config.poll_seconds <= 30 or not 65536 <= config.max_frame_bytes <= 16777216 or not 320 <= config.max_width <= 8192:
            raise ValueError("Invalid polling/image limits")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", config.token_env):
            raise ValueError("Invalid token environment variable")
        return config
