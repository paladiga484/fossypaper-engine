"""The session we're drawing in: compositor, outputs, GPU, and who owns the background."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import tools


# --------------------------------------------------------------------------- #
#  GPU / EGL — the "red brick" fix
# --------------------------------------------------------------------------- #
def gpu_env(mode: str = "auto") -> dict:
    """EGL vendor env. 'auto' pairs NVIDIA's vendor with the NVIDIA node when the
    MUX routes the panel to the dGPU (else Mesa mis-drives it → garbage)."""
    if mode == "mesa":
        return {"__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/50_mesa.json",
                "__GLX_VENDOR_LIBRARY_NAME": "mesa"}
    if mode == "nvidia" or (mode == "auto" and _dgpu()):
        return {"__EGL_VENDOR_LIBRARY_FILENAMES": "/usr/share/glvnd/egl_vendor.d/10_nvidia.json",
                "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                "__GL_SYNC_TO_VBLANK": "1",      # kill the top-edge tear/ripple
                "__GL_MaxFramesAllowed": "1"}
    return {}


def _dgpu() -> bool:
    try:
        return bool(re.search(r"dgpu|ultimate|vfio",
                              subprocess.run(["supergfxctl", "-g"], capture_output=True,
                                             text=True, timeout=5).stdout, re.I))
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- #
#  Outputs
# --------------------------------------------------------------------------- #
def outputs() -> list[str]:
    """Ask the compositor first. DRM connector names and the names a compositor
    hands to layer-shell can disagree — a MUX switch renames the panel, and
    `--screen-root` only accepts the compositor's name."""
    for probe in (_outputs_hypr, _outputs_niri, _outputs_kde, _outputs_wlr, _outputs_drm):
        try:
            got = probe()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            continue
        if got:
            return got
    return []


def _outputs_hypr() -> list[str]:
    if not tools.which("hyprctl"):
        return []
    r = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=5)
    return [m["name"] for m in json.loads(r.stdout) if m.get("name")]


def _outputs_niri() -> list[str]:
    if not tools.which("niri"):
        return []
    r = subprocess.run(["niri", "msg", "-j", "outputs"], capture_output=True, text=True, timeout=5)
    data = json.loads(r.stdout)
    return sorted(data) if isinstance(data, dict) else [o["name"] for o in data]


def _outputs_kde() -> list[str]:
    if not tools.which("kscreen-doctor") or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return []
    r = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, text=True, timeout=5)
    return [o["name"] for o in json.loads(r.stdout).get("outputs", [])
            if o.get("name") and o.get("connected") and o.get("enabled")]


def compositor() -> str:
    """Which compositor we're drawing under, as far as the environment says."""
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("NIRI_SOCKET"):
        return "niri"
    desk = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    for name in ("kde", "gnome", "sway"):
        if name in desk:
            return name
    return desk or "unknown"


# linux-wallpaperengine finds fullscreen windows through wlr-foreign-toplevel /
# hyprland IPC; KWin and Mutter expose neither. That only matters when we're
# forced onto layer-shell there — the Plasma wallpaper watches the task
# manager itself, and a GNOME still has nothing to pause.
FULLSCREEN_BLIND = ("kde", "gnome")


def fullscreen_blind() -> bool:
    return compositor() in FULLSCREEN_BLIND and host() == "layer"


# --------------------------------------------------------------------------- #
#  Hosts — who actually owns the desktop background
# --------------------------------------------------------------------------- #
#   layer   wlr-layer-shell surface (Hyprland, niri, sway, river, …)
#   plasma  our own Plasma wallpaper type, configured over plasmashell's
#           scripting D-Bus call — Plasma paints its desktop over any
#           layer-shell "background", and under a "bottom" one we'd cover the
#           icons, so being registered is the only way to *be* the wallpaper
#   gnome   gsettings for stills; Hanabi for video when it's installed.
#           Mutter has no layer-shell at all, so nothing else can draw there
#   x11     the root window, for bare X11 window managers
HOSTS = ("auto", "layer", "plasma", "gnome", "x11")
_HOST: dict[str, str] = {}


