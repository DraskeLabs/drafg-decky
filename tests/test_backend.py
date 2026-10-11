import multiprocessing
import os
import shutil
import signal
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "py_modules"))

from drafg_decky import control, steam, supervisor  # noqa: E402


LIBRARY_VDF = r'''
"libraryfolders"
{
	"0"
	{
		"path"		"{main}"
		"apps"
		{
			"228980"		"123"
		}
	}
	// a second library on an SD card
	"1"
	{
		"path"		"{card}"
	}
}
'''

MANIFEST = r'''
"AppState"
{
	"appid"		"{appid}"
	"name"		"DraFG"
	"installdir"		"{dir}"
}
'''


def make_library(root: str, app_id: int, installdir: str, executable: str | None) -> None:
    apps = os.path.join(root, "steamapps")
    os.makedirs(os.path.join(apps, "common", installdir), exist_ok=True)
    with open(os.path.join(apps, f"appmanifest_{app_id}.acf"), "w") as f:
        f.write(MANIFEST.replace("{appid}", str(app_id)).replace("{dir}", installdir))
    if executable:
        target = os.path.join(apps, "common", installdir, "drafg")
        shutil.copy(executable, target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)


class SteamLibraryTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.main = os.path.join(self.home, ".local", "share", "Steam")
        self.card = os.path.join(self.home, "card")
        os.makedirs(os.path.join(self.main, "steamapps"))
        os.makedirs(os.path.join(self.card, "steamapps"))
        with open(os.path.join(self.main, "steamapps", "libraryfolders.vdf"), "w") as f:
            f.write(LIBRARY_VDF.replace("{main}", self.main).replace("{card}", self.card))

    def tearDown(self):
        shutil.rmtree(self.home)

    def test_parses_keyvalues(self):
        data = steam.parse_vdf(LIBRARY_VDF.replace("{main}", "/a").replace("{card}", "/b"))
        self.assertEqual(data["libraryfolders"]["1"]["path"], "/b")
        self.assertEqual(data["libraryfolders"]["0"]["apps"]["228980"], "123")

    def test_finds_drafg_on_a_second_library(self):
        make_library(self.card, 5085100, "DraFG Playtest", os.path.join(HERE, "fake_drafg.py"))
        found = steam.find_drafg(self.home)
        self.assertIsNotNone(found)
        self.assertEqual(found.app_id, 5085100)
        self.assertTrue(found.executable.endswith("DraFG Playtest/drafg"))

    def test_prefers_the_store_release(self):
        make_library(self.card, 5085100, "DraFG Playtest", os.path.join(HERE, "fake_drafg.py"))
        make_library(self.main, 4934180, "DraFG", os.path.join(HERE, "fake_drafg.py"))
        self.assertEqual(steam.find_drafg(self.home).app_id, 4934180)

    def test_ignores_a_manifest_without_the_executable(self):
        make_library(self.main, 4934180, "DraFG", None)
        self.assertIsNone(steam.find_drafg(self.home))

    def test_refuses_an_installdir_that_escapes_common(self):
        make_library(self.main, 4934180, "../../evil", None)
        self.assertIsNone(steam.find_drafg(self.home))


class SettingsTest(unittest.TestCase):
    def test_only_public_settings_pass(self):
        every = {"multiplier": 16, "resolution": "960x540", "stats": True, "model": "mobile",
                 "mode": "interpolate", "upscaler": "fsr", "sharpness": 0.5, "backend": "vulkan_fast",
                 "matrix": "off", "capture_memory": "compatible"}
        self.assertEqual(control.validate_settings(every), every)
        self.assertEqual(control.validate_settings({"preset": "quality", "path": "/bin/sh"}), {})
        # Unknown settings are dropped; the known ones with them are kept.
        self.assertEqual(control.validate_settings({"volume": 2, "theme": "dark", "model": "max"}),
                         {"model": "max"})
        self.assertEqual(control.validate_settings({"multiplier": 3.0}), {"multiplier": 3})
        for bad in ({"multiplier": 1}, {"multiplier": 17}, {"multiplier": 2.5}, {"multiplier": True},
                    {"resolution": "1240x1080"}, {"resolution": "211x97"}, {"model": "ultra"},
                    {"model": "large"}, {"backend": "cpu"}, {"mode": "sideways"},
                    {"upscaler": "lanczos"}, {"sharpness": 1.5}, {"sharpness": "1"},
                    {"matrix": True}, {"capture_memory": "slow"}, {"stats": "yes"}):
            with self.assertRaises(ValueError, msg=bad):
                control.validate_settings(bad)

    def test_command_line(self):
        self.assertEqual(
            supervisor.command_line("/x/drafg", {"multiplier": 3, "resolution": "native", "volume": 2,
                                                 "stats": True, "model": "mobile", "capture_memory": "compatible",
                                                 "theme": "dark"}),
            ["/x/drafg", "gamemode", "--multiplier", "3", "--resolution", "native",
             "--model", "mobile", "--capture-memory", "compatible", "--stats"],
        )

    def test_clean_environment_carries_nothing_else(self):
        env = supervisor.clean_environment("/home/deck", "deck", "/run/user/1000")
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")


