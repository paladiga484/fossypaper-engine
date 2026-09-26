"""Routing a wallpaper to a renderer, and starting / stopping it."""
from __future__ import annotations

import os
import subprocess
import time

from . import gnome, library, plasma, session, tools, x11


# --------------------------------------------------------------------------- #
#  Backend routing
# --------------------------------------------------------------------------- #
_MANAGED = ("linux-wallpaper", "mpvpaper")     # process names we own (comm is 15 chars)

_UNSUPPORTED = {
    "web": "HTML wallpaper — no Linux renderer runs these, and fossypaper will not "
           "start a local web server to fake one",
    "application": "executable wallpaper — a Windows .exe, not something to run here",
    "missing_assets": "no renderable file in this wallpaper's folder — looks like a "
                       "partial or corrupted Workshop download; unsubscribe and "
                       "resubscribe in Steam to force a fresh copy",
    "missing_dependency": "a preset with no base wallpaper to render",
}


def backend_of(wid: str) -> tuple[str, bool]:
    """(backend, usable): how this wallpaper renders, and whether its tool is here.

    mpvpaper=video, swww/mpvpaper=still, wpe=WE scene.
    """
    wp = library.find(wid)
    if wp is None:
        return "wpe", tools.have_renderer()
    return backend_for(wp)


def backend_for(wp: library.Wallpaper) -> tuple[str, bool]:
    """(backend, usable here). The backend names the *kind* of renderer a
    wallpaper needs; whether it's usable depends on the desktop too — Plasma
    and GNOME draw it themselves, so the layer-shell tools don't matter there."""
    if wp.type in _UNSUPPORTED:
        return wp.type, False
    be, ok = _layer_backend_for(wp)
    h = session.host()
    if h in ("plasma", "gnome"):
        return be, True           # always something to show: live, or its still
    if h == "x11":
        return be, x11._x11_can(wp)
    return be, ok


def _layer_backend_for(wp: library.Wallpaper) -> tuple[str, bool]:
    if wp.entry is not None and wp.entry.suffix.lower() in library._VIDEO_EXT:
        return "mpvpaper", bool(tools.which("mpvpaper"))
    if wp.entry is not None and wp.entry.suffix.lower() == ".pkg":
        return "wpe", tools.have_renderer()
    if wp.entry is not None and wp.entry.suffix.lower() in library._IMAGE_EXT:
        be = "swww" if tools.which("swww") else "mpvpaper"
        return be, bool(tools.which(be))
    return "wpe", tools.have_renderer()


def wants_audio(wp: library.Wallpaper, opts: dict) -> bool:
    """Should the renderer capture and analyse system audio for this wallpaper?

    `auto` — the default — says yes only when the wallpaper's own project.json
    declares `supportsaudioprocessing`. Most don't, and for those the capture is
    pure cost: a monitor stream held open for a wallpaper that ignores it.
    """
    mode = str(opts.get("audio_processing", "auto")).lower()
    if mode in ("never", "off", "false"):
        return False
    if mode in ("always", "on", "true"):
        return True
    return bool(wp.audio)


def why_unsupported(wp: library.Wallpaper) -> str:
    if wp.type == "missing_dependency" and wp.depends_on:
        return (f"needs workshop item {wp.depends_on} as its base — subscribe to it "
                f"in Steam Workshop, then rescan")
    return _UNSUPPORTED.get(wp.type, "")


def render_note(wp: library.Wallpaper) -> str:
    """A heads-up for a wallpaper that *does* render but not completely —
    distinct from `why_unsupported`, which is for one that draws nothing."""
    if wp.skipped_textures:
        keys = ", ".join(wp.skipped_textures)
        return (f"renders via its base wallpaper ({wp.render_id}); this preset's own "
                f"custom image slot(s) — {keys} — stay blank, since "
                f"linux-wallpaperengine can't load an external file as a scene texture")
    return ""


def backends_status() -> dict:
    return {"wpe": tools.have_renderer(),
            "mpvpaper": bool(tools.which("mpvpaper")),
            "swww": bool(tools.which("swww"))}


def is_running() -> bool:
    # the markers outlive a session: a Plasma wallpaper set yesterday says
    # nothing about whether anything is drawing under Hyprland today
    h = session.host()
    if (h == "plasma" and plasma._PLASMA_PREV.is_file()) or (h == "gnome" and gnome._GNOME_PREV.is_file()):
        return True
    return any(subprocess.run(["pgrep", "-x", c], capture_output=True).stdout.strip()
               for c in _MANAGED) or x11._x11_video_alive()


