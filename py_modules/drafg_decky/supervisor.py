"""Start, watch and stop DraFG's Game Mode process.

The plugin runs `drafg gamemode` from the install Steam made and nothing
else. DraFG itself checks Steam ownership, finds the Game Mode session, shows
itself only while a game is focused, and takes its settings over the control
socket.

DraFG's Game Mode exit codes (public, see PROTOCOL.md):

* 0 — it was asked to quit, or the game session's stream ended
* 2 — it did not understand its command line: a DraFG older than this plugin
* 3 — Steam did not confirm this account owns DraFG
* 4 — not running in SteamOS Game Mode
* 5 — another DraFG already runs in Game Mode
* anything else — an unexpected failure

Steam and Game Mode's session come up after Decky loads the plugin, and come
back after a Game Mode session restarts. Exits 3 and 4 then mean "not yet":
they are retried for a while before the player is told. Every retry is a new
DraFG, which checks ownership again from the start.

After every exit, `drafg gamemode --cleanup` puts back whatever DraFG may
have left changed in the Game Mode session (after a crash, a kill, or a quit
that could not finish); it does nothing when nothing was. A DraFG started
again at once does the same itself as it starts.

Every method may be called from any thread.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .control import Client, ControlError, OlderDraFG, validate_settings

EXIT_USAGE = 2
EXIT_OWNERSHIP = 3
EXIT_NO_SESSION = 4
EXIT_ALREADY_RUNNING = 5

# Restart delays after unexpected exits; past the last one, give up. A run
# that lasted HEALTHY_RUN_S starts the count again.
BACKOFF_S = (2.0, 5.0, 15.0)
HEALTHY_RUN_S = 60.0

# Exit 4 (no Game Mode session) is "not yet" for NO_SESSION_GRACE_S after a
# start, and after a DraFG that had found the session ends (the session may
# be restarting): retried every NO_SESSION_RETRY_S, then "not in Game Mode".
NO_SESSION_GRACE_S = 90.0
NO_SESSION_RETRY_S = 3.0

# Exit 3 (Steam did not confirm ownership) is "Steam is not ready yet" for
# STEAM_GRACE_S after Decky loads the plugin, after Game Mode's session
# appears, and after a DraFG that ran ends (Steam may be restarting with the
# session): long enough for Steam to start, update itself and sign in. After
# the player turns frame generation on, for STEAM_TOGGLE_GRACE_S. Retries
# back off along STEAM_RETRY_S, its last value repeating; the first exit 3
# after the window shows the ownership error.
STEAM_GRACE_S = 180.0
STEAM_TOGGLE_GRACE_S = 10.0
STEAM_RETRY_S = (2.0, 4.0, 8.0, 15.0)

# Stopping. DraFG is asked to quit (the request waits QUIT_REQUEST_TIMEOUT_S
# at most for an answer; without one, SIGTERM asks the same) and gets
# QUIT_GRACE_S, then TERM_GRACE_S after SIGTERM, then is killed and waited
# for KILL_WAIT_S more. The cleanup after it is waited for until STOP_LIMIT_S
# from the start of the stop, and one still running then is left to finish
# on its own — never cut short. Decky kills a plugin still alive 5 s after
# it asked it to stop: this leaves more than a second for the plugin's
# process to end (tests/test_backend.py, DeckyUnloadTest, measures it).
QUIT_REQUEST_TIMEOUT_S = 0.5
QUIT_GRACE_S = 2.0
TERM_GRACE_S = 0.5
KILL_WAIT_S = 0.5
STOP_LIMIT_S = 3.5

# After an exit while DraFG is wanted, the cleanup is waited for up to
# CLEANUP_WAIT_S. A cleanup still running when DraFG starts again is ended
# first: the new DraFG puts back the same things as it starts.
CLEANUP_WAIT_S = 3.0

# The messages for the panel that only say "not yet" or "trying again": a
# DraFG that answers ends them.
PASSING_ERRORS = ("waiting_for_game_mode", "waiting_for_steam", "restarting")


def clean_environment(home: str, user: str, runtime_dir: str) -> dict:
    """The environment DraFG starts with: only what it needs.

    Decky's loader is a frozen Python application whose environment (library
    paths in particular) must not leak into DraFG. DraFG finds the Game Mode
    session itself, from the runtime directory.
    """
    return {
        "HOME": home,
        "USER": user,
        "LOGNAME": user,
        "XDG_RUNTIME_DIR": runtime_dir,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


# Every setting with a value, and its `drafg gamemode` flag.
FLAGS = (
    ("multiplier", "--multiplier"),
    ("resolution", "--resolution"),
    ("model", "--model"),
    ("mode", "--mode"),
    ("upscaler", "--upscaler"),
    ("sharpness", "--sharpness"),
    ("backend", "--backend"),
    ("matrix", "--matrix"),
    ("capture_memory", "--capture-memory"),
)


def command_line(executable: str, settings: dict) -> list[str]:
    clean = validate_settings(settings)
    argv = [executable, "gamemode"]
    for key, flag in FLAGS:
        if key in clean:
            argv += [flag, str(clean[key])]
    if clean.get("stats"):
        argv.append("--stats")
    return argv


def _wait(process: subprocess.Popen, until: float) -> bool:
    """Wait for `process` to end until the monotonic time `until`."""
    try:
        process.wait(timeout=max(0.0, until - time.monotonic()))
        return True
    except subprocess.TimeoutExpired:
        return False


@dataclass
class Supervisor:
    executable: str
    working_dir: str
    env: dict
    log_path: str
    settings: dict = field(default_factory=dict)
    process: subprocess.Popen | None = None
    wanted: bool = False
    failures: int = 0
    retry_at: float | None = None
    last_error: str | None = None
    last_exit: int | None = None
    # When DraFG was last asked for (Decky's load, or the player).
    started_at: float | None = None
    # When the running (or last) DraFG process started.
    spawned_at: float | None = None
    # Since when DraFG has not found Game Mode's session; None once it has.
    no_session_since: float | None = None
    # Whether the last exit was 4: the next other exit means the session
    # has just come up.
    session_missing: bool = False
    # Until when exit 3 means "Steam is not ready yet", and the retries so far.
    steam_wait_until: float | None = None
    steam_retries: int = 0
    # The last `drafg gamemode --cleanup` started.
    cleanup_process: subprocess.Popen | None = None
    # The DraFG a `stop` is ending. Nothing starts another while it may
    # still run: the two would share one session.
    ending: subprocess.Popen | None = None
    # Guards every field above. Never held while waiting for DraFG to end or
    # for a cleanup to finish, so another thread's `poll` cannot hold up
    # `stop` for long (KILL_WAIT_S at most, while a start ends a cleanup).
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    # Held by the `stop` under way: one at a time. Only `stop` takes it.
    stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def client(self, timeout: float = 2.0) -> Client:
        return Client(self.env["XDG_RUNTIME_DIR"], timeout)

    def running(self) -> bool:
        process = self.process
        return process is not None and process.poll() is None

    def start(self, boot: bool = False) -> None:
        """Ask for DraFG to run; starts it now if it is not running.

        `boot`: Decky has just loaded the plugin, so Steam may still be
        starting or signing in."""
        now = time.monotonic()
        with self.lock:
            self.wanted = True
            self.failures = 0
            self.retry_at = None
            self.last_error = None
            self.started_at = now
            self.no_session_since = now
            self.session_missing = False
            # Turning it off and on again does not cut a longer wait short.
            self._wait_for_steam(now + (STEAM_GRACE_S if boot else STEAM_TOGGLE_GRACE_S))
            self.steam_retries = 0
            if self.process is not None and self.process.poll() is not None:
                # An exit `poll` has not seen yet. The DraFG started now
                # puts back what that one left as it starts.
                self.process = None
            # While a `stop` still ends the last DraFG, that `stop` starts
            # the new one once the last has gone.
            if self.process is None and self.ending is None:
                self._spawn()

    def _spawn(self) -> None:
        """Start DraFG. The lock is held."""
        self._end_cleanup()
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        log = open(self.log_path, "w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command_line(self.executable, self.settings),
                cwd=self.working_dir,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        self.spawned_at = time.monotonic()

    def stop(self) -> None:
        """Ask DraFG to quit; terminate, then kill it if it does not; then
        clean up. Takes STOP_LIMIT_S at most, whatever DraFG does.

        A `stop` already under way (frame generation turned off just before
        Decky unloads the plugin) is waited for within that limit, so its
        DraFG has gone and its cleanup has started when this returns. A
        `start` while a `stop` ends DraFG starts the new one when the old one
        has gone."""
        stopping = time.monotonic()
        deadline = stopping + STOP_LIMIT_S
        with self.lock:
            self.wanted = False
            self.retry_at = None
            # Nothing is being waited for any more.
            if self.last_error in PASSING_ERRORS:
                self.last_error = None
        if not self.stop_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            return  # the other `stop` overran its own limit
        try:
            with self.lock:
                if self.wanted:
                    return  # turned on again since: that comes last
                # Taken over here, so `poll` cannot handle the same exit
                # meanwhile, and `start` starts nothing beside it.
                process, self.process = self.process, None
                self.ending = process
            if process is not None:
                try:
                    self._end(process, stopping)
                finally:
                    with self.lock:
                        self.ending = None
                        restart = self.wanted and self.process is None
                        if restart and process.poll() is not None:
                            # Turned on again meanwhile. The new DraFG puts
                            # back what the old one left as it starts.
                            self._spawn()
                        else:
                            self._start_cleanup()
                            if restart:  # not gone even after SIGKILL: later
                                self.retry_at = time.monotonic() + BACKOFF_S[0]
            # Also waits for a cleanup `poll` started after an exit just before.
            self.wait_cleanup(deadline)
        finally:
            self.stop_lock.release()

    def _end(self, process: subprocess.Popen, stopping: float) -> None:
        if process.poll() is not None:
            return
        try:
            self.client(QUIT_REQUEST_TIMEOUT_S).quit()
            asked = True
        except ControlError:
            # Not answering, or not listening: the same request by signal.
            asked = False
            process.terminate()
        if _wait(process, stopping + QUIT_GRACE_S):
            return
        if asked:
            process.terminate()
            if _wait(process, time.monotonic() + TERM_GRACE_S):
                return
        process.kill()
        # Normally at once. A process the kernel is still tearing down is
        # left to it; Python reaps it later.
        _wait(process, time.monotonic() + KILL_WAIT_S)

    def _start_cleanup(self) -> None:
        """Start `drafg gamemode --cleanup`, its output at the end of the log.
        The lock is held. It runs in a session of its own: nothing that
        ends the plugin ends it."""
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as log:
                self.cleanup_process = subprocess.Popen(
                    [self.executable, "gamemode", "--cleanup"],
                    cwd=self.working_dir,
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError:
            self.cleanup_process = None

    def _end_cleanup(self) -> None:
        """End a cleanup still running: DraFG is about to start, and puts
        back the same things itself. The lock is held."""
        cleanup, self.cleanup_process = self.cleanup_process, None
        if cleanup is not None and cleanup.poll() is None:
            cleanup.kill()
            _wait(cleanup, time.monotonic() + KILL_WAIT_S)

    def wait_cleanup(self, until: float) -> bool:
        """Wait for the last cleanup until the monotonic time `until`; True
        when none is running any more. It is never interrupted."""
        cleanup = self.cleanup_process
        return cleanup is None or _wait(cleanup, until)

    def cleanup(self, wait: float = CLEANUP_WAIT_S) -> bool:
        """Run `drafg gamemode --cleanup`, waiting up to `wait` seconds;
        True when it has finished."""
        with self.lock:
            self._start_cleanup()
        return self.wait_cleanup(time.monotonic() + wait)

    def apply(self, settings: dict) -> dict | None:
        """Remember new settings and hand them to a running DraFG."""
        clean = validate_settings(settings)
        with self.lock:
            self.settings.update(clean)
        if clean and self.running():
            return self.client().set(clean)
        return None

    def poll(self, now: float | None = None) -> None:
        """Notice an exit, explain it, and restart when that makes sense.
        Call it about once a second."""
        now = time.monotonic() if now is None else now
        with self.lock:
            process = self.process
            exited = process is not None and process.poll() is not None
            if exited:
                self.process = None
                # Started before anything else: from here on, nothing that
                # happens to the plugin can keep it from running.
                self._start_cleanup()
                self.last_exit = process.returncode
                if self.wanted:
                    self._judge(process.returncode, now)
        if exited:
            self.wait_cleanup(time.monotonic() + CLEANUP_WAIT_S)
        with self.lock:
            if (self.wanted and self.process is None and self.ending is None
                    and self.retry_at is not None and now >= self.retry_at):
                self.retry_at = None
                self._spawn()

    def _judge(self, code: int, now: float) -> None:
        """Explain an exit while DraFG is wanted, and plan what comes next.
        The lock is held."""
        if code == EXIT_USAGE:
            self._give_up("update_drafg")
            return
        if code == EXIT_NO_SESSION:
            self.session_missing = True
            if self.no_session_since is None:
                self.no_session_since = now
            if now - self.no_session_since < NO_SESSION_GRACE_S:
                self.last_error = "waiting_for_game_mode"
                self.retry_at = now + NO_SESSION_RETRY_S
            else:
                self._give_up("not_game_mode")
            return
        # This DraFG found Game Mode's session.
        self.no_session_since = None
        if self.session_missing:
            # It has just come up, and Steam comes up with it.
            self.session_missing = False
            self._wait_for_steam(now + STEAM_GRACE_S)
        if code == EXIT_OWNERSHIP:
            if self.steam_wait_until is not None and now < self.steam_wait_until:
                self.last_error = "waiting_for_steam"
                self.retry_at = now + STEAM_RETRY_S[min(self.steam_retries, len(STEAM_RETRY_S) - 1)]
                self.steam_retries += 1
            else:
                self._give_up("ownership")
        elif code == EXIT_ALREADY_RUNNING:
            self._give_up("already_running")
        else:
            # It ran and ended: the session's stream ended (0), or it
            # failed. The session, and Steam with it, may be restarting.
            self._wait_for_steam(now + STEAM_GRACE_S)
            if self.spawned_at is not None and now - self.spawned_at >= HEALTHY_RUN_S:
                self.failures = 0
            self.failures += 1
            if self.failures > len(BACKOFF_S):
                self._give_up("crashed")
            else:
                self.last_error = "restarting" if code != 0 else None
                self.retry_at = now + BACKOFF_S[self.failures - 1]

    def _wait_for_steam(self, until: float) -> None:
        if self.steam_wait_until is None or until > self.steam_wait_until:
            self.steam_wait_until = until
            self.steam_retries = 0

    def _give_up(self, error: str) -> None:
        self.last_error = error
        self.wanted = False
        self.retry_at = None

    def status(self) -> dict | None:
        process = self.process
        if process is None or process.poll() is not None:
            return None
        try:
            status = self.client().status()
        except OlderDraFG:
            # It started (it knows every flag the plugin passes) but cannot
            # be controlled: the same advice as its exit code 2.
            with self.lock:
                if self.process is process:
                    self.last_error = "update_drafg"
            return None
        except ControlError:
            return None
        # DraFG answers: whatever it was waiting for or recovering from is over.
        with self.lock:
            if self.process is process and self.last_error in PASSING_ERRORS:
                self.last_error = None
        return status
