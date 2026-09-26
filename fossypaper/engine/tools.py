"""External tools and the state directory everything else writes into."""
from __future__ import annotations

import shutil
from pathlib import Path


STATE = Path.home() / ".local/state/fossypaper"

# PATH does not change under a running process, and the TUI asks "is mpvpaper
# installed?" once per visible row per keypress. Look each name up once.
_WHICH: dict[str, str | None] = {}


def which(name: str) -> str | None:
    if name not in _WHICH:
        _WHICH[name] = shutil.which(name)
    return _WHICH[name]


BIN = which("linux-wallpaperengine") or "linux-wallpaperengine"


def have_renderer() -> bool:
    return which("linux-wallpaperengine") is not None