def stop(keep: str = ""):
    """Take the wallpaper down on every host we might have put it on.
    `keep` names a host we're about to re-apply on — don't restore the
    desktop's old wallpaper only to replace it a moment later."""
    for c in _MANAGED:
        subprocess.run(["pkill", "-x", c], capture_output=True)
    if tools.which("swww"):
        try:
            subprocess.run(["swww", "clear"], capture_output=True, timeout=5)
        except subprocess.SubprocessError:
            pass
    x11._x11_video_stop()
    if keep != "plasma":
        plasma._plasma_restore()
    if keep != "gnome":
        gnome._gnome_restore()


# --------------------------------------------------------------------------- #
#  linux-wallpaperengine
# --------------------------------------------------------------------------- #
def build_argv(wid: str, opts: dict) -> list[str]:
    """Every render knob the renderer exposes, in the order it wants them:
    per-output flags follow the `--screen-root` / `--screen-span` they modify."""
    a = [tools.BIN]
    span = [s for s in (opts.get("span") or []) if s]
    if span:
        a += ["--screen-span", ",".join(span), "--bg", wid]
        a += _per_output(opts)
    else:
        for out in session.target_outputs(opts):
            a += ["--screen-root", out, "--bg", wid]
            a += _per_output(opts)

    if session.host(opts) != "x11":
        a += ["--layer", session.resolved_layer(opts)]
    a += ["--fps", str(opts.get("fps") or 30)]

    if opts.get("silent", True):
        a += ["--silent"]
    else:
        a += ["--volume", str(opts.get("volume", 15))]
        if opts.get("no_automute"):
            a += ["--noautomute"]
    if opts.get("no_audio_processing"):
        # Audio *processing* is separate from audio output: --silent only mutes.
        # The capture streams it opens on the sink monitor stay live, pile up on
        # every reapply, and contend with anything else asking the graph for a
        # low-latency quantum. Off unless the wallpaper actually reacts to sound.
        a += ["--no-audio-processing"]

    if opts.get("no_particles"):
        a += ["--disable-particles"]
    if opts.get("no_parallax"):
        a += ["--disable-parallax"]
    if opts.get("no_mouse"):
        a += ["--disable-mouse"]

    # Leaving a scene rendering behind a fullscreen game costs frames the game
    # wants. Pausing is the default; these switches loosen it.
    if not opts.get("fullscreen_pause", True):
        a += ["--no-fullscreen-pause"]
    elif opts.get("pause_only_active"):
        a += ["--fullscreen-pause-only-active"]
    for appid in (opts.get("pause_ignore_appids") or []):
        if appid:
            a += ["--fullscreen-pause-ignore-appid", str(appid)]

    if opts.get("assets_dir"):
        a += ["--assets-dir", str(opts["assets_dir"])]

    for k, v in (opts.get("properties") or {}).items():
        a += ["--set-property", f"{k}={v}"]
    return a


def _per_output(opts: dict) -> list[str]:
    """--scaling and --clamp bind to the screen named just before them."""
    a = []
    if opts.get("scaling"):
        a += ["--scaling", opts["scaling"]]
    if opts.get("clamp"):
        a += ["--clamp", opts["clamp"]]
    return a


def start(wid: str, opts: dict) -> tuple[bool, str]:
    """Apply a wallpaper through whichever backend can render it."""
    wp = library.find(wid)
    if wp is None:
        return False, f"no wallpaper with id {wid} in any library root"
    be, ok = backend_for(wp)
    if be in _UNSUPPORTED:
        return False, why_unsupported(wp) or _UNSUPPORTED[be]
    h = session.host(opts)
    if not ok:
        if h == "x11":
            return False, f"this one needs {x11._x11_needs(wp)} on X11, which isn't installed"
        return False, f"this one renders via {be}, which isn't installed — paru -S {be}"
    stop(keep=h)
    tools.STATE.mkdir(parents=True, exist_ok=True)
    RENDER_LOG.write_text("")
    _spawned.clear()
    opts = {**opts, "no_audio_processing": not wants_audio(wp, opts)}
    opts = {**opts, "properties": library.effective_properties(wp, opts)}
    if h == "plasma":
        return plasma._start_plasma(wp, opts)
    if h == "gnome":
        return gnome._start_gnome(wp, opts)
    try:
        if h == "x11":
            {"video": x11._start_x11_video, "image": x11._start_x11_still,
             "scene": _start_wpe}[library.kind_of(wp)](wp, opts)
        else:
            {"mpvpaper": _start_mpvpaper, "swww": _start_swww, "wpe": _start_wpe}[be](wp, opts)
    except OSError as e:
        return False, f"{be} wouldn't start: {e}"
    dead = _died_early(float(opts.get("verify_grace", 1.5)))
    if dead is not None:
        why = "; ".join(render_log_tail(2)) or f"exit code {dead.returncode}"
        return False, f"{be} exited straight away — {why} (full log: {RENDER_LOG})"
    gone = session.missing_output(opts)
    if gone:
        return True, f"applied via {be} — {gone} isn't connected (MUX switch?), so it's on every screen"
    return True, f"applied via {be}"


