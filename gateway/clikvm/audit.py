"""Adopter-owned projection to the pinned wellmanifest/logs 0.5.0 envelope.

Persistence belongs to the gateway event store. Build the audit event in the
same transaction as its domain event; export JSONL only after commit. This
module does not claim execution authority or send events to external services.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any

ZERO_HASH = "0" * 64
MAX_SEQUENCE = 9007199254740991
HASH = re.compile(r"[a-f0-9]{64}\Z")
FIELDS = frozenset({
    "schema", "eventId", "stream", "sequence", "eventType", "severity",
    "mode", "occurredAt", "correlationId", "causationId", "producer", "source",
    "code", "subjectRef", "outcome", "subjectState", "evidence", "inputHash",
    "receiptRef", "previousHash", "eventHash", "rawOutputIncluded",
    "secretMaterialIncluded",
})


def canonical(value: Any) -> str:
    """wellmanifest-canonical-json-v1 subset used by these event envelopes.

    Floats, malformed Unicode, non-string keys, cycles and excessive nesting
    fail explicitly. Timing and scores in audited inputs use integer units.
    """
    def visit(item: Any, depth: int) -> None:
        if depth > 32:
            raise ValueError("canonical JSON nesting exceeds 32 levels")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, int):
            return
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("canonical JSON keys must be strings")
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise ValueError("canonical JSON supports no floats or custom objects")
    visit(value, 0)
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def build_audit_event(
    sequence: int,
    previous_hash: str,
    *,
    event_type: str,
    subject_ref: str,
    correlation_id: str,
    payload: dict,
    occurred_at: str,
    outcome: str = "OBSERVED",
    severity: str = "INFO",
    event_id: str | None = None,
    causation_id: str | None = None,
    mode: str = "APPLY",
) -> dict:
    """Create metadata-only audit JSON. Raw payload bytes are never included."""
    event = {
        "schema": "wellmanifest.logs/event/v1",
        "eventId": event_id or f"event:{uuid.uuid4()}",
        "stream": "clikvm",
        "sequence": sequence,
        "eventType": event_type,
        "severity": severity,
        "mode": mode,
        "occurredAt": occurred_at,
        "correlationId": correlation_id,
        "causationId": causation_id,
        "producer": "service:clikvm-gateway",
        "source": "clikvm.gateway",
        "code": None,
        "subjectRef": subject_ref,
        "outcome": outcome,
        "subjectState": None,
        "evidence": [],
        "inputHash": digest(payload),
        "receiptRef": None,
        "previousHash": previous_hash,
        "rawOutputIncluded": False,
        "secretMaterialIncluded": False,
    }
    event["eventHash"] = digest(event)
    validate_event(event)
    return event


def validate_event(event: dict) -> None:
    """Validate this producer's closed envelope and hash (not the whole stream)."""
    if not isinstance(event, dict) or set(event) != FIELDS:
        raise ValueError("audit event fields differ from producer contract")
    if event["schema"] != "wellmanifest.logs/event/v1" or event["stream"] != "clikvm":
        raise ValueError("unsupported audit schema or stream")
    seq = event["sequence"]
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= MAX_SEQUENCE:
        raise ValueError("invalid audit sequence")
    patterns = {
        "eventId": (r"event:[A-Za-z0-9._:-]+", 8, 128),
        "eventType": (r"clikvm(?:\.[a-z][a-z0-9_]*)+", 3, 64),
        "correlationId": (r"[A-Za-z0-9._:-]+", 1, 128),
        "subjectRef": (r"[a-z][a-z0-9+.-]*:[A-Za-z0-9._:/-]+", 3, 160),
    }
    for key, (pattern, minimum, maximum) in patterns.items():
        value = event[key]
        if not isinstance(value, str) or not minimum <= len(value) <= maximum or not re.fullmatch(pattern, value):
            raise ValueError(f"invalid audit {key}")
    cause = event["causationId"]
    if cause is not None and (not isinstance(cause, str) or len(cause) > 128 or
                             not re.fullmatch(r"[A-Za-z0-9._:-]+", cause) or cause == event["eventId"]):
        raise ValueError("invalid causation identifier")
    if event["severity"] not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("invalid severity")
    if event["outcome"] not in {"OBSERVED", "ACCEPTED", "REJECTED", "SUCCEEDED", "FAILED"}:
        raise ValueError("invalid outcome")
    if event["mode"] not in {"PLAN", "APPLY"}:
        raise ValueError("invalid mode")
    timestamp = event["occurredAt"]
    if not isinstance(timestamp, str) or len(timestamp) > 40:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("audit timestamp requires timezone")
    if event["producer"] != "service:clikvm-gateway" or event["source"] != "clikvm.gateway":
        raise ValueError("invalid producer")
    if any(event[key] is not None for key in ("code", "subjectState", "receiptRef")) or event["evidence"] != []:
        raise ValueError("unsupported producer audit extension")
    if event["rawOutputIncluded"] is not False or event["secretMaterialIncluded"] is not False:
        raise ValueError("audit must contain no raw output or secret material")
    for key in ("inputHash", "previousHash", "eventHash"):
        if not isinstance(event[key], str) or not HASH.fullmatch(event[key]):
            raise ValueError(f"invalid {key}")
    expected = digest({key: value for key, value in event.items() if key != "eventHash"})
    if event["eventHash"] != expected:
        raise ValueError("audit event hash mismatch")
