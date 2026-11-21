# clikVM device protocol 0.1.0

This is the implemented **clikVM gateway API**, an adapter to deploy alongside OQLLM. These URLs are not claims about routes already deployed on `api.oqllm.com`. Runtime OpenAPI is at `/openapi.json`; interactive documentation is at `/docs`.

## Transport and credentials

The ESP32 is an outgoing HTTPS client. A gateway reverse proxy terminates trusted TLS; the ESP32 validates its certificate. Plain HTTP is for a consciously configured local test network only. Every device has a distinct random Bearer token, mapped to one `device_id` in `CLIK_DEVICE_TOKENS_JSON`. The operator dashboard has a different token, `CLIK_OPERATOR_TOKEN`. Device credentials cannot list all sessions, approve actions, cancel tasks or reconcile uncertainty. The operator cannot impersonate a device when opening a session.

Except the POA route helper, POST requests require `Idempotency-Key`: 8–128 characters from `[A-Za-z0-9._:-]`. Generate a random key for each **new logical request**. Retry the exact same request with the same key. Reusing a key with different input returns 409. Receiving the same lease twice after a network retry is **not** permission to execute it twice: firmware must remember the action/lease ID. An action whose result is unknown is not automatically redelivered.

Most POST responses use HTTP 200. Errors: 401 token absent/invalid; 403 wrong scope or disabled simulation; 404 unavailable session/frame; 409 stale frame, invalid lease or idempotency conflict; 413 input too large; 415 wrong media type; 422 malformed input; 502 configured upstream failed; 503 upstream not configured; 507 media quota exhausted.

Recovery codes are machine-readable in `X-Clik-Error-Code`: `session_expired` (409, includes superseded sessions), `session_not_found` (404), `lease_conflict` (409), `frame_conflict` (409), `idempotency_conflict` (409). A lost network response can be retried with the same key. A terminal 409 result acknowledgement must stop retrying: capture/reconcile without replaying input. An expired/missing session starts a new idle session; it never resumes the old task automatically.

### Start after ADB is authorized

`POST /v1/devices/{device_id}/sessions`

```json
{"boot_id":"5d8be499-c99d-4712-acf2-0c8b11cddc41","capabilities":["adb","aoa_hid","microphone","speaker","display"],"simulated":false}
```

```json
{"session_id":"cf579046-779e-4891-852a-17366df1a143","latest_frame":0,"expires_in_seconds":86400,"simulated":false}
```

`adb` capability is required. Bootstrap AOA menus and Android PIN/RSA consent happen locally before this server session. Starting a new session supersedes the preceding session of the same device. Any old unacknowledged lease becomes uncertain and cannot be executed in the new session. Reboot/reconnect uses a new session; never resume cached coordinates blindly.

### Upload an observation

`POST /v1/sessions/{sid}/frames`

Headers: `Content-Type: image/png`, `X-Frame-Id: 1`, `Authorization`, `Idempotency-Key`. Raw PNG body supports HTTP chunked transfer; no base64 is required on the device. Optional `X-Width` / `X-Height` must match IHDR if provided. Frame IDs increase monotonically within one session. Default maximum PNG is 4 MiB, configurable up to 16 MiB. PNG signature, chunk boundaries, CRC, IHDR dimensions and IEND are checked without decoding a full framebuffer.

```json
{"frame_id":1,"width":1080,"height":2400,"resource_urn":"urn:clikvm:frame:sha256:…","planning_intent":null}
```

Only authenticated `GET /v1/sessions/{sid}/frames/{frame_id}` exposes an image. Resource URNs identify content; they are not public download URLs. By default eight frames/session are retained, within a global media quota. Protected Android windows may be absent/blank even in a valid PNG; this is not bypassed by the gateway.

Starting a new session removes retained screenshots from the superseded session of that device. Their content hashes and event history remain, while image GET returns 404. This prevents repeated reconnects from retaining an unbounded collection of phone screens.

An upload invalidates old queued coordinates. Following a successful action acknowledgement, the next frame schedules the next planning step for the current user-started intent. On a real session, an unconfigured or failing planner marks the intent failed, never substitutes simulator output.

### User intent

`POST /v1/sessions/{sid}/intent`

```json
{"text":"Otwórz ustawienia Wi-Fi i sprawdź nazwę sieci","frame_id":1}
```

