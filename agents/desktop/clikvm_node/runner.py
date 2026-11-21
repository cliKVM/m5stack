from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

import httpx

from .common import ActionRejected, Backend, BackendError, Observation
from .config import NodeConfig, env_secret


class GatewayError(RuntimeError):
    def __init__(self, status: int, code: str = ""):
        super().__init__("Gateway HTTP " + str(status))
        self.status, self.code = status, code


class GatewayClient:
    def __init__(self, config: NodeConfig, *, client=None):
        self.config = config
        token = env_secret(config.token_env)
        if len(token) < 24 or "\r" in token or "\n" in token:
            raise ValueError("Device token must contain at least 24 characters and no line breaks")
        self.headers = {"Authorization": "Bearer " + token}
        self.client = client or httpx.Client(base_url=config.gateway_url, timeout=20, follow_redirects=False, trust_env=False)

    def close(self):
        self.client.close()

    def post(self, path: str, body=None, *, binary: bytes | None = None, headers=None, key: str | None = None):
        request_headers = {**self.headers, "Idempotency-Key": key or str(uuid.uuid4()), **(headers or {})}
        for attempt in range(3):
            try:
                with self.client.stream("POST", path, json=body if binary is None else None,
                                        content=binary, headers=request_headers) as response:
                    if response.status_code >= 500:
                        raise GatewayError(response.status_code, response.headers.get("X-Clik-Error-Code", ""))
                    if not 200 <= response.status_code < 300:
                        raise GatewayError(response.status_code, response.headers.get("X-Clik-Error-Code", ""))
                    data = bytearray()
                    for block in response.iter_bytes():
                        if len(data) + len(block) > 1048576:
                            raise RuntimeError("Gateway response exceeds byte limit")
                        data.extend(block)
                    result = json.loads(data)
                    if not isinstance(result, dict):
                        raise RuntimeError("Gateway response is not an object")
                    return result
            except (httpx.TransportError, GatewayError) as e:
                if isinstance(e, GatewayError) and e.status < 500:
                    raise
                if attempt == 2:
                    raise
                time.sleep(0.2 * (2 ** attempt))


