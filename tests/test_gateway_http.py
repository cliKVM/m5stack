"""Real TCP/HTTP smoke test, with an explicitly synthetic Android device."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import httpx

from clikvm.cli import simulate
from clikvm.config import Settings


class RealHTTPTests(unittest.TestCase):
    def test_synthetic_device_over_real_tcp_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1",0))
                port=sock.getsockname()[1]
            url=f"http://127.0.0.1:{port}"
            repo=Path(__file__).resolve().parents[1]
            env={**os.environ,"PYTHONPATH":str(repo/"gateway"),"CLIK_STATE_DIR":tmp,
                 "CLIK_DEVICE_TOKENS_JSON":'{"a":"dddddddddddddddddddddddddddddddd"}',
                 "CLIK_OPERATOR_TOKEN":"o"*32,"CLIK_ALLOW_SIMULATION":"1","CLIK_REQUIRE_ACTION_CONFIRMATION":"0"}
            process=subprocess.Popen([sys.executable,"-m","clikvm.cli","--env-file",str(Path(tmp)/"absent.env"),"serve","--host","127.0.0.1","--port",str(port)],
                                     cwd=repo,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+15
                with httpx.Client(trust_env=False,timeout=1) as client:
                    while time.monotonic()<deadline:
                        if process.poll() is not None:
                            self.fail("Gateway exited before readiness: "+process.stderr.read()[-2000:])
                        try:
                            if client.get(url+"/health").status_code==200: break
                        except httpx.HTTPError: pass
                        time.sleep(.05)
                    else: self.fail("Gateway readiness timeout")
                    self.assertIn("Twoje urządzenia",client.get(url+"/").text)
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    simulate(url,"a",Settings(device_tokens={"a":"d"*32},operator_token="o"*32,allow_simulation=True))
                self.assertIn('"event": "completed"',output.getvalue())
                self.assertIn('"real_hardware_tested": false',output.getvalue())
                self.assertTrue((Path(tmp)/"logs/wellmanifest-events.jsonl").is_file())
            finally:
                process.terminate()
                try: process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill();process.communicate(timeout=5)


if __name__=="__main__":
    unittest.main()