Response: `{"intent_id":"…","status":"planning"}`. Planning runs after the response. Read status or poll actions. Only one intent and one physical input action can be active. A user-started intent authorizes ordinary navigation; there is no autonomous task creation without a user goal. Default budget: 12 actions/intent. No success is inferred merely from an acknowledged tap.

If the latest observation is older than `CLIK_MAX_OBSERVATION_AGE_SECONDS` (default 5), the accepted intent first enters `awaiting_frame` and sets `capture_requested=true`. No model action is planned until the device uploads a new screenshot. This avoids acting on an old image after the phone has been idle. Set the limit to 0 to request a fresh observation for every new intent. The initial acknowledgement still says `planning` to describe the accepted planning workflow; `/status` gives its precise current stage.

### Lease one command

`POST /v1/sessions/{sid}/actions/next` with an empty JSON object `{}`.

This is POST because creating a lease is a command, not a CQRS query. New polls get new idempotency keys. Exact retries reuse a key.

```json
{
  "session_id":"…",
  "state":"ready",
  "latest_frame":1,
  "capture_requested":false,
  "action":{
    "action_id":"…",
    "lease_id":"…",
    "based_on_frame":1,
    "valid_for_ms":59999,
    "command":{"type":"tap","x":540,"y":1200},
    "operation_uri":"proc://clikvm/android/tap/v1",
    "after_action":"screenshot"
  }
}
```

Without a command, `action` is null. States include `idle`, `planning`, `awaiting_action`, `awaiting_frame`, `awaiting_confirmation`, `awaiting_result`, `uncertain`, `done`, `failed`, `blocked`, `cancelled`, `capture_requested`.

Before executing, firmware checks its current session, local frame number against `based_on_frame`, connection generation and lease validity. It records that lease locally and executes once. When an already-issued lease is outstanding, a fresh poll returns `awaiting_result` with no action. On timeout or server restart it becomes `uncertain`, never queued again. A lost HTTP response can therefore require reconciliation; correctness takes priority over repeating a possible Send tap.

An exact retry of the lease request preserves the action/lease identity but recalculates remaining `valid_for_ms`. A completed, stale, cancelled or expired cached lease returns 409; idempotency cannot renew its execution window.

Commands:

| `type` | Payload | Limits |
|---|---|---|
| `tap` | `x`, `y` | Integer pixels within current frame |
| `swipe` | `x`, `y`, `x2`, `y2`, `duration_ms` | 50–3000 ms |
| `key` | `key` | HOME, BACK, ENTER, TAB, ESCAPE, DPAD_UP/DOWN/LEFT/RIGHT, DEL, APP_SWITCH |
| `text` | `text` | 1–512 printable ASCII characters, excluding `%` |
| `open_url` | `url` | HTTP(S), ASCII/percent encoded, max 1024 chars |
| `screenshot` | No extra fields | Capture on Android |
| `wait` | `duration_ms` | 50–5000 ms |

The `text` limitation reflects Android shell `input text`, not STT or the user's intent language. Polish voice/intent text is preserved. Literal Unicode insertion needs a separately implemented Android IME/clipboard mechanism; it is deliberately rejected here. `%` is excluded because Android rewrites `%s` to a space. Firmware independently validates and shell-quotes commands. Arbitrary shell/model scripts are not part of this contract.

### Acknowledge, then observe

`POST /v1/sessions/{sid}/actions/{action_id}/result`

```json
{"lease_id":"…","status":"succeeded","detail":"ADB command exit status 0"}
```

Statuses: `succeeded`, `failed`, `uncertain`. Success means the command transport/execution completed, **not** that the user's goal succeeded. Response: `{"action_id":"…","status":"succeeded","next":"upload_frame"}`. Upload a new frame after the result. A screenshot command also follows this order: capture bytes, acknowledge execution, upload the observation. Wrong/expired lease: 409; do not repeat the physical action. Result retries use the same idempotency key.

### Status, capture and operator decisions

