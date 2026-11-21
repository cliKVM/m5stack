#!/usr/bin/env python3
"""Offline adopter checks. This is not a replacement for Wellman's fleet gate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))


def load(path: Path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key {key}")
            out[key] = value
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def write(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def operations(recorded_at: str):
    from clikvm.models import Tap, Swipe, Key, Text, OpenUrl, Screenshot, Wait, ApiAction
    source = "gateway/clikvm/models.py"
    source_hash = hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
    summaries = {
        "tap": "Tap one pixel coordinate on the current Android frame.",
        "swipe": "Swipe between bounded coordinates with a bounded duration.",
        "key": "Send one explicitly allowed Android key.",
        "text": "Enter printable ASCII through the supported Android input profile.",
        "open_url": "Open a validated HTTP(S) URL in Android.",
        "screenshot": "Capture a new Android screen through authenticated ADB.",
        "wait": "Wait for a bounded duration before another observation.",
    }
    universal_summaries = {
        "tap": "Click or tap a bounded coordinate in the target's current screenshot.",
        "swipe": "Drag between bounded screenshot coordinates with a bounded duration.",
        "key": "Send a declared key with the target platform's key semantics.",
        "text": "Enter printable ASCII, excluding percent, using the declared input adapter.",
        "open_url": "Open a validated HTTP(S) URL only when the target advertises this operation.",
        "screenshot": "Request an actual screenshot from a declared image-capable adapter.",
        "wait": "Wait for a bounded duration before a fresh observation.",
        "api": "Execute one configured API operation with locally validated JSON Schema arguments.",
    }
    result = []
    for namespace, classes, descriptions in (
        ("android", (Tap, Swipe, Key, Text, OpenUrl, Screenshot, Wait), summaries),
        ("device", (Tap, Swipe, Key, Text, OpenUrl, Screenshot, Wait, ApiAction), universal_summaries),
    ):
        for cls in classes:
            args_schema = cls.model_json_schema()
            # POA's closed top-level object subset excludes $defs. Resolve local
            # references without changing their validation meaning; recursive or
            # external references fail instead of silently weakening a schema.
            definitions=args_schema.pop("$defs",{})
            def inline(value, stack=()):
                if isinstance(value,list):
                    return [inline(item,stack) for item in value]
                if isinstance(value,dict):
                    if "$ref" in value:
                        reference=value["$ref"]
                        if not reference.startswith("#/$defs/") or reference in stack:
                            raise ValueError("POA export cannot inline recursive/external schema reference")
                        name=reference[len("#/$defs/"):]
                        if name not in definitions:
                            raise ValueError("POA export cannot resolve model definition")
                        target=inline(definitions[name],stack+(reference,))
                        siblings=inline({key:item for key,item in value.items() if key!="$ref"},stack)
                        return {"allOf":[target,siblings]} if siblings else target
                    return {key:inline(item,stack) for key,item in value.items()}
                return value
            args_schema=inline(args_schema)
            kind = args_schema["properties"]["type"]["const"]
            effects = [] if kind == "wait" else ["read_data", "remote_session"] if kind == "screenshot" else ["external_write", "remote_session"]
            result.append({
                "schema": "poa.operation/v1",
                "operation_id": f"{namespace}.{kind}",
                "uri": f"proc://clikvm/{namespace}/{kind}/v1",
                "version": 1,
                "summary": descriptions[kind],
                "input_schema": args_schema,
                "effects": effects,
                "risk": "low" if kind == "wait" else "medium",
                "lifecycle": "candidate",
                "provenance": {
                    "registry": f"clikvm.{namespace}",
                    "source_id": source,
                    "source_sha256": source_hash,
                    "origin": "declared",
                    "recorded_at": recorded_at,
                },
            })
    return result


def check(events_path: Path | None = None):
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        raise RuntimeError("Install governance requirements: pip install jsonschema==4.26.0") from error
    from clikvm.audit import validate_event, ZERO_HASH
    checks = []
    lock = load(ROOT / ".governance/standards.lock.json")
    if lock["schema"] != "clikvm.standards-lock/v1":
        raise ValueError("unsupported source-lock format")
    for source in lock["sources"]:
        if len(source["revision"]) != 40 or any(c not in "0123456789abcdef" for c in source["revision"]):
            raise ValueError("upstream must have an immutable Git revision")
        if source.get("local_file"):
            path = ROOT / source["local_file"]
            if not path.resolve().is_relative_to(ROOT):
                raise ValueError("source path escapes repository")
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
            if observed != source["local_sha256"]:
                raise ValueError(f"upstream contract digest changed: {source['local_file']}")
            document = load(path)
            if document.get("$schema"):
                Draft202012Validator.check_schema(document)
    checks.append("pinned-contract-digests")

    requirements = load(ROOT / ".governance/standard-requirements.json")
    required = {entry["id"] for entry in requirements["requirements"]}
    if not {"wellmanifest/poa", "wellmanifest/logs", "wellmanifest/semver"}.issubset(required):
        raise ValueError("missing declared Wellman requirements")
    checks.append("wellman-requirement-registration")

    operation_schema = load(ROOT / ".governance/upstream/poa-operation.schema.json")
    declared = load(ROOT / ".governance/poa-operations.json")
    if not declared:
        raise ValueError("empty operation catalog")
    expected = operations(declared[0]["provenance"]["recorded_at"])
    if expected != declared:
        raise ValueError("stale POA operation catalog; run scripts/standards.py generate")
    validator = Draft202012Validator(operation_schema, format_checker=FormatChecker())
    for operation in declared:
        validator.validate(operation)
    checks.append("poa.operation/v1-and-model-source-binding")

    process_schema = load(ROOT / ".governance/upstream/poa-process.schema.json")
    process_validator=Draft202012Validator(process_schema, format_checker=FormatChecker())
    for path in (".governance/poa.json", ".governance/poa-multidevice.json"):
        process_validator.validate(load(ROOT / path))
    checks.append("poa.process/v1")

    audit_schema = load(ROOT / ".governance/upstream/logs-event.schema.json")
    audit_validator = Draft202012Validator(audit_schema, format_checker=FormatChecker())
    example = load(ROOT / "docs/analysis/audit-example.json")
    validate_event(example)
    audit_validator.validate(example)
    checks.append("wellmanifest.logs/event/v1-example")
    event_count = 0
    if events_path:
        previous = ZERO_HASH
        with events_path.open(encoding="utf-8") as stream:
            for expected_sequence, line in enumerate(stream, 1):
                if not line.endswith("\n") or not line.strip():
                    raise ValueError(f"invalid JSONL framing on line {expected_sequence}")
                event = json.loads(line)
                validate_event(event)
                audit_validator.validate(event)
                if event["sequence"] != expected_sequence or event["previousHash"] != previous:
                    raise ValueError("audit hash-chain/sequence continuity failure")
                previous = event["eventHash"]
                event_count += 1
        checks.append("actual-audit-stream")
    return {"schema": "clikvm.standards-check/v1", "ok": True,
            "checks": checks, "events_checked": event_count, "operation_records":len(declared), "logical_action_types":8, "processes_checked":2,
            "scope": "offline extracted contracts and producer output; no fleet certification or hardware attestation"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "generate"], nargs="?", default="check")
    parser.add_argument("--events", type=Path, help="Also validate a produced wellmanifest JSONL audit stream")
    args = parser.parse_args()
    try:
        if args.command == "generate":
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            write(ROOT / ".governance/poa-operations.json", operations(now))
            print(json.dumps({"ok": True, "generated": ".governance/poa-operations.json"}))
        else:
            print(json.dumps(check(args.events), indent=2))
    except (ValueError, OSError, ImportError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
