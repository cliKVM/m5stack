#!/usr/bin/env python3
"""Explicit online integrity check of pinned public standard source files.

No files are updated, no credentials are read and no external writes occur.
Use the offline standards.py command for ordinary builds and CI.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def inspect(source):
    try:
        request = urllib.request.Request(source["source_url"], headers={"User-Agent": "clikvm-standards-integrity"})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("pinned source exceeds bounded download limit")
        actual = hashlib.sha256(body).hexdigest()
        return {"id": source["id"], "path": source["source_file"],
                "ok": actual == source["source_sha256"], "observed_sha256": actual}
    except (OSError, ValueError, urllib.error.URLError) as error:
        return {"id": source["id"], "path": source["source_file"], "ok": False, "error": str(error)}


def main():
    lock = json.loads((ROOT / ".governance/standards.lock.json").read_text())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(inspect, lock["sources"]))
    ok = all(item["ok"] for item in results)
    print(json.dumps({"ok": ok, "sources": results}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
