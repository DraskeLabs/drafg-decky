# DraFG Game Mode control protocol, version 2

This is how the plugin talks to DraFG while it runs in SteamOS Game Mode.
Everything DraFG accepts is listed here; anything else is refused.

## Starting DraFG

```
drafg gamemode [--multiplier 2-16] [--resolution native|WxH] [--stats]
               [--model max|mobile] [--mode interpolate|extrapolate]
               [--upscaler bilinear|bilinear_sharpen|fsr_edge|fsr] [--sharpness 0-1]
               [--backend auto|vulkan_fast|vulkan|cuda] [--matrix auto|on|off]
               [--capture-memory fast|compatible]
drafg gamemode --cleanup
```

DraFG must be the copy Steam installed, started from its install directory.
It checks with Steam that the account owns it, finds the Game Mode session on
its own, and shows itself only while a game is focused.

Exit codes:

| code | meaning |
|---|---|
| 0 | it was asked to quit, or the game session's stream ended |
| 2 | it did not understand its command line: this DraFG is older than the plugin |
| 3 | Steam did not confirm this account owns DraFG (also while Steam is not running or not signed in yet) |
| 4 | not running in SteamOS Game Mode, or Game Mode's session is not up yet |
| 5 | another DraFG already runs in Game Mode |
| other | an unexpected failure |

`drafg gamemode --cleanup` puts back whatever a DraFG that did not exit
cleanly may have left changed in the Game Mode session. It needs no running
game; it normally takes well under a second, and at most about 2 s when the
session does not answer. It exits 0 when it put things back, when nothing was
changed, and when the session it changed has ended (then there is nothing to
put back). It exits 1 when that session is still running but did not confirm
in time; nothing is lost, because the next DraFG start tries again.

The plugin runs it after every DraFG exit, unless it starts DraFG again at
once, and appends its output to the log. It waits up to 3 s for it (less
while Decky is unloading the plugin) and never cuts it short then; one still
running when the plugin starts DraFG again is stopped first. Either way, the
new DraFG tries the same as it starts.

## The socket

`$XDG_RUNTIME_DIR/drafg/control.sock` — a Unix stream socket, readable only by
the user running DraFG (DraFG also checks the connecting user). Requests and
replies are single lines of JSON. Every request carries `"v": 2` and a
`"cmd"`.

| request | does |
|---|---|
| `{"v":2,"cmd":"hello"}` | protocol and DraFG version |
| `{"v":2,"cmd":"status"}` | the status below |
| `{"v":2,"cmd":"set", …}` | change one or more of the settings below |
| `{"v":2,"cmd":"pause"}` | hide DraFG and stop generating, keep it running |
| `{"v":2,"cmd":"resume"}` | undo `pause` |
| `{"v":2,"cmd":"quit"}` | exit |

Replies: `{"v":2,"ok":true, …}` or `{"v":2,"ok":false,"error":"…"}`. A
request in any other version is refused, and the reply's `v` is the version
DraFG speaks: a lower one means DraFG is older than the plugin.

### Settings

