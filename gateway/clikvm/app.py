from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pathlib import Path

from .config import Settings
from .models import (ActionResult, Confirmation, IntentRequest, Resolution, RouteRequest, SessionStart, SpeakRequest,
                     SessionCreated, FrameAccepted, IntentCreated, NextActionResponse, ResultAccepted)
from .store import Store, canonical, identifier, png_dimensions
from .upstream import Upstreams


@dataclass(frozen=True)
class Principal:
    device_id: str | None
    operator: bool

    @property
    def actor(self):
        return "operator" if self.operator else "device:" + str(self.device_id)


class BodyLimitMiddleware:
    def __init__(self, app, settings: Settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        limit = self.settings.max_frame_bytes if path.endswith("/frames") else self.settings.max_audio_bytes if path.endswith("/audio/stt") else 16384
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Invalid Content-Length"}, 400)(scope, receive, send)
        if length > limit:
            return await JSONResponse({"detail": "Request body exceeds limit"}, 413)(scope, receive, send)
        seen = 0
        async def bounded_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, "Request body exceeds limit")
            return message
        return await self.app(scope, bounded_receive, send)


def create_app(settings: Settings | None = None, upstream_client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    store = Store(settings)
    upstream = Upstreams(settings, upstream_client)
    planning_slots = asyncio.Semaphore(4)
    audio_serial = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.export_events()
        await upstream.close()
        store.close()

    app = FastAPI(title="clikVM Device Gateway", version="0.1.0", lifespan=lifespan,
                  description="Typed ESP32 Android controller API. All media and device operations require scoped authorization.")
    app.state.store, app.state.settings, app.state.upstream = store, settings, upstream
    app.add_middleware(BodyLimitMiddleware, settings=settings)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'"
        if request.url.path in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://fastapi.tiangolo.com; connect-src 'self'; frame-ancestors 'none'"
        return response

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
        token = credentials.credentials
        token_hash = hashlib.sha256(token.encode()).digest()
        if settings.operator_token and secrets.compare_digest(token_hash, hashlib.sha256(settings.operator_token.encode()).digest()):
            return Principal(None, True)
        for device_id, expected in settings.device_tokens.items():
            if secrets.compare_digest(token_hash, hashlib.sha256(expected.encode()).digest()):
                return Principal(device_id, False)
        raise HTTPException(401, "Invalid token")

    def operator(principal: Principal = Depends(auth)) -> Principal:
        if not principal.operator:
            raise HTTPException(403, "Operator authorization required")
        return principal

    def idem(idempotency_key: str = Header(alias="Idempotency-Key")) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
            raise HTTPException(422, "Idempotency-Key must have 8..128 safe ASCII characters")
        return idempotency_key

    def session_for(sid: str, principal: Principal, active=True):
        return store.get_session(sid, principal.device_id, active)

    def public_action(row):
        if row is None:
            return None
        return {"action_id": row["id"], "intent_id": row["intent_id"], "based_on_frame": row["frame_id"],
                "command": json.loads(row["command"]), "reason": row["reason"], "state": row["state"],
                "requires_confirmation": bool(row["confirmation"])}

    async def make_plan(sid: str, intent_id: str):
        async with planning_slots:
            with store.lock:
                intent = store.db.execute("SELECT * FROM intents WHERE id=? AND session_id=?", (intent_id, sid)).fetchone()
                if not intent or intent["status"] != "planning":
                    return
                try:
                    session = store.get_session(sid)
                    frame = store.get_frame(sid, intent["frame_id"])
                    png = (store.frames_dir / (frame["digest"] + ".png")).read_bytes()
                except (HTTPException, OSError) as exc:
                    with store.transaction():
                        store.db.execute("UPDATE intents SET status='failed',detail='observation_unavailable' WHERE id=?", (intent_id,))
                        store.event("planning_failed", sid, {"intent_id":intent_id,"error_type":type(exc).__name__})
                    store.export_events()
                    return
                history = [{"command": json.loads(r["command"]), "state": r["state"], "result": json.loads(r["result"]) if r["result"] else None}
                           for r in store.db.execute("SELECT * FROM actions WHERE intent_id=? ORDER BY created DESC LIMIT 8", (intent_id,)).fetchall()][::-1]
            try:
                plan = await upstream.plan(goal=intent["goal"], png=png, width=frame["width"], height=frame["height"],
                                           history=history, simulated=bool(session["simulated"]), step=intent["steps"])
                with store.transaction():
                    current = store.get_session(sid)
                    current_intent = store.db.execute("SELECT * FROM intents WHERE id=?", (intent_id,)).fetchone()
                    if current["active_intent"] != intent_id or current_intent["status"] != "planning":
                        return
                    if current["latest_frame"] != intent["frame_id"]:
                        store.db.execute("UPDATE intents SET status='awaiting_frame',detail='frame_changed_while_planning' WHERE id=?", (intent_id,))
                        store.event("plan_stale", sid, {"intent_id": intent_id, "based_on_frame": intent["frame_id"]})
                    elif plan.status != "action":
                        store.db.execute("UPDATE intents SET status=?,detail=? WHERE id=?", (plan.status, plan.reason, intent_id))
                        store.event("intent_" + plan.status, sid, {"intent_id": intent_id})
                    else:
                        command = plan.action.model_dump()
                        for xname, yname in [("x", "y"), ("x2", "y2")]:
                            if xname in command and (command[xname] >= frame["width"] or command[yname] >= frame["height"]):
                                raise HTTPException(502, "Planner coordinates outside the current screenshot")
                        if store.active_action(sid):
                            raise HTTPException(409, "An action is already active")
                        confirmation = plan.requires_confirmation or (settings.require_action_confirmation and command["type"] not in {"screenshot", "wait"})
                        aid = identifier()
                        state = "awaiting_confirmation" if confirmation else "queued"
                        store.db.execute("INSERT INTO actions(id,session_id,intent_id,frame_id,command,reason,confirmation,state,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                         (aid, sid, intent_id, intent["frame_id"], canonical(command), plan.reason, int(confirmation), state, time.time(), time.time()+settings.action_ttl_seconds))
                        store.db.execute("UPDATE intents SET status='awaiting_action',steps=steps+1 WHERE id=?", (intent_id,))
                        store.event("action_proposed", sid, {"action_id": aid, "intent_id": intent_id, "based_on_frame": intent["frame_id"],
                                                            "operation_uri": "proc://clikvm/android/" + command["type"] + "/v1", "requires_confirmation": bool(confirmation)})
            except Exception as exc:
                # Persist a bounded diagnostic; never leak provider bodies/credentials.
                detail = str(exc.detail) if isinstance(exc, HTTPException) else type(exc).__name__
                with store.transaction():
                    store.db.execute("UPDATE intents SET status='failed',detail=? WHERE id=? AND status='planning'", (detail[:300], intent_id))
                    store.event("planning_failed", sid, {"intent_id": intent_id, "error_type": type(exc).__name__})
            finally:
                store.export_events()

    def schedule_observation(sid: str, frame_id: int) -> str | None:
        session = store.get_session(sid)
        if not session["active_intent"]:
            return None
        intent = store.db.execute("SELECT * FROM intents WHERE id=?", (session["active_intent"],)).fetchone()
        if not intent or intent["status"] != "awaiting_frame":
            return None
        if intent["steps"] >= settings.max_actions_per_intent:
            store.db.execute("UPDATE intents SET status='blocked',detail='action_budget_exhausted' WHERE id=?", (intent["id"],))
            store.event("intent_budget_exhausted", sid, {"intent_id": intent["id"]})
            return None
        store.db.execute("UPDATE intents SET status='planning',frame_id=? WHERE id=?", (frame_id, intent["id"]))
        store.event("observation_ready", sid, {"intent_id": intent["id"], "frame_id": frame_id})
        return intent["id"]

    @app.get("/health")
    def health():
        return {"ok": True, "service": "clikvm-gateway", "version": "0.1.0",
                "event_export": "deferred" if store.export_error else "ok"}

    @app.post("/v1/devices/{device_id}/sessions",response_model=SessionCreated)
    def start_session(device_id: str, body: SessionStart, principal=Depends(auth), key=Depends(idem)):
        if principal.operator or principal.device_id != device_id:
            raise HTTPException(403, "Use the matching device credential to start a session")
        if body.simulated and not settings.allow_simulation:
            raise HTTPException(403, "Simulation is disabled")
        if "adb" not in body.capabilities:
            raise HTTPException(422, "Start the server session after ADB is ready")
        def apply():
            old = store.db.execute("SELECT id FROM sessions WHERE device_id=? AND status='active'", (device_id,)).fetchall()
            for row in old:
                store.db.execute("UPDATE sessions SET status='superseded' WHERE id=?", (row["id"],))
                store.db.execute("UPDATE frames SET retained=0 WHERE session_id=?", (row["id"],))
                store.db.execute("UPDATE actions SET state=CASE WHEN state='leased' THEN 'uncertain' ELSE 'cancelled' END WHERE session_id=? AND state IN ('queued','leased','awaiting_confirmation')", (row["id"],))
                store.event("session_superseded", row["id"], {})
            sid = identifier()
            store.db.execute("INSERT INTO sessions(id,device_id,boot_id,simulated,capabilities,status,created,expires) VALUES(?,?,?,?,?,'active',?,?)",
                             (sid, device_id, body.boot_id, int(body.simulated), canonical(body.capabilities), time.time(), time.time()+settings.session_ttl_seconds))
            store.event("session_started", sid, {"device_id": device_id, "simulated": body.simulated, "capabilities": body.capabilities})
            return {"session_id": sid, "latest_frame": 0, "expires_in_seconds": settings.session_ttl_seconds, "simulated": body.simulated}
        result=store.mutation(principal.actor, key, "session_start:"+device_id, body.model_dump(), apply)[0]
        store.prune_frames()
        return result

    @app.get("/v1/sessions")
    def list_sessions(principal=Depends(operator)):
        with store.lock:
            return {"sessions": [dict(r) for r in store.db.execute("SELECT id AS session_id,device_id,simulated,status,latest_frame,active_intent,created,expires FROM sessions ORDER BY created DESC LIMIT 100")]}

    @app.get("/v1/sessions/{sid}/status")
    def status(sid: str, principal=Depends(auth)):
        with store.lock:
            session = session_for(sid, principal, active=False)
            intent = store.db.execute("SELECT id,status,goal,steps,frame_id,detail FROM intents WHERE id=?", (session["active_intent"],)).fetchone()
            action = store.active_action(sid)
            result = {"session_id": sid, "device_id": session["device_id"], "status": session["status"], "simulated": bool(session["simulated"]),
                      "latest_frame": session["latest_frame"], "capture_requested": bool(session["capture_requested"]),
                      "intent": dict(intent) if intent else None, "action": public_action(action)}
            result["state"] = intent["status"] if intent else "idle"
            result["message"] = (intent["detail"] if intent else "") or (action["reason"] if action else "") or "Gotowy na polecenie"
        return result

    @app.post("/v1/sessions/{sid}/frames",response_model=FrameAccepted,
              openapi_extra={"requestBody":{"required":True,"content":{"image/png":{"schema":{"type":"string","format":"binary"}}}}})
    async def upload_frame(sid: str, request: Request, background: BackgroundTasks,
                           x_frame_id: int = Header(alias="X-Frame-Id"),
                           x_width: int | None = Header(default=None, alias="X-Width"),
                           x_height: int | None = Header(default=None, alias="X-Height"),
                           principal=Depends(auth), key=Depends(idem)):
        with store.lock:
            session_for(sid, principal)
        if not 1 <= x_frame_id <= 2**31-1:
            raise HTTPException(422, "Frame ID must be 1..2147483647")
        if request.headers.get("content-type", "").split(";")[0] != "image/png":
            raise HTTPException(415, "Content-Type must be image/png")
        data = bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk) > settings.max_frame_bytes:
                raise HTTPException(413, "Screenshot exceeds configured maximum")
            data.extend(chunk)
        width, height = png_dimensions(data)
        if (x_width is not None and x_width != width) or (x_height is not None and x_height != height):
            raise HTTPException(422, "Declared dimensions differ from PNG IHDR")
        digest = hashlib.sha256(data).hexdigest()
        def apply():
            session = session_for(sid, principal)
            if x_frame_id <= session["latest_frame"]:
                raise HTTPException(409, "Frame ID must increase monotonically", headers={"X-Clik-Error-Code":"frame_conflict"})
            store.save_frame_file(digest, data)
            store.db.execute("INSERT INTO frames(session_id,frame_id,digest,width,height,size,created) VALUES(?,?,?,?,?,?,?)",
                             (sid,x_frame_id,digest,width,height,len(data),time.time()))
            store.db.execute("UPDATE sessions SET latest_frame=?,capture_requested=0 WHERE id=?", (x_frame_id,sid))
            stale = store.db.execute("SELECT id,intent_id FROM actions WHERE session_id=? AND state IN ('queued','awaiting_confirmation') AND frame_id<>?", (sid,x_frame_id)).fetchall()
            for action in stale:
                store.db.execute("UPDATE actions SET state='stale' WHERE id=?", (action["id"],))
                store.db.execute("UPDATE intents SET status='awaiting_frame' WHERE id=?", (action["intent_id"],))
                store.event("action_stale", sid, {"action_id": action["id"]})
            store.event("frame_received", sid, {"frame_id":x_frame_id,"width":width,"height":height,"bytes":len(data),"resource_urn":"urn:clikvm:frame:sha256:"+digest})
            next_intent = schedule_observation(sid,x_frame_id)
            return {"frame_id":x_frame_id,"width":width,"height":height,"resource_urn":"urn:clikvm:frame:sha256:"+digest,"planning_intent":next_intent}
        result, cached = store.mutation(principal.actor,key,"frame:"+sid,{"frame_id":x_frame_id,"digest":digest,"width":x_width,"height":x_height},apply)
        if result["planning_intent"] and not cached:
            background.add_task(make_plan,sid,result["planning_intent"])
        store.prune_frames()
        return result

    @app.get("/v1/sessions/{sid}/frames/{frame_id}",responses={200:{"content":{"image/png":{"schema":{"type":"string","format":"binary"}}}}})
    def frame(sid: str, frame_id: int, principal=Depends(auth)):
        with store.lock:
            session_for(sid,principal,active=False)
            item=store.get_frame(sid,frame_id)
            return FileResponse(store.frames_dir/(item["digest"]+".png"), media_type="image/png")

    @app.post("/v1/sessions/{sid}/intent",response_model=IntentCreated)
    def submit_intent(sid: str,body: IntentRequest,background: BackgroundTasks,principal=Depends(auth),key=Depends(idem)):
        def apply():
            session=session_for(sid,principal)
            store.expire_actions(sid)
            if store.active_action(sid):
                raise HTTPException(409,"Finish or reconcile the existing action first")
            if session["active_intent"]:
                previous=store.db.execute("SELECT status FROM intents WHERE id=?",(session["active_intent"],)).fetchone()
                if previous and previous["status"] in {"planning","awaiting_action","awaiting_frame"}:
                    raise HTTPException(409,"An intent is already running")
            if body.frame_id != session["latest_frame"]:
                raise HTTPException(409,"Intent must use the latest frame",headers={"X-Clik-Error-Code":"frame_conflict"})
            observation=store.get_frame(sid,body.frame_id)
            refresh=time.time()-observation["created"] > settings.max_observation_age_seconds
            iid=identifier()
            store.db.execute("INSERT INTO intents(id,session_id,goal,status,frame_id,created) VALUES(?,?,?,?,?,?)",(iid,sid,body.text,"awaiting_frame" if refresh else "planning",body.frame_id,time.time()))
            store.db.execute("UPDATE sessions SET active_intent=?,capture_requested=? WHERE id=?",(iid,int(refresh),sid))
            store.event("intent_started",sid,{"intent_id":iid,"frame_id":body.frame_id,"observation_refresh":refresh,"goal_sha256":hashlib.sha256(body.text.encode()).hexdigest()})
            return {"intent_id":iid,"status":"planning"}
        result,cached=store.mutation(principal.actor,key,"intent:"+sid,body.model_dump(),apply)
        if not cached:
            background.add_task(make_plan,sid,result["intent_id"])
        return result

    @app.post("/v1/sessions/{sid}/actions/next",response_model=NextActionResponse)
    def next_action(sid: str,principal=Depends(auth),key=Depends(idem)):
        def apply():
            session=session_for(sid,principal)
            store.expire_actions(sid)
            action=store.active_action(sid)
            intent=store.db.execute("SELECT status FROM intents WHERE id=?",(session["active_intent"],)).fetchone()
            result={"session_id":sid,"state":intent["status"] if intent else "idle","latest_frame":session["latest_frame"],
                    "capture_requested":bool(session["capture_requested"]),"action":None}
            if session["capture_requested"] and (not action or action["state"]!="leased"):
                result["state"]="capture_requested"
                return result
            if not action:
                return result
            if action["state"]!="queued":
                result["state"]={"leased":"awaiting_result","uncertain":"uncertain","awaiting_confirmation":"awaiting_confirmation"}[action["state"]]
                return result
            if action["frame_id"]!=session["latest_frame"]:
                raise HTTPException(409,"Action frame is stale",headers={"X-Clik-Error-Code":"frame_conflict"})
            lease=identifier()
            lease_end=min(time.time()+settings.lease_seconds,action["expires"])
            store.db.execute("UPDATE actions SET state='leased',lease_id=?,lease_expires=? WHERE id=?",(lease,lease_end,action["id"]))
            store.event("action_leased",sid,{"action_id":action["id"],"based_on_frame":action["frame_id"]})
            command=json.loads(action["command"])
            result.update(state="ready",action={"action_id":action["id"],"lease_id":lease,"based_on_frame":action["frame_id"],
                                                 "valid_for_ms":max(0,int((lease_end-time.time())*1000)),"command":command,
                                                 "operation_uri":"proc://clikvm/android/"+command["type"]+"/v1","after_action":"screenshot"})
            return result
        response,cached=store.mutation(principal.actor,key,"next:"+sid,{},apply)
        if cached and response.get("action"):
            # Exact HTTP retries do not renew leases or revive completed commands.
            with store.lock:
                current=session_for(sid,principal)
                action=store.db.execute("SELECT * FROM actions WHERE id=? AND session_id=?",(response["action"]["action_id"],sid)).fetchone()
                if not action or action["state"]!="leased" or action["lease_expires"]<=time.time():
                    raise HTTPException(409,"Cached lease is no longer executable",headers={"X-Clik-Error-Code":"lease_conflict"})
                if action["frame_id"]!=current["latest_frame"]:
                    raise HTTPException(409,"Cached lease refers to a stale frame",headers={"X-Clik-Error-Code":"frame_conflict"})
                response["action"]["valid_for_ms"]=max(0,int((action["lease_expires"]-time.time())*1000))
        return response

    @app.post("/v1/sessions/{sid}/capture")
    def request_capture(sid: str,principal=Depends(operator),key=Depends(idem)):
        def apply():
            session_for(sid,principal)
            store.db.execute("UPDATE sessions SET capture_requested=1 WHERE id=?",(sid,))
            store.event("capture_requested",sid,{})
            return {"capture_requested":True}
        return store.mutation(principal.actor,key,"capture:"+sid,{},apply)[0]

    @app.post("/v1/sessions/{sid}/actions/{aid}/result",response_model=ResultAccepted)
    def action_result(sid: str,aid: str,body: ActionResult,principal=Depends(auth),key=Depends(idem)):
        def apply():
            session_for(sid,principal)
            store.expire_actions(sid)
            action=store.db.execute("SELECT * FROM actions WHERE id=? AND session_id=?",(aid,sid)).fetchone()
            if not action:
                raise HTTPException(404,"Action not found")
            if action["state"]!="leased" or not secrets.compare_digest(action["lease_id"] or "",body.lease_id):
                raise HTTPException(409,"Action lease is invalid or expired; reconcile instead of replaying",headers={"X-Clik-Error-Code":"lease_conflict"})
            store.db.execute("UPDATE actions SET state=?,result=? WHERE id=?",(body.status,canonical(body.model_dump()),aid))
            state="awaiting_frame" if body.status=="succeeded" else "blocked"
            store.db.execute("UPDATE intents SET status=?,detail=? WHERE id=?",(state,"" if body.status=="succeeded" else "action_"+body.status,action["intent_id"]))
            store.event("action_"+body.status,sid,{"action_id":aid,"intent_id":action["intent_id"]})
            return {"action_id":aid,"status":body.status,"next":"upload_frame" if body.status=="succeeded" else "review"}
        return store.mutation(principal.actor,key,"result:"+sid+":"+aid,body.model_dump(),apply)[0]

    @app.post("/v1/sessions/{sid}/actions/{aid}/confirm")
    def confirm(sid: str,aid: str,body: Confirmation,principal=Depends(operator),key=Depends(idem)):
        def apply():
            session=session_for(sid,principal)
            store.expire_actions(sid)
            action=store.db.execute("SELECT * FROM actions WHERE id=? AND session_id=?",(aid,sid)).fetchone()
            if not action or action["state"]!="awaiting_confirmation" or action["frame_id"]!=session["latest_frame"]:
                raise HTTPException(409,"Action is no longer awaiting confirmation on the current frame")
            state="queued" if body.approve else "cancelled"
            store.db.execute("UPDATE actions SET state=? WHERE id=?",(state,aid))
            if not body.approve:
                store.db.execute("UPDATE intents SET status='blocked',detail='operator_declined' WHERE id=?",(action["intent_id"],))
            store.event("action_confirmed" if body.approve else "action_declined",sid,{"action_id":aid})
            return {"action_id":aid,"state":state}
        return store.mutation(principal.actor,key,"confirm:"+sid+":"+aid,body.model_dump(),apply)[0]

    @app.post("/v1/sessions/{sid}/actions/{aid}/resolve")
    def resolve(sid: str,aid: str,body: Resolution,principal=Depends(operator),key=Depends(idem)):
        def apply():
            session=session_for(sid,principal)
            action=store.db.execute("SELECT * FROM actions WHERE id=? AND session_id=?",(aid,sid)).fetchone()
            if not action or action["state"]!="uncertain":
                raise HTTPException(409,"Only uncertain actions can be reconciled")
            if body.frame_id!=session["latest_frame"] or body.frame_id<=action["frame_id"]:
                raise HTTPException(409,"Reconciliation requires a new screenshot")
            store.db.execute("UPDATE actions SET state='resolved',result=? WHERE id=?",(canonical(body.model_dump()),aid))
            store.db.execute("UPDATE intents SET status='blocked',detail='reconciled_start_new_intent' WHERE id=?",(action["intent_id"],))
            store.event("action_resolved",sid,{"action_id":aid,"frame_id":body.frame_id,"resolution":body.resolution})
            return {"action_id":aid,"state":"resolved","next":"new_intent"}
        return store.mutation(principal.actor,key,"resolve:"+sid+":"+aid,body.model_dump(),apply)[0]

    @app.post("/v1/sessions/{sid}/cancel")
    def cancel(sid: str,principal=Depends(operator),key=Depends(idem)):
        def apply():
            session=session_for(sid,principal)
            store.db.execute("UPDATE actions SET state=CASE WHEN state='leased' THEN 'uncertain' ELSE 'cancelled' END WHERE session_id=? AND state IN ('queued','leased','awaiting_confirmation')",(sid,))
            store.db.execute("UPDATE intents SET status='cancelled' WHERE id=?",(session["active_intent"],))
            store.event("intent_cancelled",sid,{"intent_id":session["active_intent"]})
            return {"status":"cancelled"}
        return store.mutation(principal.actor,key,"cancel:"+sid,{},apply)[0]

    @app.post("/v1/sessions/{sid}/audio/stt",openapi_extra={"requestBody":{"required":True,"content":{"audio/wav":{"schema":{"type":"string","format":"binary"}}}}})
    async def stt(sid: str,request: Request,principal=Depends(auth),key=Depends(idem)):
        async with audio_serial:
            return await perform_stt(sid,request,principal,key)

    async def perform_stt(sid,request,principal,key):
        with store.lock:
            session_for(sid,principal)
        if request.headers.get("content-type","").split(";")[0] not in {"audio/wav","audio/x-wav"}:
            raise HTTPException(415,"Expected audio/wav")
        data=bytearray()
        async for chunk in request.stream():
            if len(data)+len(chunk)>settings.max_audio_bytes:
                raise HTTPException(413,"Audio upload exceeds limit")
            data.extend(chunk)
        digest=hashlib.sha256(data).hexdigest()
        with store.lock:
            existing=store.db.execute("SELECT request_hash,response,route FROM idempotency WHERE actor=? AND key=?",(principal.actor,key)).fetchone()
            if store.db.execute("SELECT 1 FROM audio_cache WHERE actor=? AND key=?",(principal.actor,key)).fetchone():
                raise HTTPException(409,"Idempotency-Key conflict",headers={"X-Clik-Error-Code":"idempotency_conflict"})
        req={"sha256":digest}
        if existing:
            if existing["request_hash"]!=hashlib.sha256(canonical(req).encode()).hexdigest() or existing["route"]!="stt:"+sid:
                raise HTTPException(409,"Idempotency-Key conflict")
            return json.loads(existing["response"])
        text=await upstream.transcribe(bytes(data))
        def apply():
            session_for(sid,principal)
            store.event("speech_transcribed",sid,{"audio_bytes":len(data),"text_characters":len(text)})
            return {"text":text}
        return store.mutation(principal.actor,key,"stt:"+sid,req,apply)[0]

    @app.post("/v1/sessions/{sid}/audio/tts",responses={200:{"content":{"audio/wav":{"schema":{"type":"string","format":"binary"}}}}})
    async def tts(sid: str,body: SpeakRequest,principal=Depends(auth),key=Depends(idem)):
        async with audio_serial:
            return await perform_tts(sid,body,principal,key)

    async def perform_tts(sid,body,principal,key):
        request_hash=hashlib.sha256(canonical(body.model_dump()).encode()).hexdigest()
        with store.lock:
            session_for(sid,principal)
            cached=store.db.execute("SELECT * FROM audio_cache WHERE actor=? AND key=?",(principal.actor,key)).fetchone()
            other=store.db.execute("SELECT 1 FROM idempotency WHERE actor=? AND key=?",(principal.actor,key)).fetchone()
            if other:
                raise HTTPException(409,"Idempotency-Key is used by another operation")
            if cached:
                if cached["request_hash"]!=request_hash or cached["session_id"]!=sid:
                    raise HTTPException(409,"Idempotency-Key conflict")
                path=store.frames_dir/(cached["digest"]+".wav")
                if not path.exists():
                    raise HTTPException(409,"Audio retry cache expired; use a new request key")
                return FileResponse(path,media_type="audio/wav",headers={"X-Audio-Format":"pcm_s16le","X-Sample-Rate":"16000","X-Channels":"1"})
        wav=await upstream.speak(body.text)
        digest=hashlib.sha256(wav).hexdigest()
        with store.transaction():
            if store.db.execute("SELECT 1 FROM idempotency WHERE actor=? AND key=?",(principal.actor,key)).fetchone():
                raise HTTPException(409,"Idempotency-Key conflict",headers={"X-Clik-Error-Code":"idempotency_conflict"})
            # Retain a bounded retry cache. Old requests can never silently return a
            # different recording under the same key; metadata remains a tombstone.
            recent={r[0] for r in store.db.execute("SELECT digest FROM audio_cache ORDER BY created DESC LIMIT 15")}
            for path in store.frames_dir.glob("*.wav"):
                if path.stem not in recent:
                    path.unlink(missing_ok=True)
            used=sum(p.stat().st_size for p in store.frames_dir.iterdir() if p.suffix in {".png",".wav"})
            path=store.frames_dir/(digest+".wav")
            if used+(0 if path.exists() else len(wav))>settings.max_storage_bytes:
                raise HTTPException(507,"Media storage quota exhausted")
            if not path.exists():
                temporary=store.frames_dir/(".audio-"+identifier())
                temporary.write_bytes(wav)
                temporary.chmod(0o600)
                temporary.replace(path)
            store.db.execute("INSERT OR IGNORE INTO audio_cache VALUES(?,?,?,?,?,?)",(principal.actor,key,request_hash,sid,digest,time.time()))
            store.event("speech_synthesized",sid,{"text_characters":len(body.text),"audio_bytes":len(wav)})
        store.export_events()
        return Response(wav,media_type="audio/wav",headers={"X-Audio-Format":"pcm_s16le","X-Sample-Rate":"16000","X-Channels":"1"})

    @app.post("/v1/oqllm/route")
    async def route(body: RouteRequest,principal=Depends(operator)):
        # Routing only: never dispatch opaque procedures to production automatically.
        return await upstream.route_poa(body.query)

    static=Path(__file__).parent/"static"
    app.mount("/static",StaticFiles(directory=static,check_dir=False),name="static")
    @app.get("/",include_in_schema=False)
    def dashboard():
        return FileResponse(static/"index.html")

    return app
