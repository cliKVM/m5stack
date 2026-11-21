from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import io
import json
import tempfile
import time
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from clikvm.app import create_app
from clikvm.audit import validate_event
from clikvm.cli import demo_png
from clikvm.config import Settings
from clikvm.models import Plan, Text
from clikvm.store import Store
from clikvm.upstream import Upstreams, normalize_wav, validate_wav


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(state_dir=Path(self.temp.name), device_tokens={"a": "d"*32, "b": "b"*32},
                                 operator_token="o"*32, allow_simulation=True)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.closed = False
        self.png = demo_png(48, 80)

    def tearDown(self):
        if not self.closed:
            self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def headers(self, token="d"*32, key=None, **extra):
        return {"Authorization": "Bearer "+token, "Idempotency-Key": key or str(uuid.uuid4()), **extra}

    def start(self, **overrides):
        body={"boot_id":str(uuid.uuid4()),"capabilities":["adb"],"simulated":True,**overrides}
        r=self.client.post("/v1/devices/a/sessions",headers=self.headers(),json=body)
        self.assertEqual(r.status_code,200,r.text)
        return r.json()["session_id"]

    def frame(self,sid,n=1,png=None,key=None):
        return self.client.post(f"/v1/sessions/{sid}/frames",headers=self.headers(key=key,**{"Content-Type":"image/png","X-Frame-Id":str(n)}),content=png or self.png)

    def ready(self, simulated=True):
        sid=self.start(simulated=simulated)
        self.assertEqual(self.frame(sid).status_code,200)
        r=self.client.post(f"/v1/sessions/{sid}/intent",headers=self.headers(),json={"text":"demo","frame_id":1})
        self.assertEqual(r.status_code,200,r.text)
        return sid

    def next(self,sid,key=None):
        return self.client.post(f"/v1/sessions/{sid}/actions/next",headers=self.headers(key=key),json={})

    def result(self,sid,action,key=None):
        return self.client.post(f"/v1/sessions/{sid}/actions/{action['action_id']}/result",headers=self.headers(key=key),
                                json={"lease_id":action["lease_id"],"status":"succeeded","detail":"SIMULATION"})

    def status(self,sid):
        return self.client.get(f"/v1/sessions/{sid}/status",headers=self.headers()).json()

    def test_complete_http_contract_and_audit_chain(self):
        sid=self.ready()
        action=self.next(sid).json()["action"]
        self.assertEqual(action["command"],{"type":"key","key":"HOME"})
        self.assertEqual(action["based_on_frame"],1)
        self.assertEqual(self.result(sid,action).status_code,200)
        self.assertEqual(self.status(sid)["intent"]["status"],"awaiting_frame")
        self.assertEqual(self.frame(sid,2,demo_png(48,80,1)).status_code,200)
        self.assertEqual(self.status(sid)["intent"]["status"],"done")
        events=[json.loads(line) for line in (Path(self.temp.name)/"logs/wellmanifest-events.jsonl").read_text().splitlines()]
        self.assertGreater(len(events),5)
        previous="0"*64
        for i,event in enumerate(events,1):
            validate_event(event)
            self.assertEqual(event["sequence"],i)
            self.assertEqual(event["previousHash"],previous)
            previous=event["eventHash"]
        self.assertNotIn("Bearer",json.dumps(events))
        self.assertNotIn("SIMULATION",json.dumps(events))

    def test_no_action_without_user_intent(self):
        sid=self.start()
        self.frame(sid)
        msg=self.next(sid).json()
        self.assertEqual(msg["state"],"idle")
        self.assertIsNone(msg["action"])

    def test_old_observation_requests_capture_before_planning(self):
        sid=self.start()
        self.frame(sid)
        self.app.state.store.db.execute("UPDATE frames SET created=? WHERE session_id=?",(time.time()-10,sid))
        result=self.client.post(f"/v1/sessions/{sid}/intent",headers=self.headers(),json={"text":"Open settings now","frame_id":1})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.status(sid)["intent"]["status"],"awaiting_frame")
        message=self.next(sid).json()
        self.assertTrue(message["capture_requested"])
        self.assertIsNone(message["action"])
        self.assertEqual(self.frame(sid,2).status_code,200)
        action=self.next(sid).json()["action"]
        self.assertEqual(action["based_on_frame"],2)

    def test_authorization_and_device_isolation(self):
        sid=self.start()
        self.frame(sid)
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/status").status_code,401)
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/status",headers=self.headers(token="b"*32)).status_code,404)
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/frames/1",headers=self.headers(token="b"*32)).status_code,404)
        self.assertEqual(self.client.get("/v1/sessions",headers=self.headers()).status_code,403)
        self.assertEqual(self.client.get("/v1/sessions",headers=self.headers(token="o"*32)).status_code,200)

    def test_idempotency_conflict_and_frame_monotonicity(self):
        sid=self.start()
        first=self.frame(sid,key="same-frame-0001")
        second=self.frame(sid,key="same-frame-0001")
        self.assertEqual(first.json(),second.json())
        self.assertEqual(self.frame(sid,2,key="same-frame-0001").status_code,409)
        self.assertEqual(self.frame(sid,1).status_code,409)
        self.assertEqual(self.frame(sid,2).status_code,200)

    def test_lease_retry_does_not_deliver_an_action_twice(self):
        sid=self.ready()
        first=self.next(sid,key="lease-idem-001")
        second=self.next(sid,key="lease-idem-001")
        self.assertEqual(first.json()["action"]["lease_id"],second.json()["action"]["lease_id"])
        self.assertLessEqual(second.json()["action"]["valid_for_ms"],first.json()["action"]["valid_for_ms"])
        third=self.next(sid)
        self.assertEqual(third.json()["state"],"awaiting_result")
        self.assertIsNone(third.json()["action"])
        action=first.json()["action"]
        result=self.result(sid,action,key="result-idem-001")
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.result(sid,action,key="result-idem-001").json(),result.json())
        self.assertEqual(self.result(sid,action).status_code,409)
        self.assertEqual(self.next(sid,key="lease-idem-001").headers.get("X-Clik-Error-Code"),"lease_conflict")

    def test_expired_lease_is_uncertain_and_never_replayed(self):
        sid=self.ready()
        action=self.next(sid,key="expiry-lease-001").json()["action"]
        self.app.state.store.db.execute("UPDATE actions SET lease_expires=? WHERE id=?",(time.time()-1,action["action_id"]))
        msg=self.next(sid).json()
        self.assertEqual(msg["state"],"uncertain")
        self.assertIsNone(msg["action"])
        self.assertEqual(self.result(sid,action).status_code,409)
        self.assertEqual(self.next(sid).json()["state"],"uncertain")
        retry=self.next(sid,key="expiry-lease-001")
        self.assertEqual(retry.status_code,409)
        self.assertEqual(retry.headers["X-Clik-Error-Code"],"lease_conflict")

    def test_restart_does_not_reissue_unacknowledged_action(self):
        sid=self.ready()
        action=self.next(sid).json()["action"]
        self.client.__exit__(None,None,None)
        self.closed=True
        store=Store(self.settings)
        row=store.db.execute("SELECT state FROM actions WHERE id=?",(action["action_id"],)).fetchone()
        self.assertEqual(row["state"],"uncertain")
        store.close()

    def test_new_screenshot_invalidates_old_coordinates(self):
        sid=self.ready()
        old_id=self.status(sid)["action"]["action_id"]
        self.frame(sid,2,demo_png(48,80,1))
        row=self.app.state.store.db.execute("SELECT state FROM actions WHERE id=?",(old_id,)).fetchone()
        self.assertEqual(row["state"],"stale")
        msg=self.next(sid).json()
        self.assertTrue(msg["action"] is None or msg["action"]["based_on_frame"]==2)

    def test_new_boot_supersedes_session(self):
        old=self.start()
        self.frame(old)
        new=self.start()
        self.assertNotEqual(old,new)
        self.assertEqual(self.next(old).status_code,409)
        self.assertEqual(self.next(old).headers["X-Clik-Error-Code"],"session_expired")
        self.assertEqual(self.frame(old,2).status_code,409)

    def test_operator_confirmation_cannot_be_granted_by_device(self):
        sid=self.ready()
        action=self.status(sid)["action"]
        self.app.state.store.db.execute("UPDATE actions SET state='awaiting_confirmation',confirmation=1 WHERE id=?",(action["action_id"],))
        path=f"/v1/sessions/{sid}/actions/{action['action_id']}/confirm"
        self.assertEqual(self.client.post(path,headers=self.headers(),json={"approve":True}).status_code,403)
        self.assertEqual(self.next(sid).json()["state"],"awaiting_confirmation")
        self.assertEqual(self.client.post(path,headers=self.headers(token="o"*32),json={"approve":True}).status_code,200)
        self.assertIsNotNone(self.next(sid).json()["action"])

    def test_uncertain_resolution_requires_new_frame(self):
        sid=self.ready()
        action=self.next(sid).json()["action"]
        self.app.state.store.db.execute("UPDATE actions SET lease_expires=? WHERE id=?",(time.time()-1,action["action_id"]))
        self.next(sid)
        path=f"/v1/sessions/{sid}/actions/{action['action_id']}/resolve"
        self.assertEqual(self.client.post(path,headers=self.headers(token="o"*32),json={"resolution":"abandon","frame_id":1}).status_code,409)
        self.frame(sid,2)
        self.assertEqual(self.client.post(path,headers=self.headers(token="o"*32),json={"resolution":"abandon","frame_id":2}).status_code,200)

    def test_projection_failure_does_not_rollback_committed_event(self):
        sid=self.start()
        target=Path(self.temp.name)/"logs/domain-events.jsonl"
        target.unlink()
        target.mkdir()
        r=self.frame(sid)
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.status(sid)["latest_frame"],1)
        self.assertIsNotNone(self.app.state.store.export_error)
        target.rmdir()
        self.app.state.store.export_events()
        self.assertIsNone(self.app.state.store.export_error)
        self.assertIn("frame_received",target.read_text())

    def test_partial_projection_line_is_recovered_without_duplicate(self):
        sid=self.start()
        path=Path(self.temp.name)/"logs/wellmanifest-events.jsonl"
        with path.open("ab") as out: out.write(b'{"partial":')
        self.frame(sid)
        events=[json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([e["sequence"] for e in events],list(range(1,len(events)+1)))

    def test_invalid_oversized_or_mismatched_png_is_rejected(self):
        sid=self.start()
        corrupt=bytearray(self.png);corrupt[45]^=1
        self.assertEqual(self.frame(sid,png=bytes(corrupt)).status_code,422)
        self.assertEqual(self.frame(sid,png=b"not png").status_code,415)
        r=self.client.post(f"/v1/sessions/{sid}/frames",headers=self.headers(**{"Content-Type":"image/png","X-Frame-Id":"1","X-Width":"1000"}),content=self.png)
        self.assertEqual(r.status_code,422)
        r=self.client.post(f"/v1/sessions/{sid}/frames",headers=self.headers(**{"Content-Type":"image/png","X-Frame-Id":"1","Content-Length":"999999999"}),content=b"x")
        self.assertEqual(r.status_code,413)

    def test_real_session_never_falls_back_to_simulation(self):
        sid=self.ready(simulated=False)
        state=self.status(sid)
        self.assertEqual(state["intent"]["status"],"failed")
        self.assertIn("not configured",state["intent"]["detail"])
        self.assertIsNone(self.next(sid).json()["action"])

    def test_simulation_can_be_disabled(self):
        other=Settings(state_dir=Path(self.temp.name)/"other",device_tokens={"a":"d"*32})
        with TestClient(create_app(other)) as client:
            r=client.post("/v1/devices/a/sessions",headers=self.headers(),json={"boot_id":"x","capabilities":["adb"],"simulated":True})
            self.assertEqual(r.status_code,403)

    def test_operator_capture_requests_new_frame_without_an_intent(self):
        sid=self.start()
        r=self.client.post(f"/v1/sessions/{sid}/capture",headers=self.headers(token="o"*32),json={})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.next(sid).json()["state"],"capture_requested")
        self.frame(sid)
        self.assertFalse(self.next(sid).json()["capture_requested"])

    def test_frame_retention_is_bounded(self):
        sid=self.start()
        for n in range(1,12): self.assertEqual(self.frame(sid,n,demo_png(48,80,n%2)).status_code,200)
        count=self.app.state.store.db.execute("SELECT count(*) FROM frames WHERE retained=1").fetchone()[0]
        self.assertEqual(count,self.settings.frames_per_session)
        self.assertEqual(self.client.get(f"/v1/sessions/{sid}/frames/1",headers=self.headers()).status_code,404)

    def test_read_queries_do_not_append_events(self):
        sid=self.start()
        before=self.app.state.store.db.execute("SELECT count(*) FROM domain_events").fetchone()[0]
        self.status(sid)
        after=self.app.state.store.db.execute("SELECT count(*) FROM domain_events").fetchone()[0]
        self.assertEqual(before,after)

    def test_event_replay_rebuilds_all_command_projections(self):
        import sqlite3
        from clikvm.store import PROJECTED_TABLES
        sid=self.ready()
        action=self.next(sid).json()["action"]
        self.result(sid,action)
        self.frame(sid,2,demo_png(48,80,1))
        destination=Path(self.temp.name)/"rebuilt"
        self.app.state.store.rebuild(destination)
        rebuilt=sqlite3.connect(destination/"events.sqlite3")
        rebuilt.row_factory=sqlite3.Row
        try:
            for table in PROJECTED_TABLES:
                source=[dict(r) for r in self.app.state.store.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                target=[dict(r) for r in rebuilt.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                self.assertEqual(source,target,table)
            self.assertTrue(any((destination/"frames").glob("*.png")))
        finally:
            rebuilt.close()

    def test_events_are_append_only_and_second_worker_is_rejected(self):
        import sqlite3
        self.start()
        with self.assertRaises(sqlite3.IntegrityError):
            self.app.state.store.db.execute("DELETE FROM domain_events")
        with self.assertRaises(RuntimeError):
            Store(self.settings)


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_openai_request_and_typed_response(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"status":"action","action":{"type":"tap","x":2,"y":3},"reason":"Open settings","requires_confirmation":False})}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            upstream=Upstreams(Settings(model_endpoint="https://models.example/v1/chat/completions",model_name="vision",model_api_key="upstream-key"),client)
            plan=await upstream.plan(goal="open settings",png=demo_png(48,80),width=48,height=80,history=[],simulated=False,step=0)
            self.assertEqual(plan.action.type,"tap")
        body=json.loads(requests[0].content)
        self.assertTrue(body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(requests[0].headers["authorization"],"Bearer upstream-key")

    async def test_invalid_model_output_fails_closed(self):
        handler=lambda request:httpx.Response(200,json={"choices":[{"message":{"content":'{"status":"action","action":{"type":"shell","command":"rm -rf"}}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            upstream=Upstreams(Settings(model_endpoint="https://example.test/model",model_name="v"),client)
            with self.assertRaises(Exception) as raised:
                await upstream.plan(goal="anything",png=demo_png(48,80),width=48,height=80,history=[],simulated=False,step=0)
            self.assertEqual(raised.exception.status_code,502)

    async def test_poa_only_routes_never_dispatches(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,json={"ok":True,"selected_operation":"example"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await Upstreams(Settings(poa_api_key="test"),client).route_poa("show devices")
        self.assertTrue(result["ok"])
        self.assertEqual(requests[0].url.params["action"],"route")
        self.assertEqual(json.loads(requests[0].content),{"query":"show devices","format":"nl"})

    async def test_audio_upstreams_and_normalization(self):
        buf=io.BytesIO()
        with wave.open(buf,"wb") as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b"\x00\x00"*2400)
        provider_wav=buf.getvalue()
        requests=[]
        def handler(request):
            requests.append(request)
            if request.url.path=="/stt": return httpx.Response(200,json={"text":"Otwórz ustawienia"})
            return httpx.Response(200,content=provider_wav,headers={"content-type":"audio/wav"})
        settings=Settings(stt_endpoint="https://audio.test/stt",stt_model="whisper",tts_endpoint="https://audio.test/tts",tts_model="tts",tts_voice="pl")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            upstream=Upstreams(settings,client)
            normalized=await upstream.speak("Gotowe")
            self.assertEqual(validate_wav(normalized,True)[:2],(1,16000))
            self.assertEqual(await upstream.transcribe(normalized),"Otwórz ustawienia")
        self.assertIn("multipart/form-data",requests[-1].headers["content-type"])

    def test_literal_android_text_rejects_unicode_percent_and_control(self):
        for bad in ["zażółć","100%","line\nnext","\x00"]:
            with self.assertRaises(ValidationError): Text(type="text",text=bad)
        self.assertEqual(Text(type="text",text="Don't run $(commands); just text").type,"text")


class GatewayAudioAPITests(unittest.TestCase):
    def test_binary_audio_contract_and_exact_retry_cache(self):
        audio=io.BytesIO()
        with wave.open(audio,"wb") as writer:
            writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(16000);writer.writeframes(b"\x00\x00"*1600)
        wav=audio.getvalue()
        calls={"stt":0,"tts":0}
        def handler(request):
            if request.url.path=="/stt":
                calls["stt"]+=1
                return httpx.Response(200,json={"text":"Otwórz ustawienia"})
            calls["tts"]+=1
            return httpx.Response(200,content=wav,headers={"content-type":"audio/wav"})
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with tempfile.TemporaryDirectory() as directory:
                settings=Settings(state_dir=Path(directory),device_tokens={"a":"d"*32},
                                  stt_endpoint="https://audio.example/stt",tts_endpoint="https://audio.example/tts",tts_model="voice",tts_voice="pl")
                with TestClient(create_app(settings,upstream_client)) as client:
                    headers={"Authorization":"Bearer "+"d"*32,"Idempotency-Key":"audio-session-001"}
                    response=client.post("/v1/devices/a/sessions",headers=headers,json={"boot_id":"audio-boot","capabilities":["adb","microphone","speaker"]})
                    self.assertEqual(response.status_code,200,response.text)
                    base="/v1/sessions/"+response.json()["session_id"]
                    stt_headers={**headers,"Idempotency-Key":"stt-request-001","Content-Type":"audio/wav"}
                    for _ in range(2):
                        result=client.post(base+"/audio/stt",headers=stt_headers,content=wav)
                        self.assertEqual(result.status_code,200,result.text)
                        self.assertEqual(result.json()["text"],"Otwórz ustawienia")
                    tts_headers={**headers,"Idempotency-Key":"tts-request-001"}
                    first=client.post(base+"/audio/tts",headers=tts_headers,json={"text":"Gotowe"})
                    second=client.post(base+"/audio/tts",headers=tts_headers,json={"text":"Gotowe"})
                    self.assertEqual(first.status_code,200,first.text[:100])
                    self.assertEqual(first.content,second.content)
                    self.assertEqual(first.headers["X-Sample-Rate"],"16000")
                    self.assertEqual(validate_wav(first.content,True)[:2],(1,16000))
                    conflict=client.post(base+"/audio/tts",headers=tts_headers,json={"text":"Different content"})
                    self.assertEqual(conflict.status_code,409)
                    self.assertEqual(calls,{"stt":1,"tts":1})
        finally:
            asyncio.run(upstream_client.aclose())


if __name__=="__main__":
    unittest.main()
