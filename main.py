"""DraFG for Decky — the backend.

Runs as the Deck user (no root). It finds the DraFG that Steam installed,
starts `drafg gamemode` when frame generation is switched on, stops it when
switched off, and relays settings and status between the Quick Access panel
and DraFG's control socket. It downloads nothing and runs nothing else.
"""

import asyncio
import json
import os
import threading

import decky  # provided by Decky Loader

from drafg_decky.control import ControlError, validate_settings
from drafg_decky.steam import find_drafg
from drafg_decky.supervisor import Supervisor, clean_environment

DEFAULT_SETTINGS = {
    "multiplier": 2,
    "resolution": "native",
    "stats": False,
    "model": "max",
    "mode": "interpolate",
    "upscaler": "bilinear_sharpen",
    "sharpness": 0.8,
    "backend": "auto",
    "matrix": "auto",
    "capture_memory": "fast",
}

# Steam's own UI (app 769) is not a game: nothing to remember for it.
STEAM_UI_APP_ID = 769

# While DraFG is not installed, the Steam libraries are looked at again this
# often, so a DraFG installed after Decky loaded the plugin is found.
INSTALL_CHECK_S = 5.0

# Decky kills a plugin still alive 5 s after asking it to stop. Stopping
# DraFG takes 3.5 s at most (supervisor.STOP_LIMIT_S); should it ever take
# longer, unloading goes on without it at this point.
UNLOAD_LIMIT_S = 4.0


async def blocking(fn, *args):
    """Run a blocking call on a daemon thread of its own.

    Not asyncio.to_thread: the threads of asyncio's default executor are
    waited for when the plugin's process exits, so a status request or a
    cleanup still under way then would hold up Decky's unload.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def settle(result, error):
        if future.done():  # cancelled meanwhile
            return
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)

    def run():
        try:
            result, error = fn(*args), None
        except Exception as e:
            result, error = None, e
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:  # the loop has closed: the plugin is unloading
            pass

    threading.Thread(target=run, name="drafg-call", daemon=True).start()
    return await future


class Plugin:
    async def _main(self):
        self.settings_path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")
        self.state = self._load()
        self.install = find_drafg(decky.DECKY_USER_HOME)
        self.supervisor = None
        self.last_game = None
        if self.install:
            self._supervise()
        else:
            decky.logger.warning("DraFG is not installed through Steam")
        self.loop_task = asyncio.get_event_loop().create_task(self._watch())

    def _supervise(self) -> None:
        """Take charge of the DraFG found, and start it if frame generation
        is on. On the loop, like `_unload`: the two cannot interleave."""
        decky.logger.info(f"DraFG found: app {self.install.app_id} at {self.install.directory}")
        runtime_dir = f"/run/user/{os.getuid()}"
        self.supervisor = Supervisor(
            executable=self.install.executable,
            working_dir=self.install.directory,
            env=clean_environment(decky.DECKY_USER_HOME, decky.DECKY_USER, runtime_dir),
            log_path=os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "drafg.log"),
            settings=dict(self.state["defaults"]),
        )
        if self.state["enabled"]:
            # Just loaded, or DraFG just installed: Steam may not be ready yet.
            self.supervisor.start(boot=True)

    async def _unload(self):
        # Without a single `await`: Decky closes its connection to the
        # plugin right after asking it to stop, and its side of the
        # connection in this process then keeps the event loop busy for
        # good (Decky Loader 3.2.9), so an `await` here might never return.
        if getattr(self, "loop_task", None):
            self.loop_task.cancel()
        if getattr(self, "supervisor", None):
            stopping = threading.Thread(target=self.supervisor.stop, name="drafg-stop", daemon=True)
            stopping.start()
            stopping.join(UNLOAD_LIMIT_S)
            if stopping.is_alive():
                decky.logger.warning("DraFG did not stop in time")

    async def _uninstall(self):
        pass

    # --- settings -------------------------------------------------------

    def _load(self) -> dict:
        state = {"enabled": False, "defaults": dict(DEFAULT_SETTINGS), "per_game": {}}
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                saved = json.load(f)
            state["enabled"] = bool(saved.get("enabled", False))
            state["defaults"].update(validate_settings(saved.get("defaults", {})))
            for app, settings in saved.get("per_game", {}).items():
                if str(app).isdigit():
                    state["per_game"][str(app)] = validate_settings(settings)
        except (OSError, ValueError, AttributeError):
            pass
        return state

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        tmp = self.settings_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.settings_path)

    def _settings_for(self, app_id) -> dict:
        settings = dict(self.state["defaults"])
        settings.update(self.state["per_game"].get(str(app_id), {}))
        return settings

    # --- the watch loop -------------------------------------------------

    async def _watch(self):
        loop = asyncio.get_running_loop()
        look_again_at = loop.time() + INSTALL_CHECK_S
        while True:
            await asyncio.sleep(1.0)
            try:
                if not self.supervisor:
                    if loop.time() >= look_again_at:
                        look_again_at = loop.time() + INSTALL_CHECK_S
                        self.install = await blocking(find_drafg, decky.DECKY_USER_HOME)
                        if self.install:
                            self._supervise()
                    continue
                await blocking(self.supervisor.poll)
                status = await blocking(self.supervisor.status)
                game = status.get("game_app_id") if status else None
                if game and game != STEAM_UI_APP_ID and game != self.last_game:
                    self.last_game = game
                    # A game with its own remembered settings gets them.
                    wanted = self._settings_for(game)
                    current = status.get("settings", {})
                    changes = {k: v for k, v in wanted.items() if current.get(k) != v}
                    if changes:
                        await blocking(self.supervisor.apply, changes)
            except Exception as e:  # keep watching whatever happens
                decky.logger.error(f"watch loop: {e}")

    # --- called from the Quick Access panel ------------------------------

    async def get_state(self) -> dict:
        sup = self.supervisor
        status = await blocking(sup.status) if sup else None
        game = status.get("game_app_id") if status else None
        return {
            "installed": self.install is not None,
            "enabled": self.state["enabled"],
            "running": bool(sup and sup.running()),
            "error": sup.last_error if sup else None,
            "settings": self._settings_for(game) if game else dict(self.state["defaults"]),
            "per_game": bool(game and str(game) in self.state["per_game"]),
            "status": status,
        }

    async def set_enabled(self, enabled: bool) -> dict:
        self.state["enabled"] = bool(enabled)
        self._save()
        if self.supervisor:
            if enabled:
                self.supervisor.start()
            else:
                await blocking(self.supervisor.stop)
        return await self.get_state()

    async def set_settings(self, settings: dict, remember_for_game: bool) -> dict:
        clean = validate_settings(settings)
        game = self.last_game
        if remember_for_game and game:
            self.state["per_game"].setdefault(str(game), {}).update(clean)
        else:
            self.state["defaults"].update(clean)
        self._save()
        if self.supervisor:
            try:
                await blocking(self.supervisor.apply, clean)
            except ControlError as e:
                decky.logger.warning(f"settings not applied yet: {e}")
        return await self.get_state()

    async def forget_game(self) -> dict:
        if self.last_game:
            self.state["per_game"].pop(str(self.last_game), None)
            self._save()
            if self.supervisor:
                try:
                    await blocking(self.supervisor.apply, self.state["defaults"])
                except ControlError:
                    pass
        return await self.get_state()
