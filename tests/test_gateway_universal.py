from __future__ import annotations

import dataclasses
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from clikvm.app import create_app
from clikvm.cli import demo_png
from clikvm.config import Settings
from clikvm.store import Store


KEYS = ["HOME","BACK","ENTER","TAB","ESCAPE","DPAD_UP","DPAD_DOWN","DPAD_LEFT","DPAD_RIGHT","DEL","APP_SWITCH"]


def desktop_profile(**kwargs):
    return {"platform":"linux","os_version":"test-contract","transport":"native-agent",
            "observation_kind":"screenshot","action_types":["tap","swipe","key","text","open_url","screenshot","wait"],
            "keys":KEYS,"api_operations":[],**kwargs}


def api_profile(**kwargs):
    return {"platform":"network","transport":"http-api","observation_kind":"json-state",
            "action_types":["api","wait"],"keys":[],"api_operations":[{
                "id":"set_temperature","description":"Set the lab thermostat target", "requires_confirmation":True,
                "parameters_schema":{"type":"object","properties":{"temperature":{"type":"number","minimum":10,"maximum":30}},
                                     "required":["temperature"],"additionalProperties":False}}],**kwargs}


class UniversalGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]; self.plans=[]
        self.settings=Settings(state_dir=Path(self.temp.name),device_tokens={"pc":"p"*32,"other":"d"*32},
                               operator_token="o"*32,controller_tokens={"m5":"c"*32},controller_targets={"m5":"pc"},
                               model_endpoint="https://model.invalid/chat",model_name="test-vision",
                               stt_endpoint="https://audio.invalid/stt",tts_endpoint="https://audio.invalid/tts",
                               tts_model="test-tts",tts_voice="test-voice")
        async def transport(request):
            self.calls.append(request)
            if request.url.path=="/stt": return httpx.Response(200,json={"text":"Sprawdź temperaturę"})
            if request.url.path=="/tts": return httpx.Response(200,content=self.wav(),headers={"content-type":"audio/wav"})
            plan=self.plans.pop(0) if self.plans else {"status":"done","reason":"Observed test state","requires_confirmation":False,"action":None}
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(plan)}}]})
        self.provider=httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.app=create_app(self.settings,self.provider)
        self.client=TestClient(self.app);self.client.__enter__();self.closed=False

    def tearDown(self):
        if not self.closed: self.client.__exit__(None,None,None)
        import asyncio
        asyncio.run(self.provider.aclose())
        self.temp.cleanup()

    @staticmethod
    def wav():
        out=io.BytesIO()
        with wave.open(out,"wb") as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b"\0\0"*160)
        return out.getvalue()

    def headers(self,kind="device",key=None,**extra):
        token={"device":"p"*32,"controller":"c"*32,"operator":"o"*32,"other":"d"*32}[kind]
        return {"Authorization":"Bearer "+token,"Idempotency-Key":key or str(uuid.uuid4()),**extra}

    def start(self,profile=None,capabilities=None):
        body={"boot_id":str(uuid.uuid4()),"capabilities":capabilities or ["screenshot","keyboard","mouse","open_url","native_agent"]}
        if profile is not None:body["profile"]=profile
        response=self.client.post("/v1/devices/pc/sessions",headers=self.headers(),json=body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()["session_id"]

    def frame(self,sid,n=1):
        return self.client.post(f"/v1/sessions/{sid}/frames",headers=self.headers(**{"Content-Type":"image/png","X-Frame-Id":str(n)}),content=demo_png(48,80))

    def state(self,sid,n=1,state=None):
        return self.client.post(f"/v1/sessions/{sid}/observations",headers=self.headers(),json={"frame_id":n,"state":state or {"temperature":20.5}})

    def status(self,sid):
        return self.client.get(f"/v1/sessions/{sid}/status",headers=self.headers()).json()

    def next(self,sid):
        return self.client.post(f"/v1/sessions/{sid}/actions/next",headers=self.headers(),json={})

    def goal(self,sid):
        return self.client.post(f"/v1/sessions/{sid}/intent",headers=self.headers(),json={"text":"Complete the scoped task","frame_id":1})

    def test_desktop_without_adb_has_real_profile_and_platform_prompt(self):
        sid=self.start(desktop_profile(platform="windows"));self.assertEqual(self.frame(sid).status_code,200)
        self.plans=[{"status":"action","action":{"type":"key","key":"HOME"},"reason":"Move to line start"}]
        self.assertEqual(self.goal(sid).status_code,200)
        action=self.next(sid).json()["action"]
        self.assertEqual(action["operation_uri"],"proc://clikvm/device/key/v1")
        self.assertEqual(self.status(sid)["platform"],"windows")
        payload=json.loads(self.calls[-1].content)
        context=json.loads(payload["messages"][1]["content"][0]["text"])
        self.assertEqual(context["target"]["platform"],"windows")
        self.assertIn("cursor-home",payload["messages"][0]["content"])
        self.assertEqual(payload["messages"][1]["content"][1]["type"],"image_url")

    def test_unavailable_rfb_capability_is_not_queued(self):
        profile=desktop_profile(transport="rfb",action_types=["tap","key","screenshot","wait"])
        sid=self.start(profile);self.frame(sid)
        self.plans=[{"status":"action","action":{"type":"open_url","url":"https://example.com"},"reason":"Try unavailable browser launch"}]
        self.goal(sid)
        self.assertEqual(self.status(sid)["state"],"failed")
        self.assertIsNone(self.next(sid).json()["action"])

    def test_json_state_loop_uses_allowed_api_schema_and_confirmation(self):
        sid=self.start(api_profile(),["api_state","api_operation"])
        self.assertEqual(self.state(sid).status_code,200)
        self.plans=[{"status":"action","action":{"type":"api","operation":"set_temperature","arguments":{"temperature":21.5}},"reason":"Set requested temperature","requires_confirmation":False}]
        self.goal(sid)
        current=self.status(sid)
        self.assertEqual(current["action"]["state"],"awaiting_confirmation")
        aid=current["action"]["action_id"]
        denied=self.client.post(f"/v1/sessions/{sid}/actions/{aid}/confirm",headers=self.headers("controller"),json={"approve":True})
        self.assertEqual(denied.status_code,401)
        self.assertEqual(self.client.post(f"/v1/sessions/{sid}/actions/{aid}/confirm",headers=self.headers("operator"),json={"approve":True}).status_code,200)
        action=self.next(sid).json()["action"]
        self.assertEqual(action["after_action"],"observation")
        self.assertEqual(action["command"]["arguments"],{"temperature":21.5})
        result=self.client.post(f"/v1/sessions/{sid}/actions/{aid}/result",headers=self.headers(),json={"lease_id":action["lease_id"],"status":"succeeded","detail":"HTTP result; awaiting actual state"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.state(sid,2,{"temperature":21.5}).status_code,200)
        self.assertEqual(self.status(sid)["state"],"done")
        observed=self.client.get(f"/v1/sessions/{sid}/observations/2",headers=self.headers()).json()
        self.assertEqual(observed["state"],{"temperature":21.5})
        self.assertTrue(observed["resource_urn"].startswith("urn:clikvm:state:sha256:"))
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/frames/2",headers=self.headers()).status_code,415)
        payload=json.loads(self.calls[0].content)
        self.assertEqual(len(payload["messages"][1]["content"]),1)
        context=json.loads(payload["messages"][1]["content"][0]["text"])
        self.assertEqual(context["device_state"]["temperature"],20.5)
        self.assertNotIn("http",json.dumps(context["target"]["api_operations"]))

    def test_json_api_unknown_operation_or_invalid_arguments_never_lease(self):
        for command in [{"type":"api","operation":"delete_all","arguments":{}},
                        {"type":"api","operation":"set_temperature","arguments":{"temperature":200}},
                        {"type":"api","operation":"set_temperature","arguments":{"temperature":20,"url":"http://127.0.0.1"}}]:
            sid=self.start(api_profile(),["api_state","api_operation"]);self.state(sid)
            self.plans=[{"status":"action","action":command}];self.goal(sid)
            self.assertEqual(self.status(sid)["state"],"failed")
            self.assertIsNone(self.next(sid).json()["action"])

    def test_km_only_never_claims_an_observation_or_autonomy(self):
        sid=self.start(desktop_profile(transport="hid-bridge",observation_kind="none",action_types=["key","text","wait"]),["keyboard"])
        self.assertEqual(self.frame(sid).status_code,415)
        self.assertEqual(self.state(sid).status_code,415)
        response=self.goal(sid)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.headers["X-Clik-Error-Code"],"observation_required")
        capture=self.client.post(f"/v1/sessions/{sid}/capture",headers=self.headers("operator"),json={})
        self.assertEqual(capture.status_code,409)
        self.assertEqual(capture.headers["X-Clik-Error-Code"],"observation_required")
        target=self.client.get("/v1/controllers/m5/target",headers=self.headers("controller")).json()
        self.assertFalse(target["ready"]);self.assertEqual(target["state"],"observation_required")

    def test_controller_is_scoped_and_cannot_lease_or_forge_observation(self):
        sid=self.start(desktop_profile());self.frame(sid)
        for method,path,body in [("get","/v1/sessions",None),("get",f"/v1/sessions/{sid}/status",None),
                                  ("post",f"/v1/sessions/{sid}/actions/next",{}),
                                  ("post",f"/v1/sessions/{sid}/observations",{"frame_id":2,"state":{}})]:
            kwargs={"headers":self.headers("controller")}
            if body is not None:kwargs["json"]=body
            self.assertEqual(getattr(self.client,method)(path,**kwargs).status_code,401)
        self.assertEqual(self.client.get("/v1/controllers/other/target",headers=self.headers("controller")).status_code,401)
        target=self.client.get("/v1/controllers/m5/target",headers=self.headers("controller")).json()
        self.assertTrue(target["ready"]);self.assertEqual(target["target_device_id"],"pc")
        self.assertEqual(target["session_id"],sid)

    def test_controller_goal_forces_fresh_observation_and_retry_is_exact(self):
        sid=self.start(desktop_profile());self.frame(sid)
        header=self.headers("controller",key="voice-intent-0001")
        body={"text":"Open a known page","expected_session_id":sid}
        first=self.client.post("/v1/controllers/m5/intent",headers=header,json=body)
        self.assertEqual(first.status_code,200,first.text)
        message=self.next(sid).json();self.assertTrue(message["capture_requested"]);self.assertIsNone(message["action"])
        self.plans=[{"status":"action","action":{"type":"key","key":"ENTER"}}]
        self.frame(sid,2)
        repeated=self.client.post("/v1/controllers/m5/intent",headers=header,json=body)
        self.assertEqual(first.json(),repeated.json())
        action=self.next(sid).json()["action"];self.assertEqual(action["based_on_frame"],2)
        cancelled=self.client.post("/v1/controllers/m5/cancel",headers=self.headers("controller"),json={"expected_session_id":sid})
        self.assertEqual(cancelled.status_code,200)
        self.assertEqual(self.status(sid)["action"]["state"],"uncertain")
        self.assertIsNone(self.next(sid).json()["action"])
        rows=self.app.state.store.db.execute("SELECT actor FROM idempotency WHERE route LIKE 'intent:%'").fetchall()
        self.assertEqual(rows[0]["actor"],"controller:m5")

    def test_controller_rejects_target_restart_during_voice(self):
        old=self.start(desktop_profile());self.frame(old)
        new=self.start(desktop_profile());self.frame(new)
        for route,body in [("intent",{"text":"An old voice task","expected_session_id":old}),
                           ("capture",{"expected_session_id":old}),("cancel",{"expected_session_id":old})]:
            r=self.client.post("/v1/controllers/m5/"+route,headers=self.headers("controller"),json=body)
            self.assertEqual(r.status_code,409,r.text);self.assertEqual(r.headers["X-Clik-Error-Code"],"target_changed")
        self.assertEqual(self.status(new)["state"],"idle")

    def test_controller_online_status_tracks_connector_contact(self):
        sid=self.start(desktop_profile());self.frame(sid)
        self.app.state.last_seen[sid]=time.monotonic()-60
        target=self.client.get("/v1/controllers/m5/target",headers=self.headers("controller")).json()
        self.assertFalse(target["ready"]);self.assertEqual(target["state"],"offline")
        denied=self.client.post("/v1/controllers/m5/intent",headers=self.headers("controller"),json={"text":"Do not queue on offline executor"})
        self.assertEqual(denied.headers["X-Clik-Error-Code"],"target_unavailable")
        self.next(sid)
        self.assertTrue(self.client.get("/v1/controllers/m5/target",headers=self.headers("controller")).json()["ready"])

    def test_controller_audio_uses_own_scope_and_exact_cache(self):
        sid=self.start(desktop_profile());self.frame(sid)
        headers=self.headers("controller",key="controller-stt-0001",**{"Content-Type":"audio/wav"})
        r=self.client.post("/v1/controllers/m5/audio/stt",headers=headers,content=self.wav())
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()["text"],"Sprawdź temperaturę")
        self.assertEqual(self.client.post("/v1/controllers/m5/audio/stt",headers=headers,content=self.wav()).json(),r.json())
        self.assertEqual(len([r for r in self.calls if r.url.path=="/stt"]),1)
        tts=self.client.post("/v1/controllers/m5/audio/tts",headers=self.headers("controller"),json={"text":"Zadanie zakończone"})
        self.assertEqual(tts.status_code,200,tts.text[:100]);self.assertEqual(tts.content[:4],b"RIFF")

    def test_profile_rejects_external_schema_refs_and_false_capabilities(self):
        profile=api_profile();profile["api_operations"][0]["parameters_schema"]["$ref"]="http://127.0.0.1/private"
        r=self.client.post("/v1/devices/pc/sessions",headers=self.headers(),json={"boot_id":"schema-test","capabilities":["api_state"],"profile":profile})
        self.assertEqual(r.status_code,422)
        r=self.client.post("/v1/devices/pc/sessions",headers=self.headers(),json={"boot_id":"missing-probe","capabilities":["keyboard"],"profile":desktop_profile()})
        self.assertEqual(r.status_code,422)
        profile=api_profile();profile["api_operations"][0]["parameters_schema"]["properties"]["temperature"]["type"]="not_a_type"
        r=self.client.post("/v1/devices/pc/sessions",headers=self.headers(),json={"boot_id":"bad-schema","capabilities":["api_state"],"profile":profile})
        self.assertEqual(r.status_code,422)

    def test_json_observations_replay_and_retention(self):
        sid=self.start(api_profile(),["api_state","api_operation"])
        for n in range(1,11):self.assertEqual(self.state(sid,n,{"temperature":20+n/10}).status_code,200)
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/observations/1",headers=self.headers()).status_code,404)
        destination=Path(self.temp.name)/"rebuilt"
        self.app.state.store.rebuild(destination)
        rebuilt=Store(dataclasses.replace(self.settings,state_dir=destination),recover=False)
        try:
            rows=rebuilt.db.execute("SELECT * FROM frames WHERE retained=1").fetchall()
            self.assertEqual(len(rows),8)
            for row in rows:self.assertTrue(rebuilt.observation_path(row).is_file())
            self.assertEqual(json.loads(rebuilt.get_session(sid)["profile"])["transport"],"http-api")
        finally:rebuilt.close()

    def test_v01_ledger_rows_upcast_during_replay(self):
        sid=self.start(None,["adb"]);self.frame(sid)
        store=self.app.state.store
        with store.transaction():
            for table,query in [("sessions","SELECT * FROM sessions WHERE id=?"),("frames","SELECT * FROM frames WHERE session_id=?")]:
                raw=dict(store.db.execute(query,(sid,)).fetchone())
                raw.pop("profile" if table=="sessions" else "kind")
                raw={k:str(v) if isinstance(v,float) else v for k,v in raw.items()}
                store.event("projection_changed",sid,{"table":table,"operation":"upsert","row":raw})
        destination=Path(self.temp.name)/"old-ledger-rebuilt"
        store.rebuild(destination)
        rebuilt=Store(dataclasses.replace(self.settings,state_dir=destination),recover=False)
        try:
            self.assertEqual(rebuilt.get_session(sid)["profile"],"{}")
            self.assertEqual(rebuilt.get_frame(sid,1)["kind"],"png")
        finally:rebuilt.close()

    def test_controller_tokens_must_be_unique_and_bind_existing_target(self):
        with self.assertRaises(ValueError):dataclasses.replace(self.settings,controller_tokens={"m5":"p"*32}).validate()
        with self.assertRaises(ValueError):dataclasses.replace(self.settings,controller_targets={"m5":"missing"}).validate()
        with self.assertRaises(ValueError):dataclasses.replace(self.settings,controller_targets={"m5":["pc"]}).validate()

    def test_init_env_provisions_separate_scoped_tokens_without_overwrite(self):
        import shlex
        dest=Path(self.temp.name)/"initial.env"
        args=[sys.executable,"-m","clikvm.cli","init-env","--output",str(dest),"--device","pc","--controller","m5"]
        result=subprocess.run(args,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        raw=dest.read_text();values={}
        for line in raw.splitlines():
            if line.startswith("#"):continue
            key,value=line.split("=",1);values[key]=shlex.split(value)[0]
        device=json.loads(values["CLIK_DEVICE_TOKENS_JSON"])["pc"]
        controller=json.loads(values["CLIK_CONTROLLER_TOKENS_JSON"])["m5"]
        self.assertEqual(json.loads(values["CLIK_CONTROLLER_TARGETS_JSON"]),{"m5":"pc"})
        self.assertEqual(len({device,controller,values["CLIK_OPERATOR_TOKEN"]}),3)
        for secret in (device,controller,values["CLIK_OPERATOR_TOKEN"]):self.assertNotIn(secret,result.stdout+result.stderr)
        self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        result=subprocess.run(args,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0);self.assertEqual(dest.read_text(),raw)

    def test_v01_database_migrates_without_losing_android_state(self):
        sid=self.start(None,["adb"]);self.frame(sid)
        self.client.__exit__(None,None,None);self.closed=True
        db=sqlite3.connect(Path(self.temp.name)/"events.sqlite3")
        # Recreate the persisted v0.1 shape, with its projection capture already installed.
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'capture_%'").fetchall():
            db.execute('DROP TRIGGER "'+row[0]+'"')
        db.execute("ALTER TABLE sessions DROP COLUMN profile")
        db.execute("ALTER TABLE frames DROP COLUMN kind");db.commit();db.close()
        migrated=Store(self.settings,recover=False)
        try:
            self.assertEqual(migrated.get_session(sid)["profile"],"{}")
            frame=migrated.get_frame(sid,1)
            self.assertEqual(frame["kind"],"png");self.assertTrue(migrated.observation_path(frame).is_file())
            with migrated.transaction():
                migrated.db.execute("UPDATE sessions SET capture_requested=1 WHERE id=?",(sid,))
            projection=json.loads(migrated.db.execute("SELECT payload FROM domain_events WHERE kind='projection_changed' ORDER BY sequence DESC LIMIT 1").fetchone()[0])
            self.assertIn("profile",projection["row"])
        finally:migrated.close()


if __name__=="__main__":unittest.main()
