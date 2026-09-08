"""Shared settings for every fossypaper frontend (CLI · TUI · GUI)."""
import json
from pathlib import Path

CONFIG = Path.home() / ".config/fossypaper/config.json"
DEFAULTS = {
    "output": "eDP-1", "layer": "bottom", "fps": 30, "gpu": "auto",
    "silent": True, "volume": 15, "scaling": "",
    "no_particles": False, "no_parallax": False, "no_mouse": False,
    "current": "", "theme_backend": "builtin", "properties": {},
}
_RENDER_KEYS = ("output", "layer", "fps", "gpu", "silent", "volume",
                "scaling", "no_particles", "no_parallax", "no_mouse")


def load() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def opts(cfg: dict | None = None) -> dict:
    cfg = cfg or load()
    o = {k: cfg.get(k, DEFAULTS[k]) for k in _RENDER_KEYS}
    return o