| setting | values | default |
|---|---|---|
| `multiplier` | 2–16: at most this many times the game's frame rate (`output_fps` says what is shown) | 2 |
| `resolution` | `"native"` (the game's own size) or one of `"640x360"`, `"854x480"`, `"960x540"`, `"1280x720"`, `"1280x800"`, `"1600x900"`, `"1920x1080"`, `"1920x1200"`, `"2560x1080"`, `"2560x1440"`, `"3440x1440"`, `"3840x2160"` | `"native"` |
| `stats` | true / false: a small stats line in the corner | false |
| `model` | `"max"`, or `"mobile"` (faster, interpolation only) | `"max"` |
| `mode` | `"interpolate"`, or `"extrapolate"` (experimental, Max only) | `"interpolate"` |
| `upscaler` | `"bilinear"`, `"bilinear_sharpen"`, `"fsr_edge"`, `"fsr"`: used below native | `"bilinear_sharpen"` |
| `sharpness` | 0–1 | 0.8 |
| `backend` | `"auto"`, `"vulkan_fast"`, `"vulkan"`, `"cuda"` (NVIDIA) | `"auto"` |
| `matrix` | `"auto"`, `"on"`, `"off"`: matrix acceleration (Vulkan Fast) | `"auto"` |
| `capture_memory` | `"fast"` (frames stay on the GPU) or `"compatible"` | `"fast"` |

A `set` naming `"mobile"` with `"extrapolate"` (together, or one of them
against the other in force) is refused, as is a backend this device does not
have; nothing in that request is applied.

### Status

```json
{
  "v": 2, "ok": true,
  "state": "running",
  "app_version": "0.2.5",
  "settings": {"multiplier": 2, "resolution": "native", "stats": false,
               "model": "max", "mode": "interpolate", "upscaler": "bilinear_sharpen",
               "sharpness": 0.8, "backend": "auto", "matrix": "auto", "capture_memory": "fast"},
  "backend": "vulkan_fast",
  "options": {
    "resolutions": ["640x360", "854x480", "960x540", "1280x720"],
    "backends": [{"name": "vulkan_fast", "available": true}, {"name": "vulkan", "available": true}],
    "matrix": "none"
  },
  "model": "DraFG 1.6 Surge Max (interp)",
  "game_app_id": 1687950,
  "game_fps": 30.0, "output_fps": 60.0, "added_ms": 33.3,
  "cadence": "locked",
  "notice": null,
  "refresh_hz": 60,
  "capture_hz": 30.0, "output_hz": 60.0, "generated_hz": 30.0,
  "generation_ms_p50": 9.8,
  "resolution": {"capture": "1280x800", "generation": "1280x800", "stretched": false},
  "warnings": []
}
```

`state` is `running`, `waiting_for_game` (Steam's own UI is up; DraFG is
hidden), `starting` (a game is focused and DraFG has not captured a frame of
it yet) or `paused`.

What the player gets:

| field | meaning |
|---|---|
| `game_fps` | the game's own frame rate |
| `output_fps` | the frame rate DraFG shows; the game's own when it adds nothing |
| `added_ms` | the delay frame generation adds to the game's own, in ms |
| `cadence` | how the game's frames come: `locked` (held to the screen by Steam's frame limit), `steady` (at an even rate of its own), `free` (unevenly), `starting` (still being measured; only the game's frames show), or `idle` (the game already runs at the screen's rate: nothing to add, DraFG hides) |
| `notice` | the one thing DraFG asks the panel to say, or `null` (below) |

`game_fps`, `output_fps` and `added_ms` are numbers, to one decimal, only
while `state` is `running`, and `null` otherwise. `cadence` is set while
`state` is `running` or `starting`, and `null` otherwise. While `output_fps`
is the game's own (`cadence` still `starting`, or DraFG has stepped aside),
DraFG adds no delay even if `added_ms` is not 0, so show no delay then.

`refresh_hz` is the screen's refresh rate; `capture_hz`, `output_hz` and
`generated_hz` are measured rates of captured, shown and generated frames.

`backend` is the backend running (what `"auto"` chose). `options` says what
this device offers: `backends` lists each backend this build has, with
whether this device can run it; `resolutions` are the sizes smaller than the
game's own; `matrix` is whether the running backend's GPU has matrix cores
(`"hardware"`), only emulates them (`"emulated"`) or has none (`"none"`;
`null` when the backend has no such choice). Automatic uses them only in
hardware.

`notice` is `{"code": …, "params": {…}}` or `null`. DraFG picks at most one:
the first of these codes, in this order, that applies. `params` holds whole
numbers, and is `{}` for the codes that have none. `gpu_busy`, `no_room`
and `limit_hint` describe the running game and are only sent while `state`
is `running` or `starting`; the other codes are sent whenever their warning
below holds.

| code | params | meaning |
|---|---|---|
| `vrr_active` | — | variable refresh rate is on; generated frames cannot reach the screen while it is, so DraFG steps aside |
| `gpu_busy` | `output` | the GPU cannot generate frames as fast as needed; `output` fps show (the game's own rate when DraFG has stepped aside) |
| `no_room` | `fps`, `hz`, `fps2`, `m` | Steam's `fps` limit runs the screen at `hz` Hz, so no frame fits in between; a `fps2` limit would leave room for ×`m` |
| `limit_hint` | `fps`, `hz` | a Steam frame limit of `fps` (the screen then at `hz` Hz) would be smoothest |
| `performance_overlay_hidden` | — | as the warning below |
| `no_supported_size` | — | as the warning below |
| `pipeline_error` | — | as the warning below |

`warnings` lists every one of these that holds, in no particular order:

| warning | meaning |
|---|---|
| `vrr_active` | variable refresh rate is on (as the notice) |
| `performance_overlay_hidden` | Steam's performance overlay shares the one overlay slot and is hidden while DraFG shows |
| `pipeline_error` | a setting change failed; the previous settings still run |
| `gpu_busy` | the GPU cannot keep up (as the notice; its numbers are only in `notice`) |
| `no_supported_size` | the frame size is native, no offered size has the game's shape, and DraFG runs at `resolution.generation`, stretched to fit |

A panel that shows one line shows `notice`. Taking the first code of the
notice table found in `notice` or `warnings` gives the same code.
