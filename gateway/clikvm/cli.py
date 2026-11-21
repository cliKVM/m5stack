from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import struct
import time
import uuid
import zlib
from pathlib import Path

import httpx

from .config import Settings


def load_env(path: Path):
    if not path.exists():
        return
    import re
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}")
        name, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError(f"Invalid variable name on .env line {number}")
        tokens = shlex.split(value, comments=True)
        if len(tokens) > 1:
            raise ValueError(f"Quote values with spaces on .env line {number}")
        os.environ.setdefault(name, tokens[0] if tokens else "")


def demo_png(width: int = 360, height: int = 640, phase: int = 0) -> bytes:
    """Synthetic frame for transport tests only; never presented as a real phone."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            color = (25, 21, 24)
            if 25 < x < width-25 and 35 < y < 75:
                color = (255, 87, 51)
            for row in range(2):
                for col in range(2):
                    left=32+col*(width//2-12); top=150+row*150
                    if left <= x < left+width//2-42 and top <= y < top+120:
                        color = (243, 240, 234) if (row+col+phase)%2 else (68, 59, 66)
            if (x-width//2)**2+(y-285)**2 < 20**2:
                color=(255,87,51)
            raw.extend(color)
    def chunk(kind,data):
        return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(raw,6))+chunk(b"IEND",b"")


def simulate(url: str, device_id: str, settings: Settings, hold_seconds: int = 0):
    token=settings.device_tokens.get(device_id)
    if not token:
        raise ValueError("Configure a token for the simulator device in CLIK_DEVICE_TOKENS_JSON")
    with httpx.Client(base_url=url.rstrip("/"),timeout=60,follow_redirects=False,trust_env=False) as client:
        def request(path,body=None,*,binary=None,frame=None,operator=False):
            selected=settings.operator_token if operator else token
            headers={"Authorization":"Bearer "+selected,"Idempotency-Key":str(uuid.uuid4())}
            if binary is not None:
                headers.update({"Content-Type":"image/png","X-Frame-Id":str(frame)})
            result=client.post(path,headers=headers,json=body if binary is None else None,content=binary)
            result.raise_for_status()
            return result.json()
        session=request(f"/v1/devices/{device_id}/sessions",{"boot_id":str(uuid.uuid4()),"capabilities":["adb","display"],"simulated":True})
        sid=session["session_id"]
        base=f"/v1/sessions/{sid}"
        request(base+"/frames",binary=demo_png(),frame=1)
        request(base+"/intent",{"text":"SIMULATOR: demonstrate the HTTP action loop","frame_id":1})
        print(json.dumps({"simulation":True,"session_id":sid,"event":"session_started"}))
        frame_id=1
        deadline=time.monotonic()+max(30,hold_seconds)
        completed=False
        while time.monotonic()<deadline:
            message=request(base+"/actions/next",{})
            if message.get("capture_requested"):
                frame_id+=1
                request(base+"/frames",binary=demo_png(phase=frame_id%2),frame=frame_id)
            action=message.get("action")
            if action:
                # A simulator acknowledges only the scripted harmless command.
                if action["command"]!={"type":"key","key":"HOME"}:
                    raise RuntimeError("Simulator refuses non-demo commands")
                request(base+f"/actions/{action['action_id']}/result",{"lease_id":action["lease_id"],"status":"succeeded","detail":"SIMULATED execution; no physical phone"})
                frame_id+=1
                request(base+"/frames",binary=demo_png(phase=1),frame=frame_id)
                print(json.dumps({"simulation":True,"event":"scripted_action_acknowledged","frame_id":frame_id}))
            elif message["state"]=="awaiting_confirmation":
                if not settings.operator_token:
                    raise RuntimeError("Strict confirmation enabled: configure operator token for the simulation")
                status=client.get(base+"/status",headers={"Authorization":"Bearer "+token}).json()
                request(base+f"/actions/{status['action']['action_id']}/confirm",{"approve":True},operator=True)
            elif message["state"]=="done":
                if not completed:
                    print(json.dumps({"simulation":True,"event":"completed","real_hardware_tested":False}))
                    completed=True
                if not hold_seconds:
                    return
            elif message["state"] in {"failed","blocked","uncertain"}:
                raise RuntimeError("Simulation stopped in state "+message["state"])
            time.sleep(0.25 if not completed else 1)
        if not completed:
            raise RuntimeError("Simulation timed out")


def main():
    parser=argparse.ArgumentParser(description="clikVM Android device gateway")
    parser.add_argument("--env-file",type=Path,default=Path(".env"))
    sub=parser.add_subparsers(dest="command",required=True)
    init=sub.add_parser("init-env",help="Create local credentials without printing secrets")
    init.add_argument("--output",type=Path,default=Path(".env"))
    init.add_argument("--device",default="cores3-01")
    init.add_argument("--simulation",action="store_true")
    serve=sub.add_parser("serve")
    serve.add_argument("--host",default="127.0.0.1")
    serve.add_argument("--port",type=int,default=8787)
    sim=sub.add_parser("simulate",help="Run explicitly synthetic HTTP end-to-end device")
    sim.add_argument("--url",default="http://127.0.0.1:8787")
    sim.add_argument("--device",default="cores3-01")
    sim.add_argument("--hold-seconds",type=int,default=0)
    export=sub.add_parser("export-events",help="Retry JSONL projections from canonical SQLite events")
    rebuild=sub.add_parser("rebuild-projections",help="Replay committed domain events into a new state directory")
    rebuild.add_argument("--destination",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="init-env":
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",args.device):
            parser.error("Device ID must have 1..64 letters, digits, underscores or hyphens")
        if args.output.exists():
            parser.error("Refusing to overwrite existing credentials")
        values={"CLIK_DEVICE_TOKENS_JSON":json.dumps({args.device:secrets.token_urlsafe(32)},separators=(",",":")),
                "CLIK_OPERATOR_TOKEN":secrets.token_urlsafe(32),"CLIK_STATE_DIR":".state/gateway",
                "CLIK_ALLOW_SIMULATION":"1" if args.simulation else "0","CLIK_REQUIRE_ACTION_CONFIRMATION":"0"}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open("x") as out:
            out.write("# Local secrets. Do not commit this file.\n")
            for k,v in values.items(): out.write(k+"="+shlex.quote(v)+"\n")
        args.output.chmod(0o600)
        print("Created "+str(args.output)+" (credentials not printed)")
        return
    load_env(args.env_file)
    settings=Settings.from_env()
    settings.validate()
    if args.command=="serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(settings),host=args.host,port=args.port,access_log=False)
    elif args.command=="simulate":
        simulate(args.url,args.device,settings,args.hold_seconds)
    elif args.command=="export-events":
        from .store import Store
        store=Store(settings,recover=False)
        store.export_events()
        if store.export_error:
            raise SystemExit("Projection remains deferred: "+store.export_error)
        store.close()
        print("Event projections are current")
    elif args.command=="rebuild-projections":
        from .store import Store
        store=Store(settings,recover=False)
        try:
            store.rebuild(args.destination)
        finally:
            store.close()
        print("Rebuilt projections and retained images in "+str(args.destination))


if __name__=="__main__":
    main()