def host(opts: dict | None = None) -> str:
    """Where the wallpaper goes on this desktop. An explicit `host` in the
    options wins, then `FOSSYPAPER_HOST`, then what the session looks like."""
    want = str((opts or {}).get("host") or "auto").lower()
    if want in HOSTS and want != "auto":
        return want
    env = os.environ.get("FOSSYPAPER_HOST", "").lower()
    if env in HOSTS and env != "auto":
        return env
    if opts is None:
        # the frontends ask per row per keypress; read the saved choice once
        if "cfg" not in _HOST:
            try:
                from .. import config
                _HOST["cfg"] = str(config.load().get("host") or "auto").lower()
            except Exception:
                _HOST["cfg"] = "auto"
        if _HOST["cfg"] in HOSTS and _HOST["cfg"] != "auto":
            return _HOST["cfg"]
    return detect_host()


def detect_host() -> str:
    comp = compositor()
    if comp == "kde":
        return "plasma"
    if comp == "gnome":
        return "gnome"
    if comp in ("hyprland", "niri", "sway"):
        return "layer"
    if (os.environ.get("XDG_SESSION_TYPE") == "x11"
            or (os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"))):
        return "x11"
    return "layer"


# Processes that draw their *own* backdrop on the background layer. With one
# running, a "background" wallpaper would fight it for the same slot, so
# `layer: auto` goes one up to "bottom" — above their backdrop, below windows.
# Noctalia 5 runs as its own `noctalia` binary; iNiR and older Noctalia are
# Quickshell (`qs`). Missing one means `auto` picks "background" and the
# wallpaper fights the shell's own backdrop for the same layer — and loses.
_BACKDROP_SHELLS = ("noctalia", "qs", "quickshell", "swaybg", "hyprpaper", "wpaperd", "wbg")
_BACKDROP_TTL = 3.0
_backdrop_cache: list = [0.0, ""]


def _running_comms() -> set[str]:
    """Every process name, in one pass over /proc — one `pgrep` per candidate
    was six forks per redraw of the TUI's details pane."""
    names = set()
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                names.add((d / "comm").read_text().strip())
            except OSError:
                continue
    return names


def backdrop_shell() -> str:
    now = time.monotonic()
    if now - _backdrop_cache[0] < _BACKDROP_TTL:
        return _backdrop_cache[1]
    comms = _running_comms()
    found = next((n for n in _BACKDROP_SHELLS if n in comms), "")
    _backdrop_cache[:] = [now, found]
    return found


def resolved_layer(opts: dict) -> str:
    layer = str(opts.get("layer") or "auto").lower()
    if layer != "auto":
        return layer
    return "bottom" if backdrop_shell() else "background"


def assets_dir(opts: dict) -> str:
    """Wallpaper Engine's own shared assets — scenes reference them by name."""
    if opts.get("assets_dir"):
        return str(opts["assets_dir"])
    for base in (Path.home() / ".local/share/Steam",
                 Path.home() / ".steam/steam",
                 Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"):
        d = base / "steamapps/common/wallpaper_engine/assets"
        if d.is_dir():
            return str(d)
    return ""


def _outputs_wlr() -> list[str]:
    if not tools.which("wlr-randr"):
        return []
    r = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=5)
    return [ln.split()[0] for ln in r.stdout.splitlines()
            if ln and not ln[0].isspace() and ln.split()]


def _outputs_drm() -> list[str]:
    return sorted(p.name.split("-", 1)[1] for p in Path("/sys/class/drm").glob("card*-*")
                  if (p / "status").is_file()
                  and (p / "status").read_text().strip() == "connected")


def target_outputs(opts: dict) -> list[str]:
    """Which screens this wallpaper goes on. An empty `output` means all of
    them — the common case on a laptop, and correct on a multi-head desk."""
    want = (opts.get("output") or "").strip()
    live = outputs()
    if want and (not live or want in live):
        return [want]
    # A pinned name the compositor doesn't have is almost always the laptop
    # panel after a MUX switch (eDP-1 on the dGPU, eDP-2 on the iGPU): the
    # renderer would refuse it outright. Draw on what's actually there.
    return live or ["eDP-1"]


def missing_output(opts: dict) -> str:
    """The pinned `output`, if the compositor no longer has it."""
    want = (opts.get("output") or "").strip()
    live = outputs()
    return want if want and live and want not in live else ""
