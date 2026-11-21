# Gateway verification — 2026-11-21

Verified using Python 3.12 in the project's `.venv`, the pinned FastAPI/Pydantic/httpx dependencies and Node 24 for the dashboard race regression.

```sh
PYTHONPATH=gateway .venv/bin/python -m unittest discover -s tests -p 'test_gateway*.py' -v
node --check gateway/clikvm/static/app.js
```

The test suite includes:

- Full API cycle: session, raw PNG, user intent, typed action lease, execution receipt, second observation and completion.
- Real TCP/HTTP subprocess smoke test using `clikvm simulate`, with explicitly synthetic screenshots and a scripted HOME acknowledgement.
- Per-device authorization boundaries and operator-only approvals.
- No spontaneous action without a user-started intent.
- Monotonic observations, stale coordinate invalidation, capped leases and no automatic re-execution after expiry/restart.
- Old idle observations trigger a new capture before the first action of a new task is planned.
- Exact HTTP retries preserve action identity while recomputing remaining lease time; completed/expired cached leases return a terminal error.
- Stable `X-Clik-Error-Code` headers for terminal lease/session failures.
- Reconciliation requiring a newer screenshot.
- Invalid/oversized PNG rejection and bounded retained frame count.
- Canonical domain transaction + official hash-chained audit envelopes, projection outage recovery and partial-line recovery.
- Immutable SQL event ledger and exact replay equality for sessions, frames, intents, actions and idempotency projections.
- Exclusive state-directory owner lock rejecting a second worker.
- OpenAI-compatible vision requests and strict action output parsing through an HTTP transport mock.
- Explicit POA routing (no implicit dispatch), STT multipart WAV and TTS PCM16 normalization through a transport mock.
- Dashboard race regression executing the actual JavaScript: delayed status/image for phone A cannot enable actions for phone B; an empty session list clears the old image and disables commands.

**Scope:** these tests do not establish physical USB compatibility, Android AOA/ADB correctness on a handset, actual provider credentials/model behavior, microphone quality or Wi-Fi/TLS latency on ESP32. Those checks belong to the hardware acceptance runbook. No production provider POST, Android action or GitHub mutation was executed by these tests.

`gateway/docs/openapi.json` is generated from the actual application, including Bearer authorization, binary PNG/WAV inputs and typed session/frame/action/result responses. `gateway/docs/API.md` describes retry and lifecycle semantics beyond OpenAPI's structural types.
