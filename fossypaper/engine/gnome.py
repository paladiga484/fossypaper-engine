"""GNOME: gsettings for stills, Hanabi for video. Mutter has no layer-shell."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import library, media, tools


# ---- GNOME ------------------------------------------------------------------ #
_GNOME_PREV = tools.STATE / "gnome-previous.json"
_GNOME_BG = "org.gnome.desktop.background"
_HANABI = "io.github.jeffshee.hanabi-extension"


def _gsettings(*args: str) -> tuple[bool, str]:
    if not tools.which("gsettings"):
        return False, "gsettings isn't installed"
    try:
        r = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def _gv(text: str) -> str:
    """A string as GVariant text, so gsettings never has to guess."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def hanabi_available() -> bool:
    """Hanabi is the GNOME extension that plays a video as the wallpaper. Only
    use it if its schema *and* the key we write are really there."""
    ok, keys = _gsettings("list-keys", _HANABI)
    return ok and "video-path" in keys.split()


def _gnome_save_previous() -> None:
    if _GNOME_PREV.is_file():
        return
    prev = {}
    for key in ("picture-uri", "picture-uri-dark", "picture-options"):
        ok, v = _gsettings("get", _GNOME_BG, key)
        if ok:
            prev[key] = v
    if hanabi_available():
        ok, v = _gsettings("get", _HANABI, "video-path")
        if ok:
            prev["hanabi:video-path"] = v
    tools.STATE.mkdir(parents=True, exist_ok=True)
    _GNOME_PREV.write_text(json.dumps(prev))


def _gnome_set_picture(path: Path, opts: dict) -> tuple[bool, str]:
    uri = path.resolve().as_uri()
    option = {"fit": "scaled", "stretch": "stretched"}.get(opts.get("scaling", ""), "zoom")
    for key, val in (("picture-uri", _gv(uri)), ("picture-uri-dark", _gv(uri)),
                     ("picture-options", _gv(option))):
        ok, err = _gsettings("set", _GNOME_BG, key, val)
        if not ok:
            return False, err
    return True, ""


def _start_gnome(wp: library.Wallpaper, opts: dict) -> tuple[bool, str]:
    _gnome_save_previous()
    kind = library.kind_of(wp)
    video = wp.entry if kind == "video" else \
        (library.scene_video(wp) if kind == "scene" and opts.get("scene_video", True) else None)
    if video and hanabi_available():
        ok, err = _gsettings("set", _HANABI, "video-path", _gv(str(video)))
        if ok:
            return True, "applied via Hanabi"
    if hanabi_available():
        # a video left in Hanabi keeps playing on top of whatever we set next
        _gsettings("set", _HANABI, "video-path", "''")
    pic = wp.entry if kind == "image" else media._still_for(wp, opts)
    if pic is None:
        return False, "couldn't produce a still frame for GNOME to show"
    ok, err = _gnome_set_picture(pic, opts)
    if not ok:
        return False, f"gsettings refused the wallpaper — {err}"
    if kind == "image":
        return True, "applied as the GNOME background"
    why = "GNOME has no layer-shell, so scenes can't animate there" if kind == "scene" \
        else "install the Hanabi extension to play videos"
    return True, f"applied as a still frame — {why}"


def _gnome_restore() -> None:
    if not _GNOME_PREV.is_file():
        return
    try:
        prev = json.loads(_GNOME_PREV.read_text() or "{}")
    except ValueError:
        prev = {}
    if hanabi_available() and "hanabi:video-path" not in prev:
        _gsettings("set", _HANABI, "video-path", "''")
    for key, val in prev.items():
        # gsettings get prints GVariant text ('file:///…'); set parses the same
        schema, _, key = key.rpartition(":")
        _gsettings("set", _HANABI if schema == "hanabi" else _GNOME_BG, key, val)
    _GNOME_PREV.unlink(missing_ok=True)