class Journal:
    """One durable pending execution, guarded by a process lock.

    A crash after writing 'executing' is ambiguous, even if the backend had not
    reached the OS yet. On restart it is reported uncertain, never executed again.
    """

    def __init__(self, config: NodeConfig):
        self.root = config.state_dir
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "pending.json"
        self.identity = hashlib.sha256((config.gateway_url + "\0" + config.device_id).encode()).hexdigest()
        self.lock = (self.root / "owner.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.lock.seek(0)
                if not self.lock.read(1):
                    self.lock.write(b"0")
                    self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self.lock.close()
            raise RuntimeError("Another node owns this state directory") from e
        self.pending = None
        self.last = None
        try:
            self._recover()
        except BaseException:
            self.lock.close()
            raise

    def _recover(self):
        previous = self.root / "last-receipt.json"
        if previous.exists():
            if previous.stat().st_size > 4096:
                raise RuntimeError("Receipt history exceeds limit; manual recovery required")
            self.last = json.loads(previous.read_text())
            if self.last.get("identity") != self.identity:
                raise RuntimeError("Receipt history belongs to another target or gateway")
        if self.path.exists():
            if self.path.stat().st_size > 4096:
                raise RuntimeError("Pending journal exceeds limit; manual recovery required")
            self.pending = json.loads(self.path.read_text())
            if self.pending.get("identity") != self.identity:
                raise RuntimeError("State directory belongs to another target or gateway")
            if self.pending.get("status") == "executing":
                self.pending.update(status="uncertain", detail="Node restarted while an execution was pending")
                self.write(self.pending)

    def close(self):
        self.lock.close()

    def write(self, record: dict):
        record = {**record, "identity": self.identity}
        temporary = self.root / "pending.tmp"
        with temporary.open("w", encoding="utf-8") as out:
            if os.name != "nt":
                os.fchmod(out.fileno(), 0o600)
            json.dump(record, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(self.path)
        if os.name != "nt":
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        self.pending = record

    def clear(self, disposition: str):
        if self.pending:
            # Only the last receipt is kept; it contains IDs/status, not screenshots or text.
            record = {**self.pending, "disposition": disposition}
            previous = self.root / "last-receipt.json"
            previous.write_text(json.dumps(record, separators=(",", ":")))
            if os.name != "nt":
                previous.chmod(0o600)
            self.path.unlink(missing_ok=True)
            self.last = record
        self.pending = None


def log(event: str, **fields):
    print(json.dumps({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False), flush=True)


class NodeRunner:
    def __init__(self, config: NodeConfig, backend: Backend, client: GatewayClient, *, logger=log):
        self.config, self.backend, self.client = config, backend, client
        self.journal = Journal(config)
        self.log = logger
        self.sid = ""
        self.frame = self.frame_counter = 0
        self.observation = None
        self.need_observation = True
        self.backend_open = False
        self.stop_requested = False

    def close(self):
        self.backend.close()
        self.client.close()
        self.journal.close()

    def _new_session(self):
        if not self.backend_open:
            self.backend.open()
            self.backend_open = True
        # Probe the real observation before advertising a usable desktop/API target.
        observed = self.backend.observe()
        response = self.client.post(f"/v1/devices/{self.config.device_id}/sessions",
                                    {"boot_id": str(uuid.uuid4()), "capabilities": self.backend.capabilities,
                                     "simulated": False, "profile": self.backend.profile})
        sid = response.get("session_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            raise RuntimeError("Gateway returned an invalid session ID")
        self.sid, self.frame, self.frame_counter = sid, 0, 0
        self.need_observation = True
        self._upload(observed)
        self.log("session_started", device=self.config.device_id, session_id=sid,
                 transport=self.backend.profile["transport"], observation_kind=observed.kind)

    def _upload(self, observed: Observation | None = None):
        observed = observed or self.backend.observe()
        self.frame_counter += 1
        number = self.frame_counter
        if observed.kind == "screenshot":
            response = self.client.post(f"/v1/sessions/{self.sid}/frames", binary=observed.png,
                                        headers={"Content-Type": "image/png", "X-Frame-Id": str(number)})
        else:
            response = self.client.post(f"/v1/sessions/{self.sid}/observations", {"frame_id": number, "state": observed.state})
        if response.get("frame_id") != number:
            raise RuntimeError("Gateway acknowledged a different observation")
        self.observation, self.frame = observed, number
        self.need_observation = False

    def _acknowledge(self) -> None:
        record = self.journal.pending
        if not record:
            return
        body = {"lease_id": record["lease_id"], "status": record["status"], "detail": record["detail"]}
        try:
            self.client.post(f"/v1/sessions/{record['session_id']}/actions/{record['action_id']}/result", body, key=record["result_key"])
            disposition = "acknowledged"
        except GatewayError as e:
            if e.status not in {404, 409}:
                raise
            disposition = "terminal_conflict_review_required"
            if e.code in {"session_expired", "session_not_found"}:
                self.sid = ""
        self.log("action_receipt", action_id=record["action_id"], status=record["status"], disposition=disposition)
        self.journal.clear(disposition)
        self.need_observation = True

    def _execute_once(self, lease: dict, requested_at: float) -> None:
        aid, lid = lease.get("action_id"), lease.get("lease_id")
        if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", x) for x in (aid, lid)):
            raise RuntimeError("Gateway returned invalid lease identifiers")
        valid_ms = lease.get("valid_for_ms")
        if type(valid_ms) is not int or not 0 <= valid_ms <= 120000:
            raise RuntimeError("Invalid lease budget")
        deadline = requested_at + valid_ms / 1000  # Include the request RTT conservatively.
        record = {"session_id": self.sid, "action_id": aid, "lease_id": lid,
                  "result_key": str(uuid.uuid4()), "status": "executing", "detail": ""}
        last = self.journal.last
        if last and last.get("session_id") == self.sid and last.get("action_id") == aid and last.get("lease_id") == lid:
            self.journal.write({key: value for key, value in last.items() if key != "disposition"})
            return
        try:
            if self.journal.pending:
                raise RuntimeError("Cannot execute while a prior result is pending")
            if lease.get("based_on_frame") != self.frame or self.observation is None:
                raise ActionRejected("Lease refers to a stale observation")
            if deadline <= time.monotonic():
                raise ActionRejected("Lease expired before execution")
            self.backend.validate(lease.get("command"), self.observation)
        except ActionRejected as e:
            record.update(status="failed", detail=str(e))
            self.journal.write(record)
            return
        self.journal.write(record)  # Must become durable BEFORE sending any input.
        try:
            self.backend.execute(lease["command"], deadline)
        except Exception as e:
            record.update(status="uncertain", detail="Backend execution interrupted: " + type(e).__name__)
        else:
            record.update(status="succeeded", detail="Backend completed; awaiting a fresh observation for goal verification")
        self.journal.write(record)

    def step(self):
        # Recovery receipts are sent before creating another session or obtaining input.
        self._acknowledge()
        if not self.sid:
            self._new_session()
        if self.need_observation:
            self._upload()
        requested_at = time.monotonic()
        message = self.client.post(f"/v1/sessions/{self.sid}/actions/next", {})
        if message.get("capture_requested"):
            self._upload()
            return
        lease = message.get("action")
        if lease:
            self._execute_once(lease, requested_at)
            # Acknowledge before capture/planning. Never execute a second action here.
            self._acknowledge()
        if self.need_observation:
            self._upload()

    def run(self, *, once: bool = False):
        while not self.stop_requested:
            try:
                self.step()
            except GatewayError as e:
                self.log("gateway_error", status=e.status, code=e.code)
                if e.code in {"session_expired", "session_not_found"}:
                    self.sid = ""
                    self.need_observation = True
                elif e.status in {401, 403, 422}:
                    raise RuntimeError("Gateway rejected device credentials or protocol configuration") from e
            except httpx.TransportError:
                self.log("gateway_connection_unavailable")
            except (BackendError, OSError, TimeoutError) as e:
                self.log("backend_unavailable", error_type=type(e).__name__)
                self.backend.close()
                self.backend_open = False
                self.sid = ""
            if once:
                return
            time.sleep(self.config.poll_seconds)
