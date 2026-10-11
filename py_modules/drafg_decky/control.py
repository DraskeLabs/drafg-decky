"""A client for DraFG's Game Mode control socket (see PROTOCOL.md)."""

from __future__ import annotations

import json
import os
import socket

PROTOCOL_VERSION = 2

# The settings DraFG accepts over the socket, with their allowed values.
MULTIPLIERS = tuple(range(2, 17))
SIZES = (
    "640x360", "854x480", "960x540", "1280x720", "1280x800", "1600x900",
    "1920x1080", "1920x1200", "2560x1080", "2560x1440", "3440x1440", "3840x2160",
)
RESOLUTIONS = ("native",) + SIZES
MODELS = ("max", "mobile")
MODES = ("interpolate", "extrapolate")
UPSCALERS = ("bilinear", "bilinear_sharpen", "fsr_edge", "fsr")
BACKENDS = ("auto", "vulkan_fast", "vulkan", "cuda")
MATRIX = ("auto", "on", "off")
CAPTURE_MEMORY = ("fast", "compatible")

CHOICES = {
    "multiplier": MULTIPLIERS,
    "resolution": RESOLUTIONS,
    "model": MODELS,
    "mode": MODES,
    "upscaler": UPSCALERS,
    "backend": BACKENDS,
    "matrix": MATRIX,
    "capture_memory": CAPTURE_MEMORY,
}


class ControlError(Exception):
    """DraFG refused a request, or could not be reached."""


class OlderDraFG(ControlError):
    """DraFG answers in an older protocol version than this plugin's."""


def socket_path(runtime_dir: str) -> str:
    return os.path.join(runtime_dir, "drafg", "control.sock")


def validate_settings(settings: dict) -> dict:
    """Keep only well-formed public settings; raise on a bad value."""
    clean: dict = {}
    for key, allowed in CHOICES.items():
        if key in settings:
            value = settings[key]
            if isinstance(value, bool) or value not in allowed:
                raise ValueError(f"{key} must be one of {allowed}")
            clean[key] = int(value) if isinstance(allowed[0], int) else value
    if "sharpness" in settings:
        value = settings["sharpness"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("sharpness must be a number 0-1")
        clean["sharpness"] = round(float(value), 2)
    if "stats" in settings:
        if not isinstance(settings["stats"], bool):
            raise ValueError("stats must be true or false")
        clean["stats"] = settings["stats"]
    return clean


class Client:
    """One request per connection: simple, and nothing to keep alive."""

    def __init__(self, runtime_dir: str, timeout: float = 2.0):
        self.path = socket_path(runtime_dir)
        self.timeout = timeout

    def available(self) -> bool:
        return os.path.exists(self.path)

    def request(self, cmd: str, **fields) -> dict:
        message = {"v": PROTOCOL_VERSION, "cmd": cmd, **fields}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(self.path)
                conn.sendall((json.dumps(message) + "\n").encode())
                reply = b""
                while not reply.endswith(b"\n"):
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    reply += chunk
        except OSError as e:
            raise ControlError(f"DraFG is not reachable: {e}") from e
        try:
            answer = json.loads(reply)
        except ValueError as e:
            raise ControlError("DraFG sent an unreadable reply") from e
        version = answer.get("v")
        if isinstance(version, int) and not isinstance(version, bool) and version < PROTOCOL_VERSION:
            raise OlderDraFG(f"DraFG speaks protocol version {version}, this plugin {PROTOCOL_VERSION}")
        if not answer.get("ok"):
            raise ControlError(answer.get("error", "DraFG refused the request"))
        return answer

    def hello(self) -> dict:
        return self.request("hello")

    def status(self) -> dict:
        return self.request("status")

    def set(self, settings: dict) -> dict:
        clean = validate_settings(settings)
        if not clean:
            raise ValueError("no setting to change")
        return self.request("set", **clean)

    def pause(self) -> dict:
        return self.request("pause")

    def resume(self) -> dict:
        return self.request("resume")

    def quit(self) -> dict:
        return self.request("quit")
