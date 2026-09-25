"""fossypaper.engine — the backend. No Qt, no curses, so it stays testable alone.

What it knows how to do:

  * **Find wallpapers** across every library root you have — Steam's Workshop
    dir, fossypaper's own downloads, and anything you point
    `FOSSYPAPER_LIBRARY` at — and read each one's `project.json` for what it
    actually is, instead of guessing from filenames.
  * **Route each wallpaper to a renderer that can actually draw it**: WE scenes
    to `linux-wallpaperengine`, video to `mpvpaper`, stills to `swww` when you
    have it and `mpvpaper` when you don't.
  * **Drive the renderer with every knob it exposes** — per-output backgrounds,
    spanning, scaling/clamp, layer, fps, GPU/EGL selection, audio, effects, and
    the fullscreen-pause rules that keep a wallpaper from eating a game's frames.
  * **Sync a colour theme** from the wallpaper's own pixels.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import properties as props_mod

STATE = Path.home() / ".local/state/fossypaper"

# PATH does not change under a running process, and the TUI asks "is mpvpaper
# installed?" once per visible row per keypress. Look each name up once.
_WHICH: dict[str, str | None] = {}


def which(name: str) -> str | None:
    if name not in _WHICH:
        _WHICH[name] = shutil.which(name)
    return _WHICH[name]


def forget_tools() -> None:
    """Drop the cache — after installing something, mid-session."""
    _WHICH.clear()


BIN = which("linux-wallpaperengine") or "linux-wallpaperengine"

STEAM_WORKSHOP = Path.home() / ".local/share/Steam/steamapps/workshop/content/431960"
FLATPAK_WORKSHOP = (Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam"
                    "/steamapps/workshop/content/431960")
OWN_LIBRARY = Path.home() / ".local/share/fossypaper/wallpapers"


def library_roots() -> list[Path]:
    """Every folder we look in, most-specific first.

    `FOSSYPAPER_LIBRARY` *replaces* the defaults rather than adding to them —
    "not tied to Steam" has to mean you can point fossypaper somewhere else and
    get only that. It may name several roots, colon-separated, like a PATH.
    """
    override = [Path(p.strip()).expanduser()
                for p in (os.environ.get("FOSSYPAPER_LIBRARY") or "").split(":") if p.strip()]
    roots = override or [STEAM_WORKSHOP, FLATPAK_WORKSHOP, OWN_LIBRARY]
    seen, out = set(), []
    for r in roots:
        if r not in seen and r.is_dir():
            seen.add(r); out.append(r)
    return out


class _DirProxy:
    """`WE_DIR` used to be a single Path and other code indexes it as one.
    Keep that working while there are really several roots: `WE_DIR / wid`
    resolves to whichever root holds that wallpaper."""

    def _primary(self) -> Path:
        roots = library_roots()
        return roots[0] if roots else OWN_LIBRARY

    def __truediv__(self, wid) -> Path:
        for root in library_roots():
            if (root / str(wid)).is_dir():
                return root / str(wid)
        return self._primary() / str(wid)

    def is_dir(self) -> bool:
        return bool(library_roots())

    def __fspath__(self) -> str:
        return str(self._primary())

    def __str__(self) -> str:
        roots = library_roots()
        return "  ".join(str(r) for r in roots) if roots else str(OWN_LIBRARY)


WE_DIR = _DirProxy()


def have_renderer() -> bool:
    return which("linux-wallpaperengine") is not None


# --------------------------------------------------------------------------- #
#  Library model
# --------------------------------------------------------------------------- #
Property = props_mod.Property          # re-exported: callers used engine.Property


@dataclass
class Wallpaper:
    id: str
    title: str
    type: str                  # scene | video | image | web | application | missing_assets | missing_dependency
    video: bool                # carries a video track the renderer must decode
    preview: Path | None
    folder: Path
    entry: Path | None = None  # the file the renderer actually opens
    audio: bool = False        # reacts to system audio
    tags: list = field(default_factory=list)
    description: str = ""
    render_id: str = ""        # id to hand the renderer — itself, unless this is a
                                # preset aliased onto a dependency it builds on
    depends_on: str = ""       # workshop id a preset needs but doesn't have locally
    preset_overrides: dict = field(default_factory=dict)  # this preset's own values,
                                # to layer onto the dependency it renders through
    skipped_textures: list = field(default_factory=list)  # scenetexture keys the
                                # renderer can never fill from an external file

    def __post_init__(self):
        if not self.render_id:
            self.render_id = self.id

    @property
    def supported(self) -> bool:
        return self.type in ("scene", "video", "image")


_PREVIEW_GLOB = ("preview.gif", "preview.png", "preview.jpg", "preview.jpeg",
                 "preview.webp", "preview.*")


def _preview(folder: Path, named: str) -> Path | None:
    if named:
        p = folder / named
        if p.is_file():
            return p
    for pat in _PREVIEW_GLOB:
        for p in sorted(folder.glob(pat)):
            if p.is_file():
                return p
    return None


_VIDEO_EXT = (".mp4", ".webm", ".mkv", ".avi", ".m4v", ".mov")
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _entry_file(folder: Path, meta: dict) -> Path | None:
    """`project.json`'s own `file` key names the entry point — scene.json,
    gifscene.json, or the video itself. Trust it; fall back to a scan only when
    it's missing or points at something that isn't there."""
    named = str(meta.get("file") or "").strip()
    if named:
        p = folder / named
        if p.is_file():
            return p
        # scene.json is the *source*; the shipped bundle is the matching .pkg
        pkg = folder / (Path(named).stem + ".pkg")
        if pkg.is_file():
            return pkg
    for pat in ("*.pkg", *(f"*{e}" for e in _VIDEO_EXT)):
        for p in sorted(folder.glob(pat)):
            return p
    for e in _IMAGE_EXT:
        for p in sorted(folder.glob("*" + e)):
            if "preview" not in p.name.lower():
                return p
    return None


def _read_project(folder: Path) -> dict | None:
    try:
        meta = json.loads((folder / "project.json").read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return None
    return meta if isinstance(meta, dict) else None


def _dependency_folder(dep_id: str) -> Path | None:
    for root in library_roots():
        d = root / dep_id
        if d.is_dir():
            return d
    return None


def _preset_overrides(preset, folder: Path, base_prop_kinds: dict) -> tuple[dict, list]:
    """A preset's own values, wire-formatted for `--set-property`, plus the
    keys we deliberately left out.

    These get applied on top of a *different* wallpaper's folder (the
    dependency this preset builds on), so a value like `"files/glow.gif"` — a
    path relative to *this* preset's own folder — has to become absolute
    here, or the renderer would look for it in the wrong place.

    `scenetexture` values are the one exception: linux-wallpaperengine's own
    texture cache resolves them by asking the wallpaper's *compiled package*
    for a texture of that name (`TextureCache::resolve`, which walks
    `assetLocator->texture(...)` and nothing else) — it has no code path for
    loading an arbitrary external image file, no matter how the path is
    spelled. Forwarding one just buys a silent "Cannot resolve user texture"
    and a blank panel, so skip it and say so instead of pretending it worked.
    """
    if not isinstance(preset, dict):
        return {}, []
    out, skipped = {}, []
    for key, value in preset.items():
        if (isinstance(value, str) and base_prop_kinds.get(key) == "scenetexture"
                and (folder / value).is_file()):
            skipped.append(key)
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, str):
            local = folder / value
            out[key] = str(local) if local.is_file() else value
        elif value is None:
            continue
        else:
            out[key] = str(value)
    return out, skipped


def read_wallpaper(folder: Path) -> Wallpaper | None:
    meta = _read_project(folder)
    if meta is None:
        return None
    general = meta.get("general") if isinstance(meta.get("general"), dict) else {}
    entry = _entry_file(folder, meta)

    # A preset addon ships no scene of its own — just overrides — and names the
    # base wallpaper it customizes via `dependency`. If that base is in the
    # library too, render through it with the preset's own values layered on;
    # if it isn't, this preset genuinely has nothing to draw.
    render_id = folder.name
    depends_on = ""
    preset_overrides: dict = {}
    skipped_textures: list = []
    dependency = str(meta.get("dependency") or "").strip()
    if entry is None and dependency:
        base_folder = _dependency_folder(dependency)
        base_meta = _read_project(base_folder) if base_folder else None
        base_entry = (_entry_file(base_folder, base_meta)
                      if base_folder and base_meta is not None else None)
        if base_entry is not None:
            entry, render_id = base_entry, base_folder.name
            general = base_meta.get("general") if isinstance(base_meta.get("general"), dict) else {}
            base_prop_kinds = {k: str((v or {}).get("type") or "").lower()
                               for k, v in (general.get("properties") or {}).items()
                               if isinstance(v, dict)}
            preset_overrides, skipped_textures = _preset_overrides(
                meta.get("preset"), folder, base_prop_kinds)
        else:
            depends_on = dependency

    kind = ("missing_dependency" if depends_on else
            str(meta.get("type") or "").lower() or _infer_type(entry))
    if entry is None and kind in ("scene", "video", "image"):
        # project.json declared a real type but nothing backs it — a partial
        # Workshop download (manifest arrives, payload doesn't) looks exactly
        # like this, and it must not fall through to "wpe has nothing to draw".
        kind = "missing_assets"
    video = bool(general.get("supportsvideo")) or (
        entry is not None and entry.suffix.lower() in _VIDEO_EXT)
    return Wallpaper(
        id=folder.name,
        title=str(meta.get("title") or folder.name),
        type=kind,
        video=video,
        preview=_preview(folder, str(meta.get("preview") or "")),
        folder=folder,
        entry=entry,
        audio=bool(general.get("supportsaudioprocessing")),
        tags=[str(t) for t in (meta.get("tags") or []) if isinstance(t, (str, int))],
        description=str(meta.get("description") or ""),
        render_id=render_id,
        depends_on=depends_on,
        preset_overrides=preset_overrides,
        skipped_textures=skipped_textures,
    )


def _infer_type(entry: Path | None) -> str:
    if entry is None:
        return "missing_assets"
    s = entry.suffix.lower()
    if s in _VIDEO_EXT:
        return "video"
    if s in _IMAGE_EXT:
        return "image"
    if s == ".html":
        return "web"
    return "scene"


def scan_library() -> list[Wallpaper]:
    """Every wallpaper across every root. The first root to define an id wins,
    so a local override shadows the Steam copy rather than duplicating it."""
    out, seen = [], set()
    for root in library_roots():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            wp = read_wallpaper(d)
            if wp:
                seen.add(d.name); out.append(wp)
    out.sort(key=lambda w: (not w.supported, w.video, w.title.lower()))
    return out


def find(wid: str) -> Wallpaper | None:
    folder = WE_DIR / wid
    return read_wallpaper(folder) if folder.is_dir() else None


def list_properties(wid: str) -> list[Property]:
    """The wallpaper's own knobs. project.json carries the full schema — types,
    ranges, combo options, and the `condition` expressions that say when a knob
    is even relevant — so read it there. Only fall back to asking the renderer
    for a library that ships no schema.

    A preset aliased onto a dependency (see `read_wallpaper`) renders through
    the *dependency's* files, so its knobs — not the preset's own, mostly-empty
    project.json — are what's actually live.
    """
    wp = find(wid)
    render_id = wp.render_id if wp else wid
    folder = WE_DIR / render_id
    ps = props_mod.from_project(folder)
    if ps:
        return ps
    return props_mod.from_renderer(BIN, render_id) if have_renderer() else []


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
    if not which("hyprctl"):
        return []
    r = subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=5)
    return [m["name"] for m in json.loads(r.stdout) if m.get("name")]


def _outputs_niri() -> list[str]:
    if not which("niri"):
        return []
    r = subprocess.run(["niri", "msg", "-j", "outputs"], capture_output=True, text=True, timeout=5)
    data = json.loads(r.stdout)
    return sorted(data) if isinstance(data, dict) else [o["name"] for o in data]


def _outputs_kde() -> list[str]:
    if not which("kscreen-doctor") or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
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
# hyprland IPC; KWin and Mutter expose neither, so "pause behind a game" is a
# silent no-op there (the renderer says as much in renderer.log).
FULLSCREEN_BLIND = ("kde", "gnome")


def _outputs_wlr() -> list[str]:
    if not which("wlr-randr"):
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
    if want:
        return [want]
    return live or ["eDP-1"]


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
    wp = find(wid)
    if wp is None:
        return "wpe", have_renderer()
    return backend_for(wp)


def backend_for(wp: Wallpaper) -> tuple[str, bool]:
    if wp.type in _UNSUPPORTED:
        return wp.type, False
    if wp.entry is not None and wp.entry.suffix.lower() in _VIDEO_EXT:
        return "mpvpaper", bool(which("mpvpaper"))
    if wp.entry is not None and wp.entry.suffix.lower() == ".pkg":
        return "wpe", have_renderer()
    if wp.entry is not None and wp.entry.suffix.lower() in _IMAGE_EXT:
        be = "swww" if which("swww") else "mpvpaper"
        return be, bool(which(be))
    return "wpe", have_renderer()


def wants_audio(wp: Wallpaper, opts: dict) -> bool:
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


def why_unsupported(wp: Wallpaper) -> str:
    if wp.type == "missing_dependency" and wp.depends_on:
        return (f"needs workshop item {wp.depends_on} as its base — subscribe to it "
                f"in Steam Workshop, then rescan")
    return _UNSUPPORTED.get(wp.type, "")


def render_note(wp: Wallpaper) -> str:
    """A heads-up for a wallpaper that *does* render but not completely —
    distinct from `why_unsupported`, which is for one that draws nothing."""
    if wp.skipped_textures:
        keys = ", ".join(wp.skipped_textures)
        return (f"renders via its base wallpaper ({wp.render_id}); this preset's own "
                f"custom image slot(s) — {keys} — stay blank, since "
                f"linux-wallpaperengine can't load an external file as a scene texture")
    return ""


def backends_status() -> dict:
    return {"wpe": have_renderer(),
            "mpvpaper": bool(which("mpvpaper")),
            "swww": bool(which("swww"))}


def is_running() -> bool:
    return any(subprocess.run(["pgrep", "-x", c], capture_output=True).stdout.strip()
               for c in _MANAGED)


def stop():
    for c in _MANAGED:
        subprocess.run(["pkill", "-x", c], capture_output=True)
    if which("swww"):
        subprocess.run(["swww", "clear"], capture_output=True, timeout=5)


# --------------------------------------------------------------------------- #
#  linux-wallpaperengine
# --------------------------------------------------------------------------- #
def build_argv(wid: str, opts: dict) -> list[str]:
    """Every render knob the renderer exposes, in the order it wants them:
    per-output flags follow the `--screen-root` / `--screen-span` they modify."""
    a = [BIN]
    span = [s for s in (opts.get("span") or []) if s]
    if span:
        a += ["--screen-span", ",".join(span), "--bg", wid]
        a += _per_output(opts)
    else:
        for out in target_outputs(opts):
            a += ["--screen-root", out, "--bg", wid]
            a += _per_output(opts)

    a += ["--layer", opts.get("layer") or "bottom", "--fps", str(opts.get("fps") or 30)]

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


# --------------------------------------------------------------------------- #
#  Applying
# --------------------------------------------------------------------------- #
def start(wid: str, opts: dict) -> tuple[bool, str]:
    """Apply a wallpaper through whichever backend can render it."""
    wp = find(wid)
    if wp is None:
        return False, f"no wallpaper with id {wid} in any library root"
    be, ok = backend_for(wp)
    if be in _UNSUPPORTED:
        return False, why_unsupported(wp) or _UNSUPPORTED[be]
    if not ok:
        return False, f"this one renders via {be}, which isn't installed — paru -S {be}"
    stop()
    STATE.mkdir(parents=True, exist_ok=True)
    RENDER_LOG.write_text("")
    _spawned.clear()
    opts = {**opts, "no_audio_processing": not wants_audio(wp, opts)}
    if wp.preset_overrides:
        # The preset's own values go first; anything the user set by hand for
        # this wallpaper wins over them.
        opts = {**opts, "properties": {**wp.preset_overrides, **(opts.get("properties") or {})}}
    try:
        {"mpvpaper": _start_mpvpaper, "swww": _start_swww, "wpe": _start_wpe}[be](wp, opts)
    except OSError as e:
        return False, f"{be} wouldn't start: {e}"
    dead = _died_early(float(opts.get("verify_grace", 1.5)))
    if dead is not None:
        why = "; ".join(render_log_tail(2)) or f"exit code {dead.returncode}"
        return False, f"{be} exited straight away — {why} (full log: {RENDER_LOG})"
    return True, f"applied via {be}"


RENDER_LOG = STATE / "renderer.log"
_spawned: list = []


def _spawn(argv, env=None):
    """Start a renderer detached, with its output in RENDER_LOG (rewritten on
    every apply) — a renderer that dies on its first frame used to vanish into
    /dev/null while the app said "applied"."""
    STATE.mkdir(parents=True, exist_ok=True)
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


def _start_wpe(wp: Wallpaper, opts: dict):
    env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
    env.setdefault("WALLPAPER_ENGINE_ASSETS", str(opts.get("assets_dir") or ""))
    _spawn(build_argv(wp.render_id, opts), env)


def _start_mpvpaper(wp: Wallpaper, opts: dict):
    """Video, and stills too when swww isn't installed — mpv holds a single
    frame perfectly well, and that beats taking a dependency for it."""
    env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
    still = wp.entry is not None and wp.entry.suffix.lower() in _IMAGE_EXT
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

    base = ["mpvpaper", "-l", opts.get("layer") or "bottom", "-o", " ".join(mo)]
    if opts.get("fullscreen_pause", True):
        # -p pauses seamlessly; -a FULL extends that to any fullscreen window.
        base = base[:1] + ["-p", "-a", "FULL"] + base[1:]
    for out in target_outputs(opts):
        _spawn(base + [out, str(wp.entry)], env)


def _start_swww(wp: Wallpaper, opts: dict):
    if subprocess.run(["pgrep", "-x", "swww-daemon"], capture_output=True).returncode != 0:
        _spawn(["swww-daemon"])
        time.sleep(0.6)
    fit = {"fill": "fill", "fit": "fit", "stretch": "stretch",
           "default": "crop", "": "crop"}.get(opts.get("scaling", ""), "crop")
    subprocess.run(["swww", "img", "--outputs", ",".join(target_outputs(opts)),
                    "--resize", fit, "--transition-type", "fade", str(wp.entry)],
                   capture_output=True)


def screenshot(wid: str, out: Path, opts: dict) -> bool:
    """Render one frame to a PNG (palette extraction, SDDM stills, previews).

    Only scenes can be rendered this way; for video and stills the source file
    already *is* a frame, so take it from there instead of spinning up GL.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    wp = find(wid)
    if wp is not None and wp.entry is not None:
        suffix = wp.entry.suffix.lower()
        if suffix in _IMAGE_EXT:
            return _still_from_image(wp.entry, out)
        if suffix in _VIDEO_EXT and which("ffmpeg"):
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
                                "-i", str(wp.entry), "-frames:v", "1", str(out)],
                               capture_output=True, timeout=60)
            if out.is_file() and r.returncode == 0:
                return True
    if have_renderer():
        env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
        render_id = wp.render_id if wp is not None else wid
        try:
            subprocess.run([BIN, "--screenshot", str(out), "--screenshot-delay", "90",
                            "--silent", render_id], env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        if out.is_file():
            return True
    # Last resort: the wallpaper's own preview is a fair likeness of its palette.
    if wp is not None and wp.preview is not None:
        return _still_from_image(wp.preview, out)
    return False


THUMBS = Path.home() / ".cache/fossypaper/library"


def thumbnail(wid: str, width: int = 240) -> Path | None:
    """A plain PNG of a wallpaper's preview, cached.

    Previews in a Workshop library are mostly animated GIFs. Anything that just
    wants to *show* one — the Noctalia panel, a launcher row — wants a still in
    a format everything reads, so bake one once and hand back the path.
    """
    wp = find(wid)
    if wp is None or wp.preview is None:
        return None
    dest = THUMBS / f"{wid}-{width}.png"
    try:
        if dest.is_file() and dest.stat().st_mtime >= wp.preview.stat().st_mtime:
            return dest
    except OSError:
        pass
    part = dest.with_suffix(".part.png")
    try:
        from PIL import Image
        THUMBS.mkdir(parents=True, exist_ok=True)
        img = Image.open(wp.preview)
        img.seek(0)                      # first frame of an animation
        img = img.convert("RGB")
        img.thumbnail((width, width), Image.LANCZOS)
        img.save(part)
        os.replace(part, dest)           # never leave a half-written thumbnail
    except Exception:
        part.unlink(missing_ok=True)
        return None
    return dest


def clear_thumbnails() -> int:
    n = 0
    for f in THUMBS.glob("*"):
        if f.is_file():
            f.unlink()
            n += 1
    return n


def _still_from_image(src: Path, out: Path) -> bool:
    try:
        from PIL import Image
        Image.open(src).convert("RGB").save(out)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Theme sync — palette out to shells/compositors
# --------------------------------------------------------------------------- #
def theme_backends() -> list[str]:
    return ["builtin"] + [t for t in ("matugen", "wallust", "wal") if which(t)]


def sync_theme(wid: str, opts: dict, backend: str = "builtin") -> tuple[bool, str]:
    STATE.mkdir(parents=True, exist_ok=True)
    frame = STATE / "current-frame.png"
    if not screenshot(wid, frame, opts):
        return False, "couldn't produce a frame to theme from"
    avail = theme_backends()
    if backend in ("", "auto"):
        backend = avail[-1]
    if backend not in avail:
        return False, (f"{backend} isn't installed — available: {', '.join(avail)}")
    if backend == "builtin":
        pal = extract_palette(frame)
        write_palette_files(pal)
        return True, f"extracted {len(pal)} colours → {STATE}/colors.*"
    cmds = {"matugen": ["matugen", "image", str(frame)],
            "wallust": ["wallust", "run", str(frame)],
            "wal": ["wal", "-i", str(frame), "-n"]}
    try:
        r = subprocess.run(cmds[backend], capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"theme sync error: {e}"
    if r.returncode != 0:
        return False, f"{backend} failed: {r.stderr.strip()[:200]}"
    write_palette_files(extract_palette(frame))    # keep our own files current too
    return True, f"themed via {backend}"


def extract_palette(png: Path, n: int = 8) -> list[str]:
    """Cluster the wallpaper's own pixels into n dominant colours, most-common
    first. Pure PIL — no matugen/pywal required."""
    from PIL import Image
    img = Image.open(png).convert("RGB")
    img.thumbnail((200, 200))
    q = img.quantize(colors=n, method=Image.Quantize.FASTOCTREE)
    pal = q.getpalette() or []
    hexes = []
    for _count, idx in sorted(q.getcolors() or [], reverse=True):
        r, g, b = pal[idx * 3:idx * 3 + 3]
        hexes.append(f"#{r:02x}{g:02x}{b:02x}")
    return hexes or ["#000000"]


def _lum(hx: str) -> float:
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _sat(hx: str) -> float:
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def derive_roles(pal: list[str]) -> dict:
    by_lum = sorted(pal, key=_lum)
    return {"background": by_lum[0], "foreground": by_lum[-1],
            "accent": max(pal, key=_sat), "palette": pal}


def write_palette_files(pal: list[str]) -> None:
    """Emit the palette so any shell can source it (colors.sh), read it
    (colors.json) or include it (colors.css). pywal-compatible names too."""
    STATE.mkdir(parents=True, exist_ok=True)
    roles = derive_roles(pal)
    cols = (pal * 16)[:16]
    (STATE / "colors.json").write_text(json.dumps(
        {"special": {"background": roles["background"], "foreground": roles["foreground"],
                     "cursor": roles["accent"]},
         "colors": {f"color{i}": c for i, c in enumerate(cols)},
         "roles": roles}, indent=2))
    (STATE / "colors.sh").write_text(
        f"# fossypaper palette\nbackground='{roles['background']}'\n"
        f"foreground='{roles['foreground']}'\naccent='{roles['accent']}'\n"
        + "".join(f"color{i}='{c}'\n" for i, c in enumerate(cols)))
    (STATE / "colors.css").write_text(
        ":root{\n" + f"  --background:{roles['background']};\n"
        f"  --foreground:{roles['foreground']};\n  --accent:{roles['accent']};\n"
        + "".join(f"  --color{i}:{c};\n" for i, c in enumerate(cols)) + "}\n")


def palette() -> list[str]:
    """The palette from the last theme sync, if there is one."""
    try:
        return json.loads((STATE / "colors.json").read_text())["roles"]["palette"]
    except (OSError, ValueError, KeyError):
        return []


# --------------------------------------------------------------------------- #
#  Login daemon + SDDM background
# --------------------------------------------------------------------------- #
SERVICE = Path.home() / ".config/systemd/user/fossypaper.service"


def ensure_wayland_env() -> None:
    """A login service can start before the compositor exported WAYLAND_DISPLAY."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for s in sorted(Path(rt).glob("wayland-*")):
        if not s.name.endswith(".lock"):
            os.environ["WAYLAND_DISPLAY"] = s.name
            os.environ.setdefault("XDG_RUNTIME_DIR", rt)
            return


def apply_current() -> tuple[bool, str]:
    """Restore the saved wallpaper — the login daemon's job."""
    from . import config
    ensure_wayland_env()
    cfg = config.load()
    wid = cfg.get("current")
    if not wid:
        return False, "nothing saved to restore"
    o = config.opts(cfg)
    o["properties"] = cfg.get("properties", {}).get(wid, {})
    return start(wid, o)


def install_service() -> Path:
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    exe = which("fossypaper") or str(Path.home() / ".local/bin/fossypaper")
    SERVICE.write_text(
        "[Unit]\nDescription=fossypaper — restore the wallpaper at login\n"
        "After=graphical-session.target\nPartOf=graphical-session.target\n\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        f"ExecStart={exe} daemon\n\n"
        "[Install]\nWantedBy=graphical-session.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "fossypaper.service"], capture_output=True)
    return SERVICE


def remove_service() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", "fossypaper.service"],
                   capture_output=True)
    SERVICE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


_SDDM_DEST = "/usr/share/sddm/themes/fossypaper-bg.png"


def sddm_prepare(wid: str, opts: dict) -> tuple[bool, str, dict]:
    """Render a still for the SDDM login background and stage a drop-in. SDDM is
    system-level, so hand back the exact root commands rather than touching /usr."""
    STATE.mkdir(parents=True, exist_ok=True)
    still = STATE / "sddm-background.png"
    if not screenshot(wid, still, opts):
        return False, "couldn't produce a still image", {}
    conf = STATE / "sddm-fossypaper.conf"
    conf.write_text("[Theme]\n# point your SDDM theme's background at the still below,\n"
                    "# or use a theme that honours [General] background=\n"
                    f"\n[General]\nbackground={_SDDM_DEST}\n")
    return True, str(still), {
        "still": str(still), "conf": str(conf),
        "install": [f"sudo install -Dm644 {still} {_SDDM_DEST}",
                    f"sudo install -Dm644 {conf} /etc/sddm.conf.d/10-fossypaper.conf"]}
