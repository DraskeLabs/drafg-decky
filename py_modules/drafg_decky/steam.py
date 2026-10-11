"""Find DraFG in the user's Steam libraries.

The plugin never downloads or bundles DraFG. It looks for the copy Steam
installed, through Steam's own library files: `libraryfolders.vdf` lists the
library folders, and each installed app has an `appmanifest_<appid>.acf` that
names its install directory.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterator

# DraFG's public Steam app ids: the store release first, then the Playtest.
APP_IDS = (4934180, 5085100)

# The executable inside the install directory.
EXECUTABLE = "drafg"

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Parse Valve's text KeyValues format into nested dicts.

    Only what library and manifest files use: quoted keys, quoted values and
    braces. Comments (`//`) are skipped; duplicate keys keep the last value.
    """
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    tokens: list[str | None] = []
    for match in _TOKEN.finditer(text):
        quoted, brace = match.groups()
        tokens.append(brace if brace else quoted.replace('\\"', '"').replace("\\\\", "\\"))
    root: dict = {}
    stack = [root]
    key: str | None = None
    for token in tokens:
        if token == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif token == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = token
        else:
            stack[-1][key] = token
            key = None
    return root


def steam_roots(home: str) -> list[str]:
    """Steam's install directories for this user, existing ones only."""
    candidates = [
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
    ]
    roots: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        real = os.path.realpath(path)
        if os.path.isdir(real) and real not in seen:
            seen.add(real)
            roots.append(real)
    return roots


def library_folders(home: str) -> Iterator[str]:
    """Every Steam library folder the user has, the main one first."""
    seen: set[str] = set()
    for root in steam_roots(home):
        if root not in seen:
            seen.add(root)
            yield root
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                data = parse_vdf(f.read())
        except OSError:
            continue
        folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
        for value in folders.values():
            path = value.get("path") if isinstance(value, dict) else value
            if isinstance(path, str):
                real = os.path.realpath(path)
                if real not in seen and os.path.isdir(real):
                    seen.add(real)
                    yield real


@dataclass(frozen=True)
class Install:
    app_id: int
    directory: str
    executable: str


def find_drafg(home: str) -> Install | None:
    """The installed DraFG to start, preferring the store release."""
    for app_id in APP_IDS:
        for library in library_folders(home):
            manifest = os.path.join(library, "steamapps", f"appmanifest_{app_id}.acf")
            try:
                with open(manifest, encoding="utf-8", errors="replace") as f:
                    state = parse_vdf(f.read()).get("AppState", {})
            except OSError:
                continue
            installdir = state.get("installdir")
            if not isinstance(installdir, str) or not installdir or "/" in installdir:
                continue
            directory = os.path.join(library, "steamapps", "common", installdir)
            executable = os.path.join(directory, EXECUTABLE)
            if os.path.isfile(executable) and os.access(executable, os.X_OK):
                return Install(app_id, directory, executable)
    return None
