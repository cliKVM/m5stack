# Gateway verification — 0.2.0, 2026-11-21

**46/46 gateway tests passed**, with zero failures, errors or skipped cases. This includes all 30 original tests and 16 universal-profile/controller/migration tests. Exact output, duration and source hashes are in [verification-tests.log](verification-tests.log) and [verification-report.json](verification-report.json). Python 3.12 uses the pinned FastAPI/Pydantic/httpx/jsonschema dependencies; the JavaScript regression runs in Node.

```sh
PYTHONPATH=gateway .venv/bin/python -m unittest discover -s tests -p 'test_gateway*.py' -v
node --check gateway/clikvm/static/app.js
PYTHONPATH=gateway .venv/bin/python tests/frontend/test_live_dom.py
.venv/bin/python scripts/standards.py check
```

## Execution and isolation

- Full Android-compatible API cycle: session, raw PNG, user intent, typed action lease, execution receipt, second observation and completion.
- Real TCP/HTTP Uvicorn subprocess test using `clikvm simulate`, with explicitly synthetic screenshots and a scripted HOME acknowledgement.
- Desktop profile registration without ADB; platform and available operations included in the actual model request; generic operation URI instead of Android semantics.
- Unsupported RFB operation, unknown API operation, invalid argument range and extra URL argument all fail before a command can be leased.
- Actual JSON state/typed API loop, including float values, mandatory catalog confirmation, fresh readback and completion. Model receives JSON with no fabricated image.
- KM-only targets cannot upload either observation modality, request a capture, or start an autonomous intent.
- Controller credentials cannot enumerate devices, enter operator APIs, lease input or forge observations. Each is bound to one target.
- Controller goals always wait for a new observation; exact retries preserve identity even after the latest observation changes.
- Session restart during voice is rejected by the expected-session guard on intent, capture and cancel.
- Last contact with the target connector determines controller availability. A fresh poll restores online status.
- Scoped STT/TTS use deterministic provider mocks; exact STT retries do not call the provider twice.
- `init-env --controller` generates distinct scoped credentials, mode 0600 and refuses to overwrite a previously generated file.

## Lifecycle, storage and uncertainty

- No spontaneous action without a user-started intent.
- Monotonic observations, stale coordinate invalidation, capped leases and no automatic re-execution after expiry/restart.
- Old idle observations trigger a fresh capture before the first action is planned.
- Exact HTTP retries preserve action identity while recomputing remaining lease time; completed/expired cached leases return a terminal error.
- Operator-only confirmation and uncertainty reconciliation after a newer observation.
- Invalid/oversized PNG rejection; bounded PNG and JSON retention and media quota.
- Canonical domain transaction and official hash-chained audit envelope; projection outage recovery and partial-line recovery.
- Immutable SQL event ledger; replay equality for sessions, frames, intents, actions and idempotency projections.
- Migration from the v0.1 database shape retains Android data and reinstalls capture triggers with the new columns.
- Replay of v0.1 ledger rows explicitly upcasts only `profile` and `kind`; retained JSON media is copied when rebuilding a new state directory.
- Exclusive state-directory owner lock rejecting a second gateway worker.

## UI and provider contract

The dashboard regression executes the actual JavaScript in a DOM fixture. Delayed status, PNG or JSON from target A cannot enable actions for target B. Empty sessions clear the old observation and disable commands. API JSON is rendered with `textContent`, never HTML or a synthetic PNG.

Provider tests check actual OpenAI-compatible request construction, typed plan parsing, explicit POA routing without dispatch, multipart WAV STT and normalized PCM16 TTS. These requests terminate in controlled HTTP transports. The separate node suite runs the gateway over real TCP with actual RFB/API fixture servers and a native GUI boundary fixture; consult [agents/desktop](../../agents/desktop/) for its own evidence.

The POA generator exports **15 records representing eight logical action types**: seven preserved Android URI records and eight generic device records. All records and both canonical processes pass the pinned official schemas; the five local adoption checks pass. Local reference inlining preserves JSON Schema meaning without modifying the upstream contract. Existing full-Wellman adoption gaps remain described in [docs/standards.md](../../docs/standards.md).

The separate jsdom/live-HTTP contract also passes **7/7 checks** using the actual shipped HTML/JavaScript and Uvicorn gateway. Its capture test deliberately holds the POST, verifies no capture flag exists before dispatch, then verifies the flag after the returned handler promise resolves. Capture/cancel handlers now return their existing promise chain, avoiding a race between an unawaited POST and the next device poll. The firmware HTML request shapes are checked against an explicitly simulated device API. Evidence: [frontend-tests.log](frontend-tests.log) and [browser-contract-check.json](../../docs/analysis/browser-contract-check.json). This is DOM/HTTP verification, not raster browser rendering.

## What remains NOT_RUN

Physical USB/ADB/HID compatibility, handset-specific screenshots, Windows/macOS/X11 permission and coordinate behavior, microphone quality, M5 Wi-Fi/TLS latency, real VNC server diversity and actual OQLLM provider credentials/model behavior are **NOT_RUN**. A passed source/API/fixture test is not a hardware acceptance certificate. No production provider mutation, Android action or GitHub publication was executed by these tests.

[openapi.json](openapi.json) is generated from the actual 0.2.0 application. [API.md](API.md), [UNIVERSAL.md](UNIVERSAL.md) and [CONTROLLERS.md](CONTROLLERS.md) document lifecycle and capability semantics beyond structural schemas.
