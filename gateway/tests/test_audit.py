"""Adversarial checks for the audit boundary, using the pinned upstream schema."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
from clikvm.audit import ZERO_HASH, build_audit_event, canonical, validate_event


class AuditContractTests(unittest.TestCase):
    def event(self, sequence=1, previous=ZERO_HASH, **overrides):
        arguments = dict(event_type="clikvm.action_completed",
                         subject_ref="urn:clikvm:session:fixture",
                         correlation_id="fixture-command-1",
                         payload={"type": "tap", "x": 10, "y": 20},
                         occurred_at="2026-11-21T12:00:00Z", outcome="SUCCEEDED")
        arguments.update(overrides)
        return build_audit_event(sequence, previous, **arguments)

    def test_events_match_independent_upstream_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((ROOT / ".governance/upstream/logs-event.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        event = self.event()
        successor = self.event(2, event["eventHash"], outcome="FAILED", severity="ERROR")
        for row in (event, successor):
            validator.validate(row)
            validate_event(row)
        self.assertEqual(successor["previousHash"], event["eventHash"])
        self.assertNotIn("payload", event)

    def test_tampering_and_extra_fields_are_detected(self):
        original = self.event()
        changed = deepcopy(original)
        changed["outcome"] = "FAILED"
        with self.assertRaises(ValueError):
            validate_event(changed)
        changed = deepcopy(original)
        changed["private_payload"] = "must not be exported"
        with self.assertRaises(ValueError):
            validate_event(changed)

    def test_invalid_inputs_fail_before_persistence(self):
        for value in (True, 0, -1, 2**53):
            with self.subTest(sequence=value), self.assertRaises(ValueError):
                self.event(sequence=value)
        with self.assertRaises(ValueError):
            self.event(previous="bad predecessor")
        with self.assertRaises(ValueError):
            self.event(occurred_at="2026-11-21T12:00:00")
        with self.assertRaises(ValueError):
            self.event(event_type="logs.action_completed")

    def test_canonical_domain_rejects_floats_and_invalid_unicode(self):
        for data in ({"latency": 0.1}, {"bad": float("nan")}, {2: "value"}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                canonical(data)
        with self.assertRaises(UnicodeError):
            canonical({"bad": "\ud800"})
        self.assertEqual(canonical({"b": 2, "a": "żółć"}), '{"a":"żółć","b":2}')


if __name__ == "__main__":
    unittest.main()