- `GET /v1/sessions/{sid}/status`: read-only projection; includes `state`, human-facing `message`, `intent`, `action`, `latest_frame`, `simulated` and `capture_requested`.
- `GET /v1/sessions`: operator-only list, latest 100 sessions.
- `POST /v1/sessions/{sid}/capture` `{}`: operator requests a fresh screenshot. Device polling sees `capture_requested: true`. Capture and upload; frame upload clears the flag. This works without a user intent and is useful for uncertainty reconciliation.
- `POST /v1/sessions/{sid}/actions/{aid}/confirm` `{"approve":true}`: operator-only. Approves only a still-current, unexpired action that explicitly awaits confirmation.
- `POST /v1/sessions/{sid}/cancel` `{}`: operator stops the intent. A command already leased becomes uncertain; cancellation cannot undo an action physically sent to Android.
- `POST /v1/sessions/{sid}/actions/{aid}/resolve` `{"resolution":"abandon","frame_id":2,"note":"Checked phone"}`: operator-only reconciliation of an uncertain action after a newer screenshot. Alternatives `executed`, `not_executed`. The old command is never reissued; start a new intent.

Model requests for consequential send/delete/purchase operations must flag `requires_confirmation`. Default routine navigation runs automatically. `CLIK_REQUIRE_ACTION_CONFIRMATION=1` optionally makes every input-changing command require approval. Image interpretation is fallible; the model's classification is not a guarantee that all consequential UI elements can be recognized.

### Audio

`POST /v1/sessions/{sid}/audio/stt`, raw `audio/wav`, PCM16 little-endian, mono, 16000 Hz, max 30 s and configured upload bound. Response `{"text":"Otwórz ustawienia"}`. ESP can record a bounded five-second push-to-talk sample, submit it, then create the intent using that text and the latest frame. STT does not create a task on its own.

`POST /v1/sessions/{sid}/audio/tts`, JSON `{"text":"Zadanie zakończone"}`. Response WAV PCM16 mono 16000 Hz, plus `X-Audio-Format: pcm_s16le`, `X-Sample-Rate: 16000`, `X-Channels: 1`. Gateway requests WAV from an explicitly configured OpenAI-compatible TTS endpoint and converts supported 16-bit mono/stereo 8–48 kHz PCM to the fixed playback format. Unsupported compressed/float WAV is rejected. A bounded on-disk retry cache retains the latest recordings; an expired retry returns 409, requiring a new request key. Provider errors are never treated as silent successful audio.

Audio endpoints default to **unconfigured**. They do not infer STT/TTS paths from the public OQLLM gateway. Raw speech is sent to the configured provider and not persisted in event logs. A TTS retry cache stores generated audio inside the private state directory.

### Existing OQLLM POA integration

Operator-only `POST /v1/oqllm/route` `{"query":"…"}` forwards to configured `CLIK_POA_ENDPOINT` with `action=route` and `{"query":"…","format":"nl"}`. This is the verified routing contract, separate from the newly implemented vision/device adapter. It never automatically dispatches an arbitrary procedure.

### Event storage and deployment

SQLite is the canonical event log plus command/query projection store. A mutation, its idempotency receipt, full `projection_changed` row events and official `wellmanifest.logs/event/v1` audit envelopes commit together. SQL guards reject updates/deletes of committed domain events. Projection changes encode floating SQLite timestamps as exact decimal strings, preserving the upstream no-float canonical JSON rule.

**Private domain events contain goal/action text and full projection rows**, so protect their state directory like the database itself. Official audit envelopes contain hashes/IDs, no screenshots, audio, prompts, tokens or raw command text. Domain JSONL and official hash-chained audit JSONL are separate recoverable projections. Export failure does not undo a successfully committed physical-action receipt; `/health` reports `event_export: deferred`. Stop the gateway and run `clikvm export-events` to retry. A partial final JSONL line is recovered from canonical SQLite.

After stopping the gateway, `clikvm rebuild-projections --destination NEW_DIRECTORY` streams committed events to rebuild sessions, frames, intents, actions and idempotency receipts in a new database, then copies retained images. It refuses an existing destination and leaves the source projections unchanged. Audio retry cache is ephemeral and is not reconstructed. Exact equality of rebuilt command projections is covered by tests. The exclusive owner lock prevents a second worker or a maintenance command from silently mutating a live state directory.

Run one gateway process/worker for this initial deployment. SQLite and JSONL file locks protect storage, but in-memory background planners and audio scheduling are intentionally single-process. Restarted planning is marked failed for explicit retry; leased actions are uncertain. For multi-worker deployment, first move planning leases into a durable job worker and validate distributed ownership; do not simply set `--workers 4`.
