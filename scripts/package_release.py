#!/usr/bin/env python3
"""Package committed sources, firmware and explicit evidence without local secrets.

This emits clikvm.release/v1, not an upstream Wellmanifest conformance claim.
Run after the source commit and all nine firmware builds. See docs/standards.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PROFILES = tuple(board + suffix for suffix in ("", "-network", "-hid")
                 for board in ("cores3", "lcd154", "lcd154-touch"))


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    commit = git("rev-parse", "HEAD").decode().strip()
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise SystemExit("Commit all tracked changes before packaging.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    sources = [Path(os.fsdecode(p)) for p in git("ls-files", "-z").split(b"\0") if p]
    content: dict[str, bytes] = {}
    for rel in sources:
        if (rel.name.startswith(".env") and rel.name != ".env.example") or \
                any(p in {".state", ".venv", "node_modules", "managed_components"} for p in rel.parts) or \
                rel.suffix in {".key", ".pem", ".sqlite3", ".bin", ".elf"}:
            raise SystemExit(f"Unexpected sensitive/generated tracked file: {rel}")
        path = ROOT / rel
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"Expected ordinary source file: {rel}")
        content[rel.as_posix()] = path.read_bytes()

    evidence = json.loads((ROOT / "docs/analysis/firmware-builds.json").read_text())
    for name, digest in evidence["firmware_source_sha256"].items():
        if sha((ROOT / name).read_bytes()) != digest:
            raise SystemExit(f"Firmware source changed since recorded build: {name}")
    components = []
    for profile in PROFILES:
        build = ROOT / "firmware" / ("build-" + profile)
        config = json.loads((build / "flasher_args.json").read_text())
        offsets = {k: Path(v).as_posix() for k, v in config["flash_files"].items()}
        if set(offsets) != {"0x0", "0x8000", "0x20000"}:
            raise SystemExit(f"Unexpected flash layout for {profile}")
        files = [*offsets.values(), "flash_args", "flasher_args.json"]
        for name in files:
            path = build / name
            if not path.resolve().is_relative_to(build.resolve()):
                raise SystemExit("Invalid generated file path")
            content[f"binaries/{profile}/{name}"] = path.read_bytes()
        app = (build / "clikvm_m5stack.bin").read_bytes()
        recorded = next(x for x in evidence["builds"] if x["profile"] == profile)
        if sha(app) != recorded["sha256"]:
            raise SystemExit(f"Binary changed since recorded validation: {profile}")
        compiled_config = ROOT / "firmware" / ("sdkconfig." + profile)
        content[f"binaries/{profile}/sdkconfig.built"] = compiled_config.read_bytes()
        components.append({
            "component_id": profile, "tier": "microcontroller_firmware", "version": version,
            "board": recorded["board"], "mode": recorded["mode"],
            "git_commit": commit, "chip": "esp32s3", "idf_version": "5.4.2",
            "sha256": sha(app), "image_size_bytes": len(app), "target_partition": "factory",
            "flash_files": offsets, "hardware_validated": False,
        })
    with tempfile.TemporaryDirectory(prefix="clikvm-package-") as temp:
        bundle = Path(temp) / "repository.bundle"
        subprocess.run(["git", "-C", str(ROOT), "bundle", "create", str(bundle), "main", "HEAD"], check=True)
        content["repository.bundle"] = bundle.read_bytes()

    manifest = {
        "schema": "clikvm.release/v1", "system_version": version,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_commit": commit, "repository": "https://github.com/clikVM/m5stack.git",
        "components": components,
        "compatibility": {"gateway": version, "firmware": version, "desktop_node": version,
                          "device_api": "v1", "controller_api": "v1",
                          "matrix": "gateway/docs/compatibility.json"},
        "validation": {
            "hardware_validated": False, "live_provider_validated": False,
            "full_wellman_conformance": False,
            "evidence": "docs/validation.md",
            "canonical_contracts": "docs/analysis/standards-check.json",
            "wellman_actual_report": "docs/analysis/wellman-check.json",
        },
        "files": {name: {"sha256": sha(data), "bytes": len(data)} for name, data in sorted(content.items())},
    }
    content["release-manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    content["SHA256SUMS"] = "".join(f"{sha(data)}  {name}\n" for name, data in sorted(content.items())).encode()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f"clikvm-m5stack-{version}"
    destination = output / (name + ".zip")
    temporary = output / (name + ".zip.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path, data in sorted(content.items()):
                archive.writestr(f"{name}/{path}", data)
        with zipfile.ZipFile(temporary) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError("Archive verification failed: " + bad)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"file": str(destination), "bytes": destination.stat().st_size,
                      "sha256": sha(destination.read_bytes()), "source_commit": commit,
                      "archive_entries": len(content), "hardware_validated": False}, indent=2))


if __name__ == "__main__":
    main()
