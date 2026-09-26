"""Bare X11 window managers: the root window (xwallpaper/feh, xwinwrap + mpv)."""
from __future__ import annotations

import os
import subprocess

from . import library, render, session, tools


# ---- X11 (bare window managers — untested) ---------------------------------- #
_X11_PID = tools.STATE / "x11-video.pgid"


def _x11_still_tool() -> str | None:
    return tools.which("xwallpaper") or tools.which("feh")


def _x11_can(wp: library.Wallpaper) -> bool:
    k = library.kind_of(wp)
    if k == "scene":
        return tools.have_renderer()
    if k == "video":
        return bool(tools.which("xwinwrap") and tools.which("mpv"))
    return bool(_x11_still_tool())


def _x11_needs(wp: library.Wallpaper) -> str:
    return {"scene": "linux-wallpaperengine", "video": "xwinwrap + mpv"}.get(
        library.kind_of(wp), "xwallpaper or feh")


def _start_x11_still(wp: library.Wallpaper, opts: dict):
    tool = _x11_still_tool()
    mode = opts.get("scaling", "")
    if tool and tool.endswith("xwallpaper"):
        flag = {"fit": "--maximize", "stretch": "--stretch"}.get(mode, "--zoom")
        subprocess.run([tool, flag, str(wp.entry)], capture_output=True, timeout=10)
    elif tool:
        flag = {"fit": "--bg-max", "stretch": "--bg-scale"}.get(mode, "--bg-fill")
        subprocess.run([tool, "--no-fehbg", flag, str(wp.entry)], capture_output=True, timeout=10)


def _start_x11_video(wp: library.Wallpaper, opts: dict):
    """xwinwrap makes a desktop-type window under everything; mpv draws in it."""
    mo = ["--loop-file=inf", "--no-osc", "--no-input-default-bindings", "--hwdec=auto-safe",
          "--no-audio" if opts.get("silent", True) else f"--volume={opts.get('volume', 15)}"]
    if opts.get("scaling") == "fill":
        mo.append("--panscan=1.0")
    elif opts.get("scaling") == "stretch":
        mo.append("--keepaspect=no")
    argv = ["xwinwrap", "-fs", "-ov", "-ni", "-nf", "-un", "-s", "-st", "-sp", "-b", "-fdt",
            "--", "mpv", "--wid=%WID", *mo, str(wp.entry)]
    env = dict(os.environ); env.update(session.gpu_env(opts.get("gpu", "auto")))
    render._spawn(argv, env)
    tools.STATE.mkdir(parents=True, exist_ok=True)
    _X11_PID.write_text(str(render._spawned[-1].pid))


def _x11_video_alive() -> bool:
    try:
        os.killpg(int(_X11_PID.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def _x11_video_stop() -> None:
    import signal
    try:
        os.killpg(int(_X11_PID.read_text()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    _X11_PID.unlink(missing_ok=True)
