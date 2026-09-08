"""Shared settings for every fossypaper frontend (CLI · TUI · GUI · plugin).

One JSON file, one schema. Unknown keys survive a round-trip, so a newer
frontend writing a key an older one doesn't know about never loses it.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

CONFIG = Path(os.environ.get("FOSSYPAPER_CONFIG")
              or Path.home() / ".config/fossypaper/config.json")

DEFAULTS = {
    # --- render ---------------------------------------------------------- #
    "output": "",              # "" = every connected output
    "span": [],                # outputs to stretch one wallpaper across
    "layer": "bottom",
    "fps": 30,
    "gpu": "auto",
    "scaling": "",             # "" | default | stretch | fit | fill
    "clamp": "",               # "" | clamp | border | repeat
    "assets_dir": "",
    # --- audio ----------------------------------------------------------- #
    "silent": True,
    "volume": 15,
    "no_automute": False,
    # auto  = process audio only for wallpapers that declare they react to it
    # always = always process (WE's own behaviour)
    # never  = never process
    "audio_processing": "auto",
    # --- effects --------------------------------------------------------- #
    "no_particles": False,
    "no_parallax": False,
    "no_mouse": False,
    # --- battery / games ------------------------------------------------- #
    "fullscreen_pause": True,          # stop rendering behind a fullscreen app
    "pause_only_active": False,        # …only when that window has focus
    "pause_ignore_appids": [],
    # --- state ----------------------------------------------------------- #
    "current": "",
    "properties": {},          # {wallpaper id: {key: value}}
    "theme_backend": "builtin",
    "theme_on_apply": False,   # re-theme the shells every time you switch
    # --- browser --------------------------------------------------------- #
    "wallhaven_key": "",       # only needed for non-SFW results
    "wallhaven_purity": "sfw",
    "wallhaven_sort": "",        # "" = toplist when browsing, relevance when searching
    "steam_login": "",         # the account that owns Wallpaper Engine
    # --- appearance (GUI) ------------------------------------------------ #
    "ui_theme": "obsidian",
    "ui_accent": "",           # "" = the theme's own accent
    "ui_card_width": 224,
    "ui_font": "",
    "ui_follow_wallpaper": False,   # recolour the GUI from the wallpaper palette
}

_RENDER_KEYS = ("output", "span", "layer", "fps", "gpu", "scaling", "clamp", "assets_dir",
                "silent", "volume", "no_automute", "audio_processing",
                "no_particles", "no_parallax", "no_mouse",
                "fullscreen_pause", "pause_only_active", "pause_ignore_appids")


def load() -> dict:
    try:
        stored = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(stored, dict):
        return dict(DEFAULTS)
    return _migrate({**DEFAULTS, **stored})


def _migrate(cfg: dict) -> dict:
    """Carry older configs forward rather than silently resetting them."""
    old = cfg.pop("no_audio_processing", None)
    if old is not None and "audio_processing" not in (cfg.get("_seen") or ()):
        cfg["audio_processing"] = "never" if old else "auto"
    if isinstance(cfg.get("span"), str):
        cfg["span"] = [s.strip() for s in cfg["span"].split(",") if s.strip()]
    return cfg


def save(cfg: dict) -> None:
    """Atomic: a crash mid-write must not leave an unreadable config."""
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CONFIG.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def opts(cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else load()
    return {k: cfg.get(k, DEFAULTS[k]) for k in _RENDER_KEYS}


def coerce(key: str, raw: str):
    """Parse a CLI `key=value` against the schema, so `--set fps=60` gives an
    int and `--set span=DP-1,HDMI-A-1` gives a list."""
    ref = DEFAULTS.get(key)
    if isinstance(ref, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(ref, int) and not isinstance(ref, bool):
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"{key} wants a number, got {raw!r}")
    if isinstance(ref, list):
        return [p.strip() for p in raw.split(",") if p.strip()]
    if isinstance(ref, dict):
        raise ValueError(f"{key} is structured — set it from the GUI or TUI")
    return raw