RENDER_LOG = tools.STATE / "renderer.log"
_spawned: list = []


def _spawn(argv, env=None):
    """Start a renderer detached, with its output in RENDER_LOG (rewritten on
    every apply) — a renderer that dies on its first frame used to vanish into
    /dev/null while the app said "applied"."""
    tools.STATE.mkdir(parents=True, exist_ok=True)
    with open(RENDER_LOG, "a", encoding="utf-8") as log:
        log.write("$ " + " ".join(argv) + "\n")
        log.flush()
        _spawned.append(subprocess.Popen(argv, env=env, stdout=log, stderr=log,
                                         stdin=subprocess.DEVNULL, start_new_session=True))


def _died_early(grace: float) -> subprocess.Popen | None:
    """The first renderer that exits within `grace` seconds, if any."""
    deadline = time.monotonic() + grace
    while _spawned and time.monotonic() < deadline:
        for proc in _spawned:
            if proc.poll() is not None:
                return proc
        time.sleep(0.1)
    return None


def render_log_tail(n: int = 4) -> list[str]:
    """The last few lines worth reading, noise filtered out."""
    try:
        lines = RENDER_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    keep = [ln.strip() for ln in lines
            if ln.strip() and not ln.startswith(("$ ", "Resolving require module"))]
    return keep[-n:]


def _start_wpe(wp: library.Wallpaper, opts: dict):
    env = dict(os.environ); env.update(session.gpu_env(opts.get("gpu", "auto")))
    env.setdefault("WALLPAPER_ENGINE_ASSETS", str(opts.get("assets_dir") or ""))
    _spawn(build_argv(wp.render_id, opts), env)


def _start_mpvpaper(wp: library.Wallpaper, opts: dict):
    """Video, and stills too when swww isn't installed — mpv holds a single
    frame perfectly well, and that beats taking a dependency for it."""
    env = dict(os.environ); env.update(session.gpu_env(opts.get("gpu", "auto")))
    still = wp.entry is not None and wp.entry.suffix.lower() in library._IMAGE_EXT
    mo = ["loop-file=inf", "hwdec=auto-safe", "vo=gpu", "profile=low-latency",
          f"video-sync={'display-resample' if not still else 'audio'}"]
    if still:
        # one frame, held. An fps cap on a static image only costs wakeups.
        mo = ["loop-file=inf", "vo=gpu", "image-display-duration=inf", "no-audio"]
    else:
        mo.append("no-audio" if opts.get("silent", True)
                  else f"volume={opts.get('volume', 15)}")
        if opts.get("fps"):
            mo.append(f"override-display-fps={opts['fps']}")

    # `scaling` is a global render setting, so it has to mean the same thing on
    # this backend as it does on the scene one. mpv spells it differently:
    # panscan zooms until the frame fills and crops the overflow, and dropping
    # keepaspect is the only way to actually distort.
    scale = opts.get("scaling", "")
    if scale == "fill":
        mo.append("panscan=1.0")
    elif scale == "fit":
        mo.append("panscan=0.0")
    elif scale == "stretch":
        mo.append("keepaspect=no")

    base = ["mpvpaper", "-l", session.resolved_layer(opts), "-o", " ".join(mo)]
    if opts.get("fullscreen_pause", True):
        # -p pauses seamlessly; -a FULL extends that to any fullscreen window.
        base = base[:1] + ["-p", "-a", "FULL"] + base[1:]
    for out in session.target_outputs(opts):
        _spawn(base + [out, str(wp.entry)], env)


def _start_swww(wp: library.Wallpaper, opts: dict):
    if subprocess.run(["pgrep", "-x", "swww-daemon"], capture_output=True).returncode != 0:
        _spawn(["swww-daemon"])
        time.sleep(0.6)
    fit = {"fill": "fill", "fit": "fit", "stretch": "stretch",
           "default": "crop", "": "crop"}.get(opts.get("scaling", ""), "crop")
    subprocess.run(["swww", "img", "--outputs", ",".join(session.target_outputs(opts)),
                    "--resize", fit, "--transition-type", "fade", str(wp.entry)],
                   capture_output=True)
