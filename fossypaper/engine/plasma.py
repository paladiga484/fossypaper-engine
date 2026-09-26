"""KDE Plasma: our own registered wallpaper type, driven over plasmashell's D-Bus."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import library, media, session, tools


# ---- Plasma ----------------------------------------------------------------- #
PLASMA_PLUGIN = "org.fossypaper.wallpaper"
_PLASMA_PREV = tools.STATE / "plasma-previous.json"
_SCENE_MODULE = "com/github/catsout/wallpaperEngineKde"


def plasma_plugin_installed() -> bool:
    return any((d / PLASMA_PLUGIN / "metadata.json").is_file() for d in (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "plasma/wallpapers",
        Path("/usr/share/plasma/wallpapers")))


def plasma_scene_module() -> Path | None:
    """catsout's compiled SceneViewer, if installed. We load *only* that QML
    type — never its wallpaper plugin, whose helper listens on a WebSocket."""
    roots = [Path(p) for p in (os.environ.get("QML_IMPORT_PATH") or "").split(":") if p]
    roots += [Path("/usr/lib/qt6/qml"), Path("/usr/lib64/qt6/qml"),
              Path("/usr/lib/x86_64-linux-gnu/qt6/qml")]
    for r in roots:
        if (r / _SCENE_MODULE / "qmldir").is_file():
            return r / _SCENE_MODULE
    return None


_PREFLIGHT = tools.STATE / "plasma-preflight.json"
_PREFLIGHT_QML = Path(__file__).resolve().parent.parent / "qml" / "preflight.qml"


def plasma_preflight(wp: library.Wallpaper, opts: dict, timeout: float = 25.0) -> tuple[bool, str]:
    """Would this scene wedge plasmashell? Load it once in a throwaway nested
    KWin with the same SceneViewer, off the user's screen. Some scenes never
    finish loading in that renderer, and then its teardown deadlocks — inside
    plasmashell that is a frozen desktop, garbage GPU memory on the wallpaper,
    and a shell that ignores SIGTERM. The verdict is cached per package."""
    rw = library.find(wp.render_id) or wp
    pkg = rw.entry
    qml = tools.which("qml6") or tools.which("qml") or ("/usr/lib/qt6/bin/qml"
                                             if Path("/usr/lib/qt6/bin/qml").is_file() else None)
    kwin = tools.which("kwin_wayland")
    if pkg is None or not qml or not kwin:
        return True, "preflight unavailable"
    key = f"{rw.id}:{int(pkg.stat().st_mtime)}:{json.dumps(opts.get('properties') or {}, sort_keys=True)}"
    try:
        cache = json.loads(_PREFLIGHT.read_text())
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key]["ok"], cache[key]["why"]

    sock = f"fossypaper-preflight-{os.getpid()}-{time.monotonic_ns()}"
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    comp = subprocess.Popen([kwin, "--virtual", "--width", "640", "--height", "360",
                             "--socket", sock, "--no-lockscreen"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    ok, why = True, ""
    try:
        for _ in range(50):
            if (runtime / sock).exists():
                break
            time.sleep(0.1)
        else:
            return True, "preflight compositor didn't start"
        env = {**os.environ, "WAYLAND_DISPLAY": sock, "QT_QPA_PLATFORM": "wayland"}
        props = json.dumps({k: _typed(v) for k, v in (opts.get("properties") or {}).items()})
        proc = subprocess.Popen([qml, str(_PREFLIGHT_QML), "--", props, pkg.resolve().as_uri(),
                                 Path(session.assets_dir(opts)).resolve().as_uri()],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, errors="replace",
                                start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            ok, why = False, "the Plasma scene renderer hangs on it"
        else:
            if "Failed to create wl_display" in out or "Could not load the Qt platform" in out:
                return True, "preflight couldn't reach its compositor"   # not a verdict: don't cache
            if proc.returncode != 0:
                ok, why = False, f"the Plasma scene renderer crashes on it (exit {proc.returncode})"
        if ok and f"scene '{rw.id}'" not in out and "scene '" not in out:
            ok, why = False, "the Plasma scene renderer never finishes loading it"
    finally:
        comp.kill()
        comp.wait()
    cache[key] = {"ok": ok, "why": why}
    tools.STATE.mkdir(parents=True, exist_ok=True)
    _PREFLIGHT.write_text(json.dumps(cache, indent=1))
    return ok, why


def _qdbus() -> str | None:
    return tools.which("qdbus6") or tools.which("qdbus")


def plasma_eval(script: str) -> tuple[bool, str]:
    """Run a script in plasmashell. Its print() output comes back on stdout;
    a locked desktop or a syntax error comes back as a D-Bus error."""
    q = _qdbus()
    if not q:
        return False, "qdbus6 isn't installed (qt6-tools)"
    try:
        r = subprocess.run([q, "org.kde.plasmashell", "/PlasmaShell",
                            "org.kde.PlasmaShell.evaluateScript", script],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip() or f"exit {r.returncode}"
    return True, r.stdout.strip()


def plasma_config(wp: library.Wallpaper, opts: dict, still: Path | None,
                  video: Path | None = None) -> dict:
    return {
        "Kind": "video" if video else library.kind_of(wp),
        # file:// URLs, percent-encoded here — a title with `#` or `%` in its
        # path would break a URL glued together in QML
        "Source": (video.resolve().as_uri() if video else
                   wp.entry.resolve().as_uri() if wp.entry is not None else ""),
        "Still": still.resolve().as_uri() if still else "",
        "Assets": Path(session.assets_dir(opts)).resolve().as_uri() if session.assets_dir(opts) else "",
        "Title": wp.title,
        "Fit": {"fit": "fit", "stretch": "stretch"}.get(opts.get("scaling", ""), "fill"),
        "Fps": int(opts.get("fps") or 30),
        "Muted": bool(opts.get("silent", True)),
        "Volume": int(opts.get("volume", 15)),
        "FullscreenPause": bool(opts.get("fullscreen_pause", True)),
        "PauseOnlyActive": bool(opts.get("pause_only_active", False)),
        "Mouse": not opts.get("no_mouse", False),
        # the renderer's static-pass cache feeds effect chains their own last
        # frame (see scene_has_effects); keep it only where there's no chain
        "CachePasses": library.kind_of(wp) == "scene" and not video and not library.scene_has_effects(wp),
        "Props": json.dumps({k: _typed(v) for k, v in (opts.get("properties") or {}).items()},
                            ensure_ascii=False),
    }


def _typed(v):
    """fossypaper keeps knob values as WE's wire strings; the scene renderer
    wants JSON types ("true" -> true, "0.5" -> 0.5, colours stay strings)."""
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    try:
        return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
    except ValueError:
        return s


def plasma_script(cfg: dict) -> str:
    """Point every desktop at our wallpaper type, remembering what each one
    had so `stop()` can put it back. Plasma keeps each type's own settings in
    its own config group, so restoring the type name restores the lot."""
    writes = "".join(f"  d.writeConfig({json.dumps(k)}, {json.dumps(v)});\n"
                     for k, v in cfg.items())
    return ("var prev = {};\n"
            "desktops().forEach(function (d) {\n"
            f"  if (d.wallpaperPlugin !== {json.dumps(PLASMA_PLUGIN)})\n"
            "    prev[d.id] = d.wallpaperPlugin;\n"
            f"  d.wallpaperPlugin = {json.dumps(PLASMA_PLUGIN)};\n"
            f"  d.currentConfigGroup = ['Wallpaper', {json.dumps(PLASMA_PLUGIN)}, 'General'];\n"
            f"{writes}"
            "  d.reloadConfig();\n"
            "});\n"
            "print(JSON.stringify(prev));\n")


def _start_plasma(wp: library.Wallpaper, opts: dict) -> tuple[bool, str]:
    if not plasma_plugin_installed():
        return False, ("the fossypaper Plasma wallpaper isn't installed — run ./install.sh, "
                       "then `fossypaper apply` again")
    kind = library.kind_of(wp)
    video = library.scene_video(wp) if kind == "scene" and opts.get("scene_video", True) else None
    live_scene = kind == "scene" and not video and plasma_scene_module() is not None \
        and session.assets_dir(opts)
    unsafe = ""
    if live_scene and opts.get("plasma_preflight", True):
        safe, unsafe = plasma_preflight(wp, opts)
        if safe:
            unsafe = ""
        else:
            live_scene = False
    still = media._still_for(wp, opts) if kind == "scene" and not live_scene and not video else None
    ok, out = plasma_eval(plasma_script(plasma_config(wp, opts, still, video)))
    if not ok:
        return False, f"plasmashell refused the wallpaper — {out} (are the widgets locked?)"
    try:
        prev = json.loads(out.splitlines()[-1]) if out else {}
    except ValueError:
        prev = {}
    if prev and not _PLASMA_PREV.is_file():
        tools.STATE.mkdir(parents=True, exist_ok=True)
        _PLASMA_PREV.write_text(json.dumps(prev))
    elif not _PLASMA_PREV.is_file():
        _PLASMA_PREV.write_text("{}")
    notes = []
    if video:
        notes.append("playing its video layer; the scene's other layers are skipped on Plasma")
    if unsafe:
        notes.append(f"still frame only: {unsafe}, and on your desktop that freezes plasmashell")
    elif kind == "scene" and not live_scene and not video:
        notes.append("still frame only: " + (
            "no Wallpaper Engine assets folder found" if plasma_scene_module()
            else "no native scene renderer (optional: AUR wallpaper-engine-kde-plugin-git)"))
    return True, "applied as a Plasma wallpaper" + "".join(" — " + n for n in notes)


def _bus_has(name: str) -> bool:
    """Is `name` owned on the session bus right now? Asking a name that isn't
    there is how D-Bus activation starts things — never poke plasmashell from
    a Hyprland or niri session just to find out."""
    try:
        r = subprocess.run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.DBus",
                            "/org/freedesktop/DBus", "org.freedesktop.DBus.NameHasOwner",
                            f"string:{name}"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "boolean true" in r.stdout


def _plasma_restore() -> None:
    # the marker outlives the session: leave it for the next Plasma login
    if not _PLASMA_PREV.is_file() or not _bus_has("org.kde.plasmashell"):
        return
    try:
        prev = json.loads(_PLASMA_PREV.read_text() or "{}")
    except ValueError:
        prev = {}
    fallback = "org.kde.image"
    script = ("var prev = " + json.dumps(prev) + ";\n"
              "desktops().forEach(function (d) {\n"
              f"  if (d.wallpaperPlugin === {json.dumps(PLASMA_PLUGIN)})\n"
              f"    d.wallpaperPlugin = prev[d.id] || {json.dumps(fallback)};\n"
              "});\n")
    ok, _ = plasma_eval(script)
    if ok:
        _PLASMA_PREV.unlink(missing_ok=True)
