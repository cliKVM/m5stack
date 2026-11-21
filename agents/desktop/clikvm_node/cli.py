from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys

from . import __version__
from .config import NodeConfig


def load_env(path: Path | None):
    if path is None:
        return
    if path.stat().st_size > 65536:
        raise ValueError("Environment file exceeds size limit")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("Environment file must be private; use chmod 600")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("Invalid environment file line")
        name, raw = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise ValueError("Invalid environment variable name")
        values = shlex.split(raw, comments=True)
        if len(values) > 1:
            raise ValueError("Quote values containing spaces in environment files")
        os.environ.setdefault(name, values[0] if values else "")


def make_backend(config: NodeConfig):
    if config.backend == "native":
        from .native import NativeBackend
        cls = NativeBackend
    elif config.backend == "rfb":
        from .rfb import RFBBackend
        cls = RFBBackend
    else:
        from .http_api import HttpAPIBackend
        cls = HttpAPIBackend
    return cls(config.options, config.max_width, config.max_frame_bytes)


def main():
    parser = argparse.ArgumentParser(description="hey Asa / clikVM scoped desktop and network node")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Probe observation/permissions without sending keyboard, mouse or API mutations")
    run = commands.add_parser("run", help="Poll the gateway and execute authorized leased actions")
    run.add_argument("--once", action="store_true", help="Perform one protocol iteration, then exit")
    args = parser.parse_args()
    backend = runner = None
    try:
        load_env(args.env_file)
        config = NodeConfig.load(args.config)
        backend = make_backend(config)
        if args.command == "doctor":
            backend.open()
            print(json.dumps(backend.diagnostic(), ensure_ascii=False, indent=2))
            return
        from .runner import GatewayClient, NodeRunner
        runner = NodeRunner(config, backend, GatewayClient(config))
        def stop(_signum, _frame):
            runner.stop_requested = True
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        runner.run(once=args.once)
    except Exception as e:
        # Known diagnostic messages never embed the endpoint response/command or secret.
        from .common import BackendError
        message = str(e) if isinstance(e, (ValueError, BackendError)) else type(e).__name__
        print(json.dumps({"status": "error", "diagnostic": message}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
    finally:
        if runner:
            runner.close()
        elif backend:
            backend.close()


if __name__ == "__main__":
    main()
