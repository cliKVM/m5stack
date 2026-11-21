from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from .config import Settings

log = logging.getLogger(__name__)
PROJECTED_TABLES = ("sessions", "frames", "intents", "actions", "idempotency")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def identifier() -> str:
    return str(uuid.uuid4())


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Validate a bounded PNG container without decoding a full framebuffer."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(415, "Expected a PNG screenshot")
    position, width, height, seen_data, seen_end = 8, 0, 0, False, False
    while position < len(data):
        if position + 12 > len(data):
            raise HTTPException(422, "Truncated PNG chunk")
        length = int.from_bytes(data[position:position + 4], "big")
        kind = data[position + 4:position + 8]
        end = position + 12 + length
        if end > len(data):
            raise HTTPException(422, "PNG chunk exceeds body")
        content = data[position + 8:position + 8 + length]
        crc = int.from_bytes(data[position + 8 + length:end], "big")
        if zlib.crc32(kind + content) & 0xFFFFFFFF != crc:
            raise HTTPException(422, "PNG CRC mismatch")
        if position == 8:
            if kind != b"IHDR" or length != 13:
                raise HTTPException(422, "PNG must start with IHDR")
            width, height = struct.unpack(">II", content[:8])
            if not 1 <= width <= 16384 or not 1 <= height <= 16384:
                raise HTTPException(422, "PNG dimensions out of bounds")
        elif kind == b"IHDR":
            raise HTTPException(422, "Duplicate PNG IHDR")
        if kind == b"IDAT":
            seen_data = True
        if kind == b"IEND":
            if length != 0 or end != len(data):
                raise HTTPException(422, "Invalid PNG ending")
            seen_end = True
        position = end
    if not seen_end or not seen_data:
        raise HTTPException(422, "Incomplete PNG")
    return width, height