def kill_leftovers(runtime: str) -> None:
    """Kill fake DraFGs and cleanups a test left running (a hanging cleanup
    is left to finish by design)."""
    try:
        with open(os.path.join(runtime, "fake-pids")) as f:
            pids = [int(line.split()[1]) for line in f if line.strip()]
    except FileNotFoundError:
        return
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if b"gamemode" not in f.read():
                    continue  # gone, and the pid taken by something else
            os.kill(pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass


def pids(runtime: str, kind: str) -> list[int]:
    try:
        with open(os.path.join(runtime, "fake-pids")) as f:
            return [int(p) for k, p in (line.split() for line in f if line.strip()) if k == kind]
    except FileNotFoundError:
        return []


def gone(pid: int, within: float = 1.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as f:
                if f.read().split(") ")[-1].startswith("Z"):
                    return True  # dead, waiting for its parent to reap it
        except FileNotFoundError:
            return True
        time.sleep(0.02)
    return False


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.runtime = os.path.join(self.dir, "run")
        os.makedirs(self.runtime)
        self.env = supervisor.clean_environment(self.dir, "deck", self.runtime)
        self.env["PATH"] = os.environ["PATH"]
        self.made = []

    def tearDown(self):
        kill_leftovers(self.runtime)
        for sup in self.made:
            for process in (sup.process, sup.cleanup_process):
                if process is not None:
                    process.kill()
                    process.wait()
        shutil.rmtree(self.dir)

    def make(self, **env) -> supervisor.Supervisor:
        e = dict(self.env)
        e.update(env)
        sup = supervisor.Supervisor(
            executable=os.path.join(HERE, "fake_drafg.py"),
            working_dir=self.dir,
            env=e,
            log_path=os.path.join(self.dir, "logs", "drafg.log"),
            settings={"multiplier": 3, "resolution": "native", "stats": False},
        )
        self.made.append(sup)
        return sup

    def sequence(self, *steps: str) -> str:
        path = os.path.join(self.dir, "sequence")
        with open(path, "w") as f:
            f.write("\n".join(steps) + "\n")
        return path

    def cleanups(self) -> int:
        try:
            with open(os.path.join(self.runtime, "fake-cleanups")) as f:
                return len(f.readlines())
        except FileNotFoundError:
            return 0

    def wait_for_socket(self, sup):
        deadline = time.monotonic() + 5
        while not sup.client().available() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(sup.client().available())

    def exit_and_poll(self, sup, now: float) -> None:
        """Let the DraFG started last exit, and `poll` at `now`."""
        sup.process.wait()
        sup.poll(now)

    def test_start_set_status_stop(self):
        sup = self.make()
        sup.start()
        self.wait_for_socket(sup)
        self.assertEqual(sup.status()["settings"]["multiplier"], 3)
        sup.apply({"resolution": "960x540", "model": "mobile"})
        self.assertEqual(sup.status()["settings"]["resolution"], "960x540")
        self.assertEqual(sup.status()["settings"]["model"], "mobile")
        sup.stop()
        self.assertFalse(sup.running())
        self.assertFalse(sup.client().available())
        self.assertEqual(self.cleanups(), 1)  # after every exit; a no-op when nothing changed

    # --- Steam not ready yet (exit 3) ------------------------------------

    def test_steam_not_ready_at_boot_is_waited_for(self):
        # Decky loads the plugin before Steam has signed in: exit 3, then
        # DraFG runs once Steam is up.
        sup = self.make(FAKE_DRAFG_SEQUENCE=self.sequence("3", "serve"))
        sup.start(boot=True)
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertEqual(sup.last_error, "waiting_for_steam")
        self.assertTrue(sup.wanted)
        sup.poll(sup.retry_at)  # restarts
        self.wait_for_socket(sup)
        self.assertIsNotNone(sup.status())
        self.assertIsNone(sup.last_error)
        sup.stop()

    def test_steam_never_confirming_ends_in_the_ownership_error(self):
        sup = self.make(FAKE_DRAFG_EXIT="3")
        sup.start(boot=True)
        now = sup.started_at
        delays = []
        while True:
            now += 0.5
            self.exit_and_poll(sup, now)
            if not sup.wanted:
                break
            self.assertEqual(sup.last_error, "waiting_for_steam")
            delays.append(sup.retry_at - now)
            now = sup.retry_at
            sup.poll(now)  # restarts
            self.assertIsNotNone(sup.process)
        self.assertEqual(sup.last_error, "ownership")
        # Not before the window has passed, and not long after.
        self.assertGreaterEqual(now, sup.started_at + supervisor.STEAM_GRACE_S)
        self.assertLess(now, sup.started_at + supervisor.STEAM_GRACE_S + max(supervisor.STEAM_RETRY_S) + 1)
        # Backing off, then steady.
        self.assertEqual(delays[: len(supervisor.STEAM_RETRY_S)], list(supervisor.STEAM_RETRY_S))
        self.assertEqual(set(delays[len(supervisor.STEAM_RETRY_S):]), {supervisor.STEAM_RETRY_S[-1]})

    def test_turning_it_on_gets_a_short_steam_window(self):
        sup = self.make(FAKE_DRAFG_EXIT="3")
        sup.start()  # the player
        now = sup.started_at
        while True:
            now += 0.5
            self.exit_and_poll(sup, now)
            if not sup.wanted:
                break
            self.assertEqual(sup.last_error, "waiting_for_steam")
            now = sup.retry_at
            sup.poll(now)
        self.assertEqual(sup.last_error, "ownership")
        self.assertGreaterEqual(now, sup.started_at + supervisor.STEAM_TOGGLE_GRACE_S)
        self.assertLess(now, sup.started_at + supervisor.STEAM_TOGGLE_GRACE_S + max(supervisor.STEAM_RETRY_S) + 1)

    def test_steam_is_waited_for_from_when_the_session_appears(self):
        # Exit 4 until Game Mode's session is up, then 3 while Steam signs in,
        # then running. The Steam window counts from the session's arrival,
        # here later than it would from the start.
        sup = self.make(FAKE_DRAFG_SEQUENCE=self.sequence("4", "3", "serve"))
        sup.start(boot=True)
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertEqual(sup.last_error, "waiting_for_game_mode")
        sup.poll(sup.retry_at)  # restarts
        late = sup.started_at + supervisor.STEAM_GRACE_S + 10
        self.exit_and_poll(sup, late)
        self.assertEqual(sup.last_error, "waiting_for_steam")
        self.assertTrue(sup.wanted)
        self.assertEqual(sup.retry_at, late + supervisor.STEAM_RETRY_S[0])
        sup.poll(sup.retry_at)  # restarts
        self.wait_for_socket(sup)
        self.assertIsNotNone(sup.status())
        self.assertIsNone(sup.last_error)
        sup.stop()

    def test_turning_it_on_again_keeps_a_longer_steam_window(self):
        sup = self.make(FAKE_DRAFG_EXIT="3")
        sup.start(boot=True)
        boot_window = sup.steam_wait_until
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertEqual(sup.last_error, "waiting_for_steam")
        sup.stop()
        sup.start()  # the player, while Steam is still starting
        self.assertEqual(sup.steam_wait_until, boot_window)
        self.exit_and_poll(sup, sup.started_at + supervisor.STEAM_TOGGLE_GRACE_S + 1)
        self.assertEqual(sup.last_error, "waiting_for_steam")

    def test_ownership_failure_after_the_window_is_not_retried(self):
        sup = self.make(FAKE_DRAFG_EXIT="3")
        sup.start()
        self.exit_and_poll(sup, sup.started_at + supervisor.STEAM_TOGGLE_GRACE_S + 1)
        self.assertEqual(sup.last_error, "ownership")
        self.assertFalse(sup.wanted)
        self.assertIsNone(sup.retry_at)
        sup.stop()  # turning it off keeps the error
        self.assertEqual(sup.last_error, "ownership")

    def test_turning_it_off_ends_the_waiting_message(self):
        # Off while Steam is still awaited: nothing is waited for any more,
        # so the panel must not say so under the off toggle.
        sup = self.make(FAKE_DRAFG_EXIT="3")
        sup.start(boot=True)
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertEqual(sup.last_error, "waiting_for_steam")
        sup.stop()
        self.assertIsNone(sup.last_error)
        self.assertFalse(sup.wanted)

    # --- Game Mode's session (exit 4) and restarts -------------------------

    def test_no_session_right_after_start_is_retried(self):
        # Decky starts plugins with Steam, before Game Mode's session is up.
        sup = self.make(FAKE_DRAFG_EXIT="4")
        sup.start()
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertEqual(sup.last_error, "waiting_for_game_mode")
        self.assertTrue(sup.wanted)
        sup.poll(sup.retry_at)  # restarts
        self.assertIsNotNone(sup.process)
        sup.process.wait()

    def test_not_game_mode_is_reported_after_the_grace(self):
        sup = self.make(FAKE_DRAFG_EXIT="4")
        sup.start()
        self.exit_and_poll(sup, sup.started_at + supervisor.NO_SESSION_GRACE_S + 1)
        self.assertEqual(sup.last_error, "not_game_mode")
        self.assertFalse(sup.wanted)

    def test_a_session_restart_long_after_the_start_is_waited_for(self):
        # DraFG runs for ten minutes; then Game Mode's session restarts: the
        # stream ends (exit 0), the session is not back yet (4), Steam is not
        # signed in yet (3), and then DraFG runs again.
        sup = self.make(FAKE_DRAFG_SEQUENCE=self.sequence("serve", "4", "3", "serve"))
        sup.start(boot=True)
        self.wait_for_socket(sup)
        sup.client().quit()
        later = sup.started_at + 600
        self.exit_and_poll(sup, later)
        self.assertTrue(sup.wanted)
        self.assertIsNone(sup.last_error)
        for expected in ("waiting_for_game_mode", "waiting_for_steam"):
            restart = sup.retry_at
            sup.poll(restart)  # restarts
            self.exit_and_poll(sup, restart + 1)
            self.assertEqual(sup.last_error, expected)
            self.assertTrue(sup.wanted)
        sup.poll(sup.retry_at)
        self.wait_for_socket(sup)
        self.assertIsNotNone(sup.status())
        self.assertIsNone(sup.last_error)
        sup.stop()

    def test_failures_far_apart_are_each_restarted(self):
        # Each run lasts past HEALTHY_RUN_S: a new incident every time, not
        # a crash loop.
        sup = self.make(FAKE_DRAFG_EXIT="1")
        sup.start()
        for _ in range(len(supervisor.BACKOFF_S) + 2):
            self.exit_and_poll(sup, sup.spawned_at + supervisor.HEALTHY_RUN_S + 1)
            self.assertEqual(sup.last_error, "restarting")
            self.assertEqual(sup.retry_at - sup.spawned_at, supervisor.HEALTHY_RUN_S + 1 + supervisor.BACKOFF_S[0])
            sup.poll(sup.retry_at)  # restarts
        sup.process.wait()

    def test_crashes_back_off_then_give_up(self):
        sup = self.make(FAKE_DRAFG_EXIT="1")
        sup.start()
        now = time.monotonic()
        for _ in range(len(supervisor.BACKOFF_S) + 1):
            self.exit_and_poll(sup, now)
            if not sup.wanted:
                break
            self.assertEqual(sup.last_error, "restarting")
            now = sup.retry_at
            sup.poll(now)  # restarts
        self.assertEqual(sup.last_error, "crashed")
        self.assertFalse(sup.wanted)

    # --- messages ------------------------------------------------------------

    def test_an_older_drafg_asks_for_an_update(self):
        sup = self.make(FAKE_DRAFG_EXIT="2")
        sup.start()
        self.exit_and_poll(sup, time.monotonic())
        self.assertEqual(sup.last_error, "update_drafg")
        self.assertFalse(sup.wanted)

    def test_a_running_drafg_clears_the_waiting_messages(self):
        for message in supervisor.PASSING_ERRORS:
            sup = self.make()
            sup.start()
            sup.last_error = message
            self.wait_for_socket(sup)
            self.assertIsNotNone(sup.status())
            self.assertIsNone(sup.last_error, msg=message)
            sup.stop()

    def test_status_reports_the_v2_fields(self):
        sup = self.make()
        sup.start()
        self.wait_for_socket(sup)
        status = sup.status()
        self.assertEqual(status["v"], control.PROTOCOL_VERSION)
        for key in ("game_fps", "output_fps", "added_ms", "cadence", "notice"):
            self.assertIn(key, status)
        sup.stop()

    def test_an_older_protocol_asks_for_an_update(self):
        # An older DraFG knows every flag, so it starts, but refuses requests.
        sup = self.make(FAKE_DRAFG_PROTOCOL="1")
        sup.start()
        self.wait_for_socket(sup)
        self.assertIsNone(sup.status())
        self.assertEqual(sup.last_error, "update_drafg")
        with mock.patch.object(supervisor, "QUIT_GRACE_S", 0.3):
            sup.stop()
        self.assertFalse(sup.running())

    # --- cleanup and stopping ------------------------------------------------

    def test_unexpected_exit_runs_cleanup_before_the_backoff(self):
        sup = self.make(FAKE_DRAFG_EXIT="1")
        sup.start()
        self.exit_and_poll(sup, time.monotonic())
        self.assertEqual(self.cleanups(), 1)
        self.assertEqual(sup.last_error, "restarting")
        self.assertIsNotNone(sup.retry_at)

    def test_every_exit_runs_cleanup(self):
        for n, code in enumerate(("0", "2", "3", "5"), start=1):
            sup = self.make(FAKE_DRAFG_EXIT=code)
            sup.start()
            self.exit_and_poll(sup, time.monotonic())
            self.assertTrue(sup.wait_cleanup(time.monotonic() + 5))
            self.assertEqual(self.cleanups(), n, msg=f"exit {code}")
        sup = self.make(FAKE_DRAFG_EXIT="4")
        sup.start()
        self.exit_and_poll(sup, sup.started_at + 1)
        self.assertTrue(sup.wait_cleanup(time.monotonic() + 5))
        self.assertEqual(self.cleanups(), 5)
        self.assertEqual(sup.last_error, "waiting_for_game_mode")

    def test_a_killed_drafg_is_cleaned_up_after(self):
        sup = self.make(FAKE_DRAFG_HANG="1")
        sup.start()
        self.wait_for_socket(sup)
        drafg = sup.process
        with mock.patch.object(supervisor, "QUIT_GRACE_S", 0.3), mock.patch.object(supervisor, "TERM_GRACE_S", 0.3):
            started = time.monotonic()
            sup.stop()
            took = time.monotonic() - started
        self.assertFalse(sup.running())
        self.assertEqual(drafg.returncode, -signal.SIGKILL)
        self.assertEqual(self.cleanups(), 1)
        self.assertLess(took, supervisor.STOP_LIMIT_S)

    def test_a_drafg_that_cannot_be_asked_gets_sigterm_at_once(self):
        # Its control socket does not answer; SIGTERM asks it to quit instead.
        sup = self.make(FAKE_DRAFG_STALL="1")
        sup.start()
        self.wait_for_socket(sup)
        drafg = sup.process
        started = time.monotonic()
        sup.stop()
        took = time.monotonic() - started
        self.assertEqual(drafg.returncode, -signal.SIGTERM)
        self.assertLess(took, supervisor.QUIT_REQUEST_TIMEOUT_S + 0.5)
        self.assertEqual(self.cleanups(), 1)

    def test_stop_after_an_unnoticed_crash_cleans_up(self):
        sup = self.make(FAKE_DRAFG_EXIT="1")
        sup.start()
        sup.process.wait()
        sup.stop()  # before `poll` saw the exit
        self.assertEqual(self.cleanups(), 1)
        self.assertIsNone(sup.process)

    def test_stop_worst_case_is_bounded(self):
        # DraFG answers nothing, ignores SIGTERM until SIGKILL, and its
        # cleanup never finishes: stop still returns within STOP_LIMIT_S, and
        # leaves the cleanup running rather than cut it short.
        sup = self.make(FAKE_DRAFG_STALL="1", FAKE_DRAFG_HANG="1", FAKE_DRAFG_CLEANUP_HANG="1")
        sup.start()
        self.wait_for_socket(sup)
        drafg = sup.process
        started = time.monotonic()
        sup.stop()
        took = time.monotonic() - started
        self.assertEqual(drafg.returncode, -signal.SIGKILL)
        self.assertLess(took, supervisor.STOP_LIMIT_S + 0.25)
        self.assertGreaterEqual(took, supervisor.STOP_LIMIT_S - 0.05)  # it did wait for the cleanup
        self.assertEqual(self.cleanups(), 1)
        self.assertIsNone(sup.cleanup_process.poll())  # still running

    def test_a_hanging_cleanup_is_left_running_until_drafg_starts(self):
        sup = self.make(FAKE_DRAFG_CLEANUP_HANG="1")
        started = time.monotonic()
        self.assertFalse(sup.cleanup(wait=0.3))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self.cleanups(), 1)
        cleanup = sup.cleanup_process
        self.assertIsNone(cleanup.poll())  # not cut short
        # DraFG puts back the same things as it starts: the cleanup ends first.
        sup.start()
        self.assertEqual(cleanup.returncode, -signal.SIGKILL)
        self.assertIsNone(sup.cleanup_process)
        self.wait_for_socket(sup)
        sup.stop()

    def test_turning_it_on_while_drafg_quits_waits_for_it(self):
        # Off, then on again while the first DraFG still quits: the second
        # starts once the first has gone, never beside it, and no cleanup
        # runs under it (it puts back what the first left as it starts).
        sup = self.make(FAKE_DRAFG_QUIT_DELAY="1.0")
        sup.start()
        self.wait_for_socket(sup)
        first = sup.process
        stopping = threading.Thread(target=sup.stop)
        stopping.start()
        time.sleep(0.3)
        sup.start()
        self.assertIsNone(first.poll())  # still quitting
        self.assertIsNone(sup.process)  # nothing started beside it
        stopping.join(supervisor.STOP_LIMIT_S + 1)
        self.assertFalse(stopping.is_alive())
        self.assertEqual(first.returncode, 0)
        self.assertTrue(sup.wanted)
        self.wait_for_socket(sup)
        self.assertIsNotNone(sup.status())
        self.assertEqual(len(pids(self.runtime, "run")), 2)
        self.assertEqual(self.cleanups(), 0)
        sup.stop()
        self.assertEqual(self.cleanups(), 1)

    def test_the_last_toggle_wins(self):
        # Off, on, off, on in quick succession while the first DraFG still
        # quits: the second stop, waiting its turn, leaves the last start's
        # DraFG running.
        sup = self.make(FAKE_DRAFG_QUIT_DELAY="1.0")
        sup.start()
        self.wait_for_socket(sup)
        stops = []
        for _ in range(2):
            stops.append(threading.Thread(target=sup.stop))
            stops[-1].start()
            time.sleep(0.2)
            sup.start()
        for stop in stops:
            stop.join(supervisor.STOP_LIMIT_S + 1)
            self.assertFalse(stop.is_alive())
        self.assertTrue(sup.wanted)
        self.assertTrue(sup.running())
        self.wait_for_socket(sup)
        self.assertEqual(len(pids(self.runtime, "run")), 2)
        sup.stop()

    def test_stop_waits_for_a_cleanup_poll_started(self):
        sup = self.make(FAKE_DRAFG_EXIT="1")
        sup.start()
        sup.process.wait()
        sup.poll(time.monotonic())
        sup.stop()
        self.assertEqual(self.cleanups(), 1)  # one cleanup for the one exit
        self.assertIsNotNone(sup.cleanup_process.poll())


def decky_plugin_process(home, settings_dir, log_dir, runtime, extra_env):
    """The plugin's process as Decky Loader 3.2.9 runs it: main.py's Plugin on
    an event loop of its own; SIGTERM runs `_unload`, then stops the loop and
    exits. SIGUSR1 stands in for the panel turning frame generation off.

    Right after the SIGTERM, Decky closes its connection to the plugin, and
    the reader at this end then keeps the event loop busy for good (seen in
    a real Decky Loader 3.2.9). `starve` does the same, just after `_unload`
    has started."""
    import asyncio
    import logging
    import types

    decky = types.ModuleType("decky")
    decky.DECKY_PLUGIN_SETTINGS_DIR = settings_dir
    decky.DECKY_PLUGIN_LOG_DIR = log_dir
    decky.DECKY_USER_HOME = home
    decky.DECKY_USER = "deck"
    decky.logger = logging.getLogger("drafg-test")
    sys.modules["decky"] = decky
    sys.path.insert(0, os.path.join(HERE, ".."))
    import main

    plain = main.clean_environment

    def environment(home, user, _runtime):
        env = plain(home, user, runtime)
        env.update(extra_env)
        return env

    main.clean_environment = environment
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    plugin = main.Plugin()
    stopping = []

    async def shutdown():
        if stopping:
            return
        stopping.append(True)
        await plugin._unload()
        loop.call_soon_threadsafe(loop.stop)
        sys.exit(0)

    def starve():
        while True:
            time.sleep(0.01)

    def on_sigterm():
        asyncio.ensure_future(shutdown())
        loop.call_soon(starve)

    loop.add_signal_handler(signal.SIGTERM, on_sigterm)
    loop.add_signal_handler(signal.SIGUSR1, lambda: asyncio.ensure_future(plugin.set_enabled(False)))
    loop.create_task(plugin._main())
    try:
        loop.run_forever()
    except SystemExit:
        pass
    finally:
        loop.close()


class DeckyUnloadTest(unittest.TestCase):
    """How long the plugin's process takes to end after Decky's SIGTERM.
    Decky kills it 5 s after; it must end by itself well before."""

    # A second short of Decky's 5 s, for a slower CPU.
    LIMIT_S = 4.0

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.home = os.path.join(self.dir, "home")
        steam_dir = os.path.join(self.home, ".local", "share", "Steam")
        make_library(steam_dir, 4934180, "DraFG", os.path.join(HERE, "fake_drafg.py"))
        self.settings = os.path.join(self.dir, "settings")
        os.makedirs(self.settings)
        with open(os.path.join(self.settings, "settings.json"), "w") as f:
            f.write('{"enabled": true}')
        self.runtime = os.path.join(self.dir, "run")
        os.makedirs(self.runtime)

    def tearDown(self):
        kill_leftovers(self.runtime)
        shutil.rmtree(self.dir)

    def unload(self, ready: str, turned_off: bool = False, **env) -> float:
        """Load the plugin (frame generation on), wait for the file `ready`
        in the runtime directory, send SIGTERM, and time the process's end.
        `turned_off`: the panel turns frame generation off 0.2 s before."""
        env["PATH"] = os.environ["PATH"]
        process = multiprocessing.get_context("fork").Process(
            target=decky_plugin_process,
            args=(self.home, self.settings, os.path.join(self.dir, "logs"), self.runtime, env),
        )
        process.start()
        try:
            deadline = time.monotonic() + 10
            while not os.path.exists(os.path.join(self.runtime, ready)):
                self.assertLess(time.monotonic(), deadline, f"no {ready}")
                time.sleep(0.02)
            time.sleep(0.1)
            if turned_off:
                os.kill(process.pid, signal.SIGUSR1)
                while True:
                    with open(os.path.join(self.settings, "settings.json")) as f:
                        if '"enabled": false' in f.read():
                            break
                    self.assertLess(time.monotonic(), deadline, "not turned off")
                    time.sleep(0.02)
                time.sleep(0.2)
            started = time.monotonic()
            process.terminate()
            process.join(5.0)
            took = time.monotonic() - started
            self.assertFalse(process.is_alive(), "still alive 5 s after SIGTERM: Decky would kill it")
            self.assertEqual(process.exitcode, 0)
            return took
        finally:
            if process.is_alive():
                process.kill()
                process.join()

    def assert_drafg_gone(self):
        runs = pids(self.runtime, "run")
        self.assertTrue(runs)
        for pid in runs:
            self.assertTrue(gone(pid), f"DraFG {pid} still running")

    def test_unload_with_drafg_running(self):
        took = self.unload("drafg/control.sock")
        self.assertLess(took, 1.0)
        self.assert_drafg_gone()
        self.assertEqual(len(pids(self.runtime, "cleanup")), 1)

    def test_unload_worst_case_fits_decky(self):
        # A status request in flight that will not be answered, a DraFG that
        # answers nothing and ignores SIGTERM until SIGKILL, and a cleanup
        # that never finishes.
        took = self.unload("fake-stalled", FAKE_DRAFG_STALL="1", FAKE_DRAFG_HANG="1", FAKE_DRAFG_CLEANUP_HANG="1")
        self.assertLess(took, self.LIMIT_S)
        self.assert_drafg_gone()
        self.assertEqual(len(pids(self.runtime, "cleanup")), 1)

    def test_unload_just_after_turning_it_off(self):
        # Turned off, and Decky unloads the plugin 0.2 s later while that
        # stop still ends a DraFG that answers nothing and ignores SIGTERM
        # (its cleanup never finishes): the unload waits for that stop, so
        # DraFG does not outlive the plugin's process uncleaned.
        took = self.unload("fake-stalled", turned_off=True,
                           FAKE_DRAFG_STALL="1", FAKE_DRAFG_HANG="1", FAKE_DRAFG_CLEANUP_HANG="1")
        self.assertLess(took, self.LIMIT_S)
        self.assert_drafg_gone()
        self.assertEqual(len(pids(self.runtime, "cleanup")), 1)

    def test_unload_while_a_cleanup_after_a_crash_runs(self):
        # DraFG failed just before: the watch loop's cleanup never finishes.
        took = self.unload("fake-cleanups", FAKE_DRAFG_EXIT="1", FAKE_DRAFG_CLEANUP_HANG="1")
        self.assertLess(took, self.LIMIT_S)
        self.assertEqual(len(pids(self.runtime, "cleanup")), 1)


class LateInstallTest(unittest.TestCase):
    """DraFG installed from Steam after Decky loaded the plugin is found, and
    started with frame generation on, without reloading the plugin."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.home = os.path.join(self.dir, "home")
        self.settings = os.path.join(self.dir, "settings")
        os.makedirs(self.settings)
        with open(os.path.join(self.settings, "settings.json"), "w") as f:
            f.write('{"enabled": true}')
        self.runtime = os.path.join(self.dir, "run")
        os.makedirs(self.runtime)

    def tearDown(self):
        kill_leftovers(self.runtime)
        shutil.rmtree(self.dir)

    def test_found_once_installed(self):
        import asyncio
        import logging
        import types

        decky = types.ModuleType("decky")
        decky.DECKY_PLUGIN_SETTINGS_DIR = self.settings
        decky.DECKY_PLUGIN_LOG_DIR = os.path.join(self.dir, "logs")
        decky.DECKY_USER_HOME = self.home
        decky.DECKY_USER = "deck"
        decky.logger = logging.getLogger("drafg-test")

        def environment(home, user, _runtime):
            env = supervisor.clean_environment(home, user, self.runtime)
            env["PATH"] = os.environ["PATH"]
            return env

        # sys.modules is put back afterwards: no `main` left behind for the
        # Decky processes other tests fork.
        with mock.patch.dict(sys.modules, {"decky": decky}), \
                mock.patch.object(sys, "path", [os.path.join(HERE, "..")] + sys.path):
            import main
            with mock.patch.object(main, "clean_environment", environment), \
                    mock.patch.object(main, "INSTALL_CHECK_S", 0.0):
                asyncio.run(self.load_then_install(main))

    async def load_then_install(self, main):
        import asyncio

        plugin = main.Plugin()
        await plugin._main()
        try:
            self.assertFalse((await plugin.get_state())["installed"])
            make_library(os.path.join(self.home, ".local", "share", "Steam"), 4934180, "DraFG",
                         os.path.join(HERE, "fake_drafg.py"))
            deadline = time.monotonic() + 10
            while not os.path.exists(os.path.join(self.runtime, "drafg", "control.sock")):
                self.assertLess(time.monotonic(), deadline, "DraFG not found, or not started")
                await asyncio.sleep(0.05)
            state = await plugin.get_state()
            self.assertTrue(state["installed"])
            self.assertTrue(state["running"])
        finally:
            await plugin._unload()


if __name__ == "__main__":
    unittest.main()
