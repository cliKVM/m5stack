from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(int(default))).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path(".state/gateway")
    # No built-in credentials. Provision distinct random tokens per physical unit.
    device_tokens: dict[str, str] = field(default_factory=dict)
    operator_token: str = ""
    controller_tokens: dict[str, str] = field(default_factory=dict)
    controller_targets: dict[str, str] = field(default_factory=dict)
    target_online_seconds: int = 30
    allow_simulation: bool = False
    require_action_confirmation: bool = False
    max_frame_bytes: int = 4 * 1024 * 1024
    max_audio_bytes: int = 4 * 1024 * 1024
    max_storage_bytes: int = 256 * 1024 * 1024
    frames_per_session: int = 8
    lease_seconds: int = 60
    action_ttl_seconds: int = 120
    session_ttl_seconds: int = 86400
    max_actions_per_intent: int = 12
    max_observation_age_seconds: int = 5
    model_endpoint: str = ""  # Full URL, never inferred on api.oqllm.com.
    model_name: str = ""
    model_api_key: str = ""
    model_timeout_seconds: float = 45.0
    poa_endpoint: str = "https://api.oqllm.com/v1/poa/"
    poa_api_key: str = ""
    stt_endpoint: str = ""
    stt_model: str = ""
    stt_api_key: str = ""
    tts_endpoint: str = ""
    tts_model: str = ""
    tts_voice: str = ""
    tts_api_key: str = ""
    allow_http_upstreams: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        raw = json.loads(os.environ.get("CLIK_DEVICE_TOKENS_JSON", "{}"))
        if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
            raise ValueError("CLIK_DEVICE_TOKENS_JSON must map device IDs to tokens")
        return cls(
            state_dir=Path(os.environ.get("CLIK_STATE_DIR", ".state/gateway")),
            device_tokens=raw,
            operator_token=os.environ.get("CLIK_OPERATOR_TOKEN", ""),
            controller_tokens=json.loads(os.environ.get("CLIK_CONTROLLER_TOKENS_JSON", "{}")),
            controller_targets=json.loads(os.environ.get("CLIK_CONTROLLER_TARGETS_JSON", "{}")),
            target_online_seconds=int(os.environ.get("CLIK_TARGET_ONLINE_SECONDS", 30)),
            allow_simulation=_flag("CLIK_ALLOW_SIMULATION"),
            require_action_confirmation=_flag("CLIK_REQUIRE_ACTION_CONFIRMATION"),
            max_frame_bytes=int(os.environ.get("CLIK_MAX_FRAME_BYTES", 4194304)),
            max_audio_bytes=int(os.environ.get("CLIK_MAX_AUDIO_BYTES", 4194304)),
            max_storage_bytes=int(os.environ.get("CLIK_MAX_STORAGE_BYTES", 268435456)),
            frames_per_session=int(os.environ.get("CLIK_FRAMES_PER_SESSION", 8)),
            lease_seconds=int(os.environ.get("CLIK_LEASE_SECONDS", 60)),
            action_ttl_seconds=int(os.environ.get("CLIK_ACTION_TTL_SECONDS", 120)),
            session_ttl_seconds=int(os.environ.get("CLIK_SESSION_TTL_SECONDS", 86400)),
            max_actions_per_intent=int(os.environ.get("CLIK_MAX_ACTIONS_PER_INTENT", 12)),
            max_observation_age_seconds=int(os.environ.get("CLIK_MAX_OBSERVATION_AGE_SECONDS", 5)),
            model_endpoint=os.environ.get("CLIK_MODEL_ENDPOINT", ""),
            model_name=os.environ.get("CLIK_MODEL_NAME", ""),
            model_api_key=os.environ.get("CLIK_MODEL_API_KEY", ""),
            model_timeout_seconds=float(os.environ.get("CLIK_MODEL_TIMEOUT_SECONDS", 45)),
            poa_endpoint=os.environ.get("CLIK_POA_ENDPOINT", "https://api.oqllm.com/v1/poa/"),
            poa_api_key=os.environ.get("CLIK_POA_API_KEY", ""),
            stt_endpoint=os.environ.get("CLIK_STT_ENDPOINT", ""),
            stt_model=os.environ.get("CLIK_STT_MODEL", ""),
            stt_api_key=os.environ.get("CLIK_STT_API_KEY", ""),
            tts_endpoint=os.environ.get("CLIK_TTS_ENDPOINT", ""),
            tts_model=os.environ.get("CLIK_TTS_MODEL", ""),
            tts_voice=os.environ.get("CLIK_TTS_VOICE", ""),
            tts_api_key=os.environ.get("CLIK_TTS_API_KEY", ""),
            allow_http_upstreams=_flag("CLIK_ALLOW_HTTP_UPSTREAMS"),
        )

    def validate(self) -> None:
        from urllib.parse import urlsplit
        import re
        for device_id, token in self.device_tokens.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", device_id):
                raise ValueError("Invalid configured device ID")
            if len(token) < 24:
                raise ValueError("Device tokens must have at least 24 characters")
        if self.operator_token and len(self.operator_token) < 24:
            raise ValueError("Operator token must have at least 24 characters")
        if len(set(self.device_tokens.values())) != len(self.device_tokens):
            raise ValueError("Every device must have a unique token")
        if self.operator_token and self.operator_token in self.device_tokens.values():
            raise ValueError("Operator token must differ from device tokens")
        if not isinstance(self.controller_tokens, dict) or not isinstance(self.controller_targets, dict):
            raise ValueError("Controller token and target configuration must be objects")
        if set(self.controller_tokens) != set(self.controller_targets):
            raise ValueError("Every controller credential requires exactly one configured target")
        all_tokens = list(self.device_tokens.values()) + ([self.operator_token] if self.operator_token else [])
        for controller_id, token in self.controller_tokens.items():
            if not isinstance(controller_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", controller_id):
                raise ValueError("Invalid controller ID")
            if not isinstance(token, str) or len(token) < 24:
                raise ValueError("Controller tokens must have at least 24 characters")
            target = self.controller_targets[controller_id]
            if not isinstance(target, str) or target not in self.device_tokens:
                raise ValueError("Controller target needs its own configured device credential")
            all_tokens.append(token)
        if len(set(all_tokens)) != len(all_tokens):
            raise ValueError("Operator, controller and device tokens must all differ")
        if not 5 <= self.target_online_seconds <= 300:
            raise ValueError("Target online timeout must be 5..300 seconds")
        for endpoint in [self.model_endpoint, self.poa_endpoint, self.stt_endpoint, self.tts_endpoint]:
            if not endpoint:
                continue
            parsed = urlsplit(endpoint)
            if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Upstream endpoint must be an absolute URL without credentials")
            if parsed.scheme != "https" and not (parsed.scheme == "http" and self.allow_http_upstreams):
                raise ValueError("Upstreams require HTTPS; opt in explicitly to local HTTP")
        if not (1 <= self.frames_per_session <= 1000 and 1 <= self.max_actions_per_intent <= 100):
            raise ValueError("Invalid retention or action budget")
        if not (65536 <= self.max_frame_bytes <= 16777216 and self.max_storage_bytes >= self.max_frame_bytes):
            raise ValueError("Invalid image/storage limits")
        if not 65536 <= self.max_audio_bytes <= 16777216:
            raise ValueError("Audio body limit must be between 64 KiB and 16 MiB")
        if min(self.lease_seconds, self.action_ttl_seconds, self.session_ttl_seconds) < 1:
            raise ValueError("Timeouts must be positive")
        if self.lease_seconds > 120:
            raise ValueError("Lease limit must not exceed the firmware's 120-second bound")
        if not 0 <= self.max_observation_age_seconds <= 3600:
            raise ValueError("Observation freshness limit must be 0..3600 seconds")