class Store:
    def __init__(self, settings: Settings, recover: bool = True):
        self.settings = settings
        self.root = settings.state_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        import fcntl
        self.owner_lock = (self.root / ".owner.lock").open("a+b")
        try:
            fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.owner_lock.close()
            raise RuntimeError("State directory is in use. Run one gateway worker; stop it before maintenance.") from exc
        self.frames_dir = self.root / "frames"
        self.logs_dir = self.root / "logs"
        self.frames_dir.mkdir(exist_ok=True, mode=0o700)
        self.logs_dir.mkdir(exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.export_error: str | None = None
        self.db = sqlite3.connect(self.root / "events.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, device_id TEXT NOT NULL, boot_id TEXT NOT NULL,
            simulated INTEGER NOT NULL, capabilities TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, expires REAL NOT NULL, latest_frame INTEGER NOT NULL DEFAULT 0,
            active_intent TEXT, capture_requested INTEGER NOT NULL DEFAULT 0,
            profile TEXT NOT NULL DEFAULT '{}'
          );
          CREATE INDEX IF NOT EXISTS session_device ON sessions(device_id, created);
          CREATE TABLE IF NOT EXISTS frames(
            session_id TEXT NOT NULL REFERENCES sessions(id), frame_id INTEGER NOT NULL,
            digest TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
            size INTEGER NOT NULL, created REAL NOT NULL, retained INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL DEFAULT 'png',
            PRIMARY KEY(session_id, frame_id)
          );
          CREATE TABLE IF NOT EXISTS intents(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            goal TEXT NOT NULL, status TEXT NOT NULL, frame_id INTEGER NOT NULL,
            steps INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, detail TEXT NOT NULL DEFAULT ''
          );
          CREATE TABLE IF NOT EXISTS actions(
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            intent_id TEXT NOT NULL REFERENCES intents(id), frame_id INTEGER NOT NULL,
            command TEXT NOT NULL, reason TEXT NOT NULL, confirmation INTEGER NOT NULL,
            state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
            lease_id TEXT, lease_expires REAL, result TEXT
          );
          CREATE INDEX IF NOT EXISTS action_session ON actions(session_id, created);
          CREATE TABLE IF NOT EXISTS idempotency(
            actor TEXT NOT NULL, key TEXT NOT NULL, route TEXT NOT NULL, request_hash TEXT NOT NULL,
            response TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(actor,key)
          );
          CREATE TABLE IF NOT EXISTS domain_events(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL, session_id TEXT, occurred_at TEXT NOT NULL,
            payload TEXT NOT NULL, audit_json TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS audio_cache(
            actor TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
            session_id TEXT NOT NULL, digest TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(actor,key)
          );
          CREATE TABLE IF NOT EXISTS projection_changes(
            ordinal INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
            operation TEXT NOT NULL, row_json TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS gateway_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON domain_events
            BEGIN SELECT RAISE(ABORT,'domain events are append only'); END;
          CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON domain_events
            BEGIN SELECT RAISE(ABORT,'domain events are append only'); END;
        """)
        if "capture_requested" not in {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}:
            self.db.execute("ALTER TABLE sessions ADD COLUMN capture_requested INTEGER NOT NULL DEFAULT 0")
        if "profile" not in {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}:
            self.db.execute("ALTER TABLE sessions ADD COLUMN profile TEXT NOT NULL DEFAULT '{}'")
        if "kind" not in {r[1] for r in self.db.execute("PRAGMA table_info(frames)")}:
            self.db.execute("ALTER TABLE frames ADD COLUMN kind TEXT NOT NULL DEFAULT 'png'")
        self._install_projection_capture()
        with self.transaction():
            if not self.db.execute("SELECT 1 FROM gateway_metadata WHERE key='projection_capture_v1'").fetchone():
                # Explicit baseline migration for a state directory created by an
                # earlier development revision. Subsequent changes are captured by
                # SQL triggers inside the same mutation transaction.
                for table in PROJECTED_TABLES:
                    for row in self.db.execute(f"SELECT * FROM {table}").fetchall():
                        state = {k: str(v) if isinstance(v, float) else v for k, v in dict(row).items()}
                        self.db.execute("INSERT INTO projection_changes(table_name,operation,row_json) VALUES(?,'upsert',?)", (table, canonical(state)))
                self.db.execute("INSERT INTO gateway_metadata VALUES('projection_capture_v1','1')")
        # A restart cannot determine whether a physical action was executed.
        # Never resend a pre-crash lease, and never pretend an in-flight model completed.
        if recover:
            with self.transaction():
                rows = self.db.execute("SELECT id,session_id FROM actions WHERE state='leased'").fetchall()
                for row in rows:
                    self.db.execute("UPDATE actions SET state='uncertain' WHERE id=?", (row["id"],))
                    self.event("action_uncertain", row["session_id"], {"action_id": row["id"], "cause": "gateway_restart"})
                self.db.execute("UPDATE intents SET status='failed',detail='gateway_restart_during_planning' WHERE status='planning'")
        self.export_events()

    @contextlib.contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._flush_projection_changes()
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def _install_projection_capture(self):
        for table in PROJECTED_TABLES:
            columns = self.db.execute(f"PRAGMA table_info({table})").fetchall()
            for operation, reference, label in [("INSERT", "NEW", "upsert"), ("UPDATE", "NEW", "upsert"), ("DELETE", "OLD", "delete")]:
                args = []
                for column in columns:
                    name, datatype = column[1], column[2]
                    value = f"{reference}.{name}"
                    # Official canonical JSON excludes floats. Preserve double
                    # precision as decimal text; SQLite affinity restores it on replay.
                    if datatype == "REAL":
                        value = f"CASE WHEN {value} IS NULL THEN NULL ELSE printf('%!.17g',{value}) END"
                    args.extend([f"'{name}'", value])
                self.db.execute(f"DROP TRIGGER IF EXISTS capture_{table}_{operation.lower()}")
                self.db.execute(f"""CREATE TRIGGER capture_{table}_{operation.lower()}
                    AFTER {operation} ON {table} BEGIN
                    INSERT INTO projection_changes(table_name,operation,row_json)
                    VALUES('{table}','{label}',json_object({','.join(args)})); END""")

    def _flush_projection_changes(self):
        rows = self.db.execute("SELECT * FROM projection_changes ORDER BY ordinal").fetchall()
        for row in rows:
            state = json.loads(row["row_json"])
            sid = state.get("session_id") or (state.get("id") if row["table_name"] == "sessions" else None)
            self.event("projection_changed", sid, {"table": row["table_name"], "operation": row["operation"], "row": state})
        self.db.execute("DELETE FROM projection_changes")

    def close(self):
        with self.lock:
            self.db.close()
            self.owner_lock.close()

    def event(self, kind: str, session_id: str | None, payload: dict):
        from .audit import build_audit_event
        stamp = now_iso()
        previous = self.db.execute("SELECT sequence,audit_json FROM domain_events ORDER BY sequence DESC LIMIT 1").fetchone()
        sequence = previous["sequence"] + 1 if previous else 1
        previous_hash = json.loads(previous["audit_json"])["eventHash"] if previous else "0" * 64
        audit = build_audit_event(sequence, previous_hash, event_type="clikvm." + kind,
                                  subject_ref="urn:clikvm:session:" + (session_id or "gateway"),
                                  correlation_id=session_id or "gateway", payload=payload, occurred_at=stamp)
        self.db.execute("INSERT INTO domain_events(sequence,event_id,kind,session_id,occurred_at,payload,audit_json) VALUES(?,?,?,?,?,?,?)",
                        (sequence, identifier(), kind, session_id, stamp, canonical(payload), canonical(audit)))

    def mutation(self, actor: str, key: str, route: str, request: object, apply: Callable[[], dict]) -> tuple[dict, bool]:
        digest = hashlib.sha256(canonical(request).encode()).hexdigest()
        with self.transaction():
            if self.db.execute("SELECT 1 FROM audio_cache WHERE actor=? AND key=?", (actor, key)).fetchone():
                raise HTTPException(409, "Idempotency-Key already used by an audio operation", headers={"X-Clik-Error-Code":"idempotency_conflict"})
            cached = self.db.execute("SELECT * FROM idempotency WHERE actor=? AND key=?", (actor, key)).fetchone()
            if cached:
                if cached["route"] != route or cached["request_hash"] != digest:
                    raise HTTPException(409, "Idempotency-Key already used for a different request", headers={"X-Clik-Error-Code":"idempotency_conflict"})
                return json.loads(cached["response"]), True
            result = apply()
            self.db.execute("INSERT INTO idempotency VALUES(?,?,?,?,?,?)", (actor, key, route, digest, canonical(result), time.time()))
        self.export_events()
        return result, False

    def get_session(self, session_id: str, device_id: str | None = None, active: bool = True) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row or (device_id is not None and row["device_id"] != device_id):
            raise HTTPException(404, "Session not found", headers={"X-Clik-Error-Code":"session_not_found"})
        if active and (row["status"] != "active" or row["expires"] <= time.time()):
            raise HTTPException(409, "Session expired or superseded; start a new session", headers={"X-Clik-Error-Code":"session_expired"})
        return row

    def active_action(self, session_id: str):
        return self.db.execute("SELECT * FROM actions WHERE session_id=? AND state IN ('queued','awaiting_confirmation','leased','uncertain') ORDER BY created DESC LIMIT 1", (session_id,)).fetchone()

    def expire_actions(self, session_id: str):
        now = time.time()
        rows = self.db.execute("SELECT * FROM actions WHERE session_id=? AND state IN ('leased','queued','awaiting_confirmation')", (session_id,)).fetchall()
        for row in rows:
            state = None
            if row["state"] == "leased" and row["lease_expires"] <= now:
                state = "uncertain"
            elif row["state"] != "leased" and row["expires"] <= now:
                state = "expired"
            if state:
                self.db.execute("UPDATE actions SET state=? WHERE id=?", (state, row["id"]))
                self.db.execute("UPDATE intents SET status='blocked',detail=? WHERE id=?", ("action_" + state, row["intent_id"]))
                self.event("action_" + state, session_id, {"action_id": row["id"]})

    def get_frame(self, session_id: str, frame_id: int) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM frames WHERE session_id=? AND frame_id=? AND retained=1", (session_id, frame_id)).fetchone()
        if not row or not self.observation_path(row).is_file():
            raise HTTPException(404, "Frame not retained")
        return row

    def observation_path(self, row):
        return self.frames_dir / (row["digest"] + (".json" if row["kind"] == "json" else ".png"))

    def save_frame_file(self, digest: str, data: bytes, kind="png"):
        if kind not in {"png", "json"}:
            raise ValueError("Unknown observation media kind")
        path = self.frames_dir / (digest + "." + kind)
        if path.exists():
            return
        used = sum(p.stat().st_size for p in self.frames_dir.iterdir() if p.suffix in {".png", ".wav", ".json"})
        if used + len(data) > self.settings.max_storage_bytes:
            raise HTTPException(507, "Screenshot storage quota exhausted")
        temporary = self.frames_dir / (".upload-" + identifier())
        try:
            with temporary.open("xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def prune_frames(self):
        with self.transaction():
            sessions = self.db.execute("SELECT id FROM sessions").fetchall()
            for session in sessions:
                rows = self.db.execute("SELECT frame_id FROM frames WHERE session_id=? AND retained=1 ORDER BY frame_id DESC LIMIT -1 OFFSET ?",
                                       (session["id"], self.settings.frames_per_session)).fetchall()
                for row in rows:
                    self.db.execute("UPDATE frames SET retained=0 WHERE session_id=? AND frame_id=?", (session["id"], row["frame_id"]))
            retained = {r[0] for r in self.db.execute("SELECT DISTINCT digest FROM frames WHERE retained=1")}
            for path in self.frames_dir.iterdir():
                if path.suffix in {".png", ".json"} and path.stem not in retained:
                    path.unlink(missing_ok=True)
        self.export_events()

    def export_events(self):
        """Recoverable projections. SQLite commit remains successful if disk export fails.

        flock plus a complete-line tail prevents concurrent exporter duplication. A
        partial last line from a crash is discarded; committed rows are then replayed.
        """
        import fcntl
        try:
            with self.lock, (self.logs_dir / ".export.lock").open("a+b") as lockfile:
                fcntl.flock(lockfile, fcntl.LOCK_EX)
                for filename, audit in [("domain-events.jsonl", False), ("wellmanifest-events.jsonl", True)]:
                    path = self.logs_dir / filename
                    last = 0
                    with path.open("a+b") as target:
                        target.seek(0, os.SEEK_END)
                        size = target.tell()
                        if size:
                            start = max(0, size - 65536)
                            target.seek(start)
                            tail = target.read()
                            ending = tail.rfind(b"\n")
                            if ending < 0:
                                if start:
                                    raise ValueError("Event projection line exceeds recovery limit")
                                target.truncate(0)
                            else:
                                target.truncate(start + ending + 1)
                                lines = tail[:ending].split(b"\n")
                                last = json.loads(lines[-1])["sequence"]
                        rows = self.db.execute("SELECT * FROM domain_events WHERE sequence>? ORDER BY sequence", (last,))
                        target.seek(0, os.SEEK_END)
                        for row in rows:
                            item = json.loads(row["audit_json"]) if audit else {
                                "sequence": row["sequence"], "event_id": row["event_id"], "type": row["kind"],
                                "session_id": row["session_id"], "occurred_at": row["occurred_at"], "data": json.loads(row["payload"])}
                            target.write((canonical(item) + "\n").encode())
                        target.flush()
                        os.fsync(target.fileno())
                fcntl.flock(lockfile, fcntl.LOCK_UN)
            self.export_error = None
        except Exception as exc:
            self.export_error = type(exc).__name__
            log.warning("Event projection deferred (%s); committed events remain in SQLite", self.export_error)

    def rebuild(self, destination: Path):
        """Rebuild disposable SQL read models from committed events into a new dir.

        Retained media is copied as content-addressed resources. The audio retry
        cache is intentionally ephemeral and not part of the command projections.
        """
        import dataclasses
        import shutil
        destination = destination.resolve()
        if destination.exists():
            raise ValueError("Rebuild destination must not already exist")
        with self.lock:
            # Keep a consistent source snapshot and block command writers while
            # replaying. Use this maintenance operation in the single-process app.
            events = self.db.execute("SELECT * FROM domain_events ORDER BY sequence")
            target = Store(dataclasses.replace(self.settings, state_dir=destination), recover=False)
            try:
                for table in PROJECTED_TABLES:
                    for operation in ("insert", "update", "delete"):
                        target.db.execute(f"DROP TRIGGER IF EXISTS capture_{table}_{operation}")
                target.db.execute("BEGIN IMMEDIATE")
                for event in events:
                    payload = json.loads(event["payload"])
                    if event["kind"] == "projection_changed":
                        table = payload["table"]
                        if table not in PROJECTED_TABLES:
                            raise ValueError("Unknown projection table in event")
                        info = target.db.execute(f"PRAGMA table_info({table})").fetchall()
                        columns = [r[1] for r in info]
                        primary = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
                        row = payload["row"]
                        # Explicit v0.1 -> v0.2 upcast preserves older ledger rows.
                        if table == "sessions" and "profile" not in row:
                            row["profile"] = "{}"
                        if table == "frames" and "kind" not in row:
                            row["kind"] = "png"
                        if set(row) != set(columns):
                            raise ValueError("Projection schema differs from event; migration required")
                        if payload["operation"] == "delete":
                            target.db.execute(f"DELETE FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in primary), tuple(row[k] for k in primary))
                        elif payload["operation"] == "upsert":
                            updates = ",".join(f"{k}=excluded.{k}" for k in columns if k not in primary)
                            target.db.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT({','.join(primary)}) DO UPDATE SET {updates}", tuple(row[k] for k in columns))
                        else:
                            raise ValueError("Unknown projection operation")
                    target.db.execute("INSERT INTO domain_events(sequence,event_id,kind,session_id,occurred_at,payload,audit_json) VALUES(?,?,?,?,?,?,?)", tuple(event))
                target.db.execute("COMMIT")
                target._install_projection_capture()
                for row in target.db.execute("SELECT DISTINCT digest,kind FROM frames WHERE retained=1"):
                    original = self.observation_path(row)
                    if original.is_file():
                        shutil.copy2(original, target.frames_dir / original.name)
                target.export_events()
                if target.export_error:
                    raise RuntimeError("Replay succeeded but JSONL export was deferred")
            except BaseException:
                if target.db.in_transaction:
                    target.db.execute("ROLLBACK")
                raise
            finally:
                target.close()
