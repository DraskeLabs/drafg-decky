#!/usr/bin/env python3
"""A stand-in for `drafg gamemode`, speaking the public protocol.

Behaviour is chosen with environment variables:

* FAKE_DRAFG_EXIT: unset = serve until `quit`; a number = exit with that
  code right away (to test exit-code handling).
* FAKE_DRAFG_SEQUENCE: a file with one behaviour per line, taken in turn by
  each start: a number = exit with that code, `serve` = serve. Past its last
  line, it serves. Overrides FAKE_DRAFG_EXIT.
* FAKE_DRAFG_HANG=1: ignore `quit` and SIGTERM, so it has to be killed.
* FAKE_DRAFG_STALL=1: take requests but never answer them; each one adds a
  line to $XDG_RUNTIME_DIR/fake-stalled.
* FAKE_DRAFG_PROTOCOL: the protocol version it speaks (default 2), to stand
  in for an older DraFG.
* FAKE_DRAFG_CLEANUP_HANG=1: `--cleanup` never finishes.
* FAKE_DRAFG_QUIT_DELAY: seconds it takes to quit after answering `quit`,
  to stand in for a DraFG that takes a while to put things back.

`drafg gamemode --cleanup` adds a line to $XDG_RUNTIME_DIR/fake-cleanups and
exits 0, whatever the variables above say. Every start adds "run <pid>" or
"cleanup <pid>" to $XDG_RUNTIME_DIR/fake-pids.
"""

import json
import os
import signal
import socket
import sys
import time

RUNTIME = os.environ["XDG_RUNTIME_DIR"]


def note(name: str, line: str) -> None:
    with open(os.path.join(RUNTIME, name), "a") as f:
        f.write(line + "\n")


if sys.argv[1:] == ["gamemode", "--cleanup"]:
    note("fake-pids", f"cleanup {os.getpid()}")
    note("fake-cleanups", "cleanup")
    if os.environ.get("FAKE_DRAFG_CLEANUP_HANG"):
        time.sleep(60)
    sys.exit(0)

note("fake-pids", f"run {os.getpid()}")
code = os.environ.get("FAKE_DRAFG_EXIT")
sequence = os.environ.get("FAKE_DRAFG_SEQUENCE")
if sequence:
    with open(sequence) as f:
        steps = f.read().split()
    code = None
    if steps:
        step, rest = steps[0], steps[1:]
        with open(sequence, "w") as f:
            f.write("\n".join(rest) + "\n")
        code = None if step == "serve" else step
if code is not None:
    sys.exit(int(code))

hang = bool(os.environ.get("FAKE_DRAFG_HANG"))
if hang:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
stall = bool(os.environ.get("FAKE_DRAFG_STALL"))
quit_delay = float(os.environ.get("FAKE_DRAFG_QUIT_DELAY", "0"))
protocol = int(os.environ.get("FAKE_DRAFG_PROTOCOL", "2"))

assert sys.argv[1] == "gamemode", sys.argv
args = sys.argv[2:]
settings = {"multiplier": 2, "resolution": "native", "stats": False, "model": "max",
            "mode": "interpolate", "upscaler": "bilinear_sharpen", "sharpness": 0.8, "backend": "auto",
            "matrix": "auto", "capture_memory": "fast"}
i = 0
while i < len(args):
    if args[i] == "--stats":
        settings["stats"] = True
        i += 1
    else:
        key = args[i].lstrip("-").replace("-", "_")
        assert key in settings, f"unknown flag {args[i]}"
        value = args[i + 1]
        if key == "multiplier":
            value = int(value)
        elif key == "sharpness":
            value = float(value)
        settings[key] = value
        i += 2

path = os.path.join(RUNTIME, "drafg", "control.sock")
os.makedirs(os.path.dirname(path), exist_ok=True)
if os.path.exists(path):
    os.unlink(path)
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(8)
print("fake drafg started with", json.dumps(settings), flush=True)
paused = False
stalled = []
while True:
    conn, _ = server.accept()
    if stall:
        # Read the request, then keep the connection open without a word.
        conn.makefile("r").readline()
        stalled.append(conn)
        note("fake-stalled", "request")
        continue
    with conn:
        f = conn.makefile("rw")
        for line in f:
            req = json.loads(line)
            cmd = req.get("cmd")
            reply = {"v": protocol, "ok": True}
            if req.get("v") != protocol:
                reply = {"v": protocol, "ok": False,
                         "error": f"protocol version {req.get('v')} is not supported ({protocol})"}
            elif cmd == "status":
                reply.update(state="paused" if paused else "running", settings=settings,
                             game_app_id=1687950, refresh_hz=60, capture_hz=30.0, output_hz=60.0,
                             generated_hz=30.0, game_fps=30.0, output_fps=60.0, added_ms=33.3,
                             cadence="locked", notice=None,
                             generation_ms_p50=9.8, model="Model", warnings=[],
                             backend="vulkan_fast",
                             resolution={"capture": "1280x800", "generation": "1280x800", "stretched": False},
                             options={"resolutions": ["640x360", "854x480", "960x540", "1280x720"],
                                      "backends": [{"name": "vulkan_fast", "available": True},
                                                   {"name": "vulkan", "available": True}],
                                      "matrix": "none"})
            elif cmd == "set":
                settings.update({k: v for k, v in req.items() if k not in ("v", "cmd")})
                reply.update(settings=settings)
            elif cmd in ("pause", "resume"):
                paused = cmd == "pause"
            elif cmd == "quit" and not hang:
                f.write(json.dumps(reply) + "\n")
                f.flush()
                time.sleep(quit_delay)
                os.unlink(path)
                sys.exit(0)
            elif cmd not in ("hello", "quit"):
                reply = {"v": protocol, "ok": False, "error": f"unknown command \"{cmd}\""}
            f.write(json.dumps(reply) + "\n")
            f.flush()
