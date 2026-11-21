import base64
import json
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
import httpx
import uvicorn
from clikvm.app import create_app
from clikvm.cli import demo_png
from clikvm.config import Settings

PROJECT = Path(__file__).resolve().parents[2]

with tempfile.TemporaryDirectory(prefix='clikvm-dom-review-') as directory:
    root=Path(directory)
    operator='test-operator-'+uuid.uuid4().hex
    device='test-device-'+uuid.uuid4().hex
    app=create_app(Settings(state_dir=root/'state',device_tokens={'test-unit':device},operator_token=operator,allow_simulation=True,require_action_confirmation=True))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    base=f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(base_url=base,trust_env=False) as client:
            for _ in range(100):
                try:
                    if client.get('/health').status_code==200:break
                except httpx.ConnectError:pass
                time.sleep(.05)
            def headers():return {'Authorization':'Bearer '+device,'Idempotency-Key':str(uuid.uuid4())}
            session=client.post('/v1/devices/test-unit/sessions',headers=headers(),json={'boot_id':str(uuid.uuid4()),'capabilities':['adb','display'],'simulated':True});session.raise_for_status();sid=session.json()['session_id']
            png=demo_png()
            response=client.post('/v1/sessions/'+sid+'/frames',headers={**headers(),'Content-Type':'image/png','X-Frame-Id':'1'},content=png);response.raise_for_status()
            config=root/'test-config.json'
            config.write_text(json.dumps({'base':base,'token':operator,'deviceToken':device,'project':str(PROJECT),'sid':sid,'png':base64.b64encode(png).decode()}))
            result=subprocess.run(['node',str(PROJECT/'tests/frontend/dom-contract.cjs'),str(config)],timeout=45)
            raise SystemExit(result.returncode)
    finally:
        server.should_exit=True;thread.join(10)
