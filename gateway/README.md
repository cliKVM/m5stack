# Gateway clikVM / OQLLM

Runnable Python gateway for the ESP32-S3 Android controller. The physical controller remains USB host; this server handles typed plans, image/audio provider adapters and durable events. It can be deployed on the same server as OQLLM. A user's laptop is not required in the final device-to-server path.

## Local start

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/clikvm init-env --device cores3-01 --simulation
.venv/bin/clikvm --env-file .env serve --host 127.0.0.1 --port 8787
```

In another terminal:

```sh
.venv/bin/clikvm --env-file .env simulate --device cores3-01 --url http://127.0.0.1:8787
```

The simulator uses **real HTTP** for session → PNG → user intent → action lease → acknowledgement → next PNG. Images are synthetic; the session and every completion message are labelled simulation. It acknowledges only the harmless scripted HOME command. It does not test USB, Android or an LLM. Set `--hold-seconds 300` to keep the synthetic device polling while inspecting the panel.

Open `http://127.0.0.1:8787/`. Enter the operator token from the newly created local `.env`. The panel keeps it only in memory. Select the session, view authenticated screenshots and issue a task. Do not paste the device token into the operator login. For real hardware, set `CLIK_ALLOW_SIMULATION=0` and provision its own device token. Starting a real session never falls back to simulation.

`init-env` creates random device/operator tokens without printing them, uses mode 0600 and refuses to overwrite an existing file. The CLI reads `.env` without evaluating shell code. Environment variables already exported take precedence. Do not commit `.env` or `.state/`.

## OQLLM and model configuration

The inspected public OQLLM OpenAPI (`https://api.oqllm.com/openapi.json`, 2026-11-21) exposes POA routing/dispatch, catalog and authentication. It does not describe the binary screenshot, STT, TTS or device-command contract needed here. This repository implements that adapter explicitly. The linked private `oqllm/oqllm` source was unavailable, so no source-level compatibility claim is made.

Set the full endpoint of a **vision-capable OpenAI-compatible chat-completions service**:

```dotenv
CLIK_MODEL_ENDPOINT='https://your-oqllm-adapter.example/v1/chat/completions'
CLIK_MODEL_NAME='your-vision-model'
CLIK_MODEL_API_KEY='your-server-side-key'
```

This illustrative hostname is not a deployed service. Use the actual configured local OQLLM proxy/provider endpoint. The adapter sends one current image plus user goal and bounded prior actions; it requests strict JSON. Returned shell commands, unknown actions, stale coordinates and unsupported literal text are rejected. Until this endpoint is configured, real sessions report a planning configuration failure.

An existing OQLLM POA key can independently enable the operator route helper:

```dotenv
CLIK_POA_ENDPOINT='https://api.oqllm.com/v1/poa/'
CLIK_POA_API_KEY='your-oqllm-key'
```

Configure audio with explicit provider endpoints:

```dotenv
CLIK_STT_ENDPOINT='https://your-audio-provider.example/v1/audio/transcriptions'
CLIK_STT_MODEL='your-stt-model'
CLIK_STT_API_KEY='your-server-side-key'
CLIK_TTS_ENDPOINT='https://your-audio-provider.example/v1/audio/speech'
CLIK_TTS_MODEL='your-tts-model'
CLIK_TTS_VOICE='your-polish-voice'
CLIK_TTS_API_KEY='your-server-side-key'
```

STT uses multipart WAV + optional model and expects JSON `text`. TTS requests WAV with `model`, `input`, `voice`, `response_format`. Full provider keys stay on the server; firmware only receives a scoped clikVM device token. Production upstreams require HTTPS. A local HTTP service requires deliberate `CLIK_ALLOW_HTTP_UPSTREAMS=1`. Environment HTTP/SOCKS proxy variables are not implicitly inherited by upstream clients.

## Settings

| Variable | Default / purpose |
|---|---|
| `CLIK_DEVICE_TOKENS_JSON` | `{}`; JSON device-ID → unique token, min 24 characters |
| `CLIK_OPERATOR_TOKEN` | Empty; separate operator authorization |
| `CLIK_STATE_DIR` | `.state/gateway` |
| `CLIK_ALLOW_SIMULATION` | `0` |
| `CLIK_REQUIRE_ACTION_CONFIRMATION` | `0`; strict optional per-input approval |
| `CLIK_MAX_FRAME_BYTES` | 4194304 |
| `CLIK_MAX_AUDIO_BYTES` | 4194304 |
| `CLIK_MAX_STORAGE_BYTES` | 268435456, private media quota |
| `CLIK_FRAMES_PER_SESSION` | 8 |
| `CLIK_LEASE_SECONDS` | 60; maximum 120, allows a 45-second capture plus acknowledgement |
| `CLIK_ACTION_TTL_SECONDS` | 120 |
| `CLIK_SESSION_TTL_SECONDS` | 86400 |
| `CLIK_MAX_ACTIONS_PER_INTENT` | 12 |
| `CLIK_MAX_OBSERVATION_AGE_SECONDS` | 5; older observations trigger an automatic capture before a new goal is planned |
| `CLIK_MODEL_TIMEOUT_SECONDS` | 45 |

Fresh Android debugging/trust dialogs are completed on the phone. The server does not approve them. Routine navigation in a user-started task runs automatically. Actions flagged as consequential require an operator confirmation. A UI classifier is fallible; strict mode is available when the application needs it.

## Tests and evidence

```sh
PYTHONPATH=gateway python -m unittest discover -s tests -p 'test_gateway*.py' -v
python scripts/standards.py check --events .state/gateway/logs/wellmanifest-events.jsonl
```

Tests cover the complete API loop, auth isolation, no actions without an intent, stale screenshots, idempotency conflicts, no duplicate leases, uncertain execution, restart recovery, operator-only approval, bounded media retention, invalid images, projection outage recovery, provider contract parsing, STT/TTS WAV conversion and explicit simulation isolation. Provider tests use a deterministic HTTP transport mock. The separate `simulate` CLI tests a real TCP/HTTP server. Neither substitutes for physical USB acceptance tests.

## Data and operations

`events.sqlite3` is the canonical event store. Semantic domain events, complete projection-change events, official audit envelopes and command receipts commit in one transaction. The immutable event ledger can replay sessions, frames, intents, actions and receipts into a new database. `logs/domain-events.jsonl` is a **private** domain projection that includes goals and action text; `logs/wellmanifest-events.jsonl` uses the verified upstream audit schema/hash chain and contains hashes rather than raw user content. Screenshots remain content-addressed resources; deleted historical media cannot be reconstructed from a hash.

Stop the gateway before maintenance (an exclusive state-directory lock enforces this):

```sh
.venv/bin/clikvm --env-file .env export-events
.venv/bin/clikvm --env-file .env rebuild-projections --destination .state/rebuilt
```

Rebuild refuses an existing destination, leaves source projections unchanged, streams the event log without loading it all into RAM and copies retained images. Tests compare exact rebuilt SQL projections. The generated-audio retry cache is deliberately ephemeral.

Deploy with a trusted HTTPS reverse proxy and a private persistent state volume. Run **one process/worker**. A multi-process durable planner is a future deployment extension, not an implied feature. Back up the SQLite database with the SQLite backup API or a consistent stopped-service copy, together with retained media. Exact API, TTL and retry behavior: [docs/API.md](docs/API.md).
