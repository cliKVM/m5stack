"""Standalone check: real POSIX shell preserves hostile text as one argument."""
from __future__ import annotations

import ctypes
import subprocess
import sys


class Action(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_int),
        ("connection_generation", ctypes.c_uint32),
        ("x", ctypes.c_int32),
        ("y", ctypes.c_int32),
        ("x2", ctypes.c_int32),
        ("y2", ctypes.c_int32),
        ("duration_ms", ctypes.c_int32),
        ("keycode", ctypes.c_int32),
        ("text", ctypes.c_char * 1025),
    ]


library = ctypes.CDLL(sys.argv[1])
build = library.clik_action_command
build.argtypes = [ctypes.POINTER(Action), ctypes.c_char_p, ctypes.c_size_t]
build.restype = ctypes.c_int

samples = [
    "hello world",
    "a'; echo injected; '",
    '$(echo injected) " `echo injected`',
    "back\\slash & | > < ; # ~ * ? [ ]",
    "'" * 200,
]
for sample in samples:
    action = Action(kind=3, text=sample.encode("ascii"))
    output = ctypes.create_string_buffer(4096)
    assert build(ctypes.byref(action), output, len(output)) == 0
    # A shell function captures arguments; no Android/USB/hardware or file effect.
    script = 'input() { printf "%s\\0" "$@"; }; ' + output.value.decode("ascii")
    result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, check=True, timeout=2)
    assert result.stdout.split(b"\0") == [b"text", sample.replace(" ", "%s").encode("ascii"), b""]
    assert not result.stderr

url = "https://heyasa.com/?a=';echo injected;'&b=$(echo nope)&q=100%25"
action = Action(kind=4, text=url.encode("ascii"))
output = ctypes.create_string_buffer(4096)
assert build(ctypes.byref(action), output, len(output)) == 0
script = 'am() { printf "%s\\0" "$@"; }; ' + output.value.decode("ascii")
result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, check=True, timeout=2)
assert result.stdout.split(b"\0") == [b"start", b"-a", b"android.intent.action.VIEW", b"-d", url.encode("ascii"), b""]
assert not result.stderr
print("PASS: 6 actual POSIX-shell argument-preservation cases (no command injection)")
