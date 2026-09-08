"""fossypaper.engine — the backend. No Qt here, so it's testable on its own.

Wraps linux-wallpaperengine: enumerates the Wallpaper Engine workshop library,
classifies pure-GL vs (broken-on-NVIDIA) video scenes, parses each wallpaper's
customizable PROPERTIES, drives the render process with fossypaper's extra
controls (fps / scaling / layer / gpu / effects), and can grab a frame to sync a
colour theme out to your shells.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

STATE = Path.home() / ".local/state/fossypaper"
BIN = shutil.which("linux-wallpaperengine") or "linux-wallpaperengine"

# Library path is not tied to a Steam/Wallpaper-Engine install. It defaults to
# Steam's workshop dir if present, but any folder of `<id>/(scene.pkg|*.mp4)`
# works — including one fossypaper fills itself from the Workshop web.
_STEAM_WS = Path.home() / ".local/share/Steam/steamapps/workshop/content/431960"
_OWN_LIB = Path.home() / ".local/share/fossypaper/wallpapers"
WE_DIR = Path(os.environ.get("FOSSYPAPER_LIBRARY")
              or (_STEAM_WS if _STEAM_WS.is_dir() else _OWN_LIB))


def have_renderer() -> bool:
    return shutil.which("linux-wallpaperengine") is not None


def download_workshop(wid: str) -> tuple[bool, str]:
    """Pull a wallpaper from the Steam Workshop web, no WE app required. Uses
    steamcmd when present (WE-owned content needs a Steam login); otherwise
    returns guidance. Downloads land in the configured library dir."""
    if not shutil.which("steamcmd"):
        return False, ("install `steamcmd` to pull from the Workshop, then: "
                       "steamcmd +login <user> +workshop_download_item 431960 "
                       f"{wid} +quit  (drops into {_STEAM_WS})")
    # Anonymous works for some items; WE content generally needs the owning login.
    try:
        r = subprocess.run(["steamcmd", "+login", "anonymous",
                            "+workshop_download_item", "431960", wid, "+quit"],
                           capture_output=True, text=True, timeout=300)
        ok = "Success" in r.stdout or (_STEAM_WS / wid).is_dir()
        return ok, ("downloaded" if ok else
                    "steamcmd couldn't fetch it anonymously — log in with your Steam "
                    "account (owns WE) and retry.")
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"download error: {e}"


# --------------------------------------------------------------------------- #
#  Library model
# --------------------------------------------------------------------------- #
@dataclass
class Property:
    key: str
    kind: str            # color | slider | boolean | textinput | combo | texture
    text: str
    value: str
    mn: float = 0.0
    mx: float = 1.0
    step: float = 0.01
    options: list = field(default_factory=list)


@dataclass
class Wallpaper:
    id: str
    title: str
    type: str
    video: bool
    preview: Path | None


def _has_video(folder: Path) -> bool:
    pkg = folder / "scene.pkg"
    if not pkg.is_file():
        return any(folder.glob("*.mp4")) or any(folder.glob("*.webm"))
    try:
        return bool(re.search(rb"\.(mp4|webm|mkv)", pkg.open("rb").read(2_000_000), re.I))
    except OSError:
        return False


def scan_library() -> list[Wallpaper]:
    out = []
    if not WE_DIR.is_dir():
        return out
    for d in sorted(WE_DIR.glob("*/")):
        pj = d / "project.json"
        if not pj.is_file():
            continue
        try:
            m = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        prev = d / m.get("preview", "")
        out.append(Wallpaper(d.name, m.get("title", d.name), str(m.get("type", "?")).lower(),
                             _has_video(d), prev if prev.is_file() else None))
    out.sort(key=lambda w: (w.video, w.title.lower()))
    return out


_PROP_HDR = re.compile(r"^(\w+) - (color|slider|boolean|textinput|combo|scene texture|texture)\s*$")


def list_properties(wid: str) -> list[Property]:
    """Parse `--list-properties` into typed Property records for the editor."""
    try:
        out = subprocess.run([BIN, "--list-properties", wid], capture_output=True,
                             text=True, timeout=25).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    props, cur = [], None
    for line in out.splitlines():
        h = _PROP_HDR.match(line.strip())
        if h:
            kind = "texture" if "texture" in h.group(2) else h.group(2)
            cur = Property(h.group(1), kind, h.group(1), "")
            props.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("Text:"):   cur.text = s[5:].strip()
        elif s.startswith("Value:"): cur.value = s[6:].strip()
        elif s.startswith("Min:"):   cur.mn = _f(s[4:])
        elif s.startswith("Max:"):   cur.mx = _f(s[4:])
        elif s.startswith("Step:"):  cur.step = _f(s[5:]) or 0.01
        elif s.startswith("Options:"): cur.options = [o.strip() for o in s[8:].split(",") if o.strip()]
    return props


def _f(s):
    try:
        return float(s.strip())
    except ValueError:
        return 0.0


# --------------------------------------------------------------------------- #
#  GPU / EGL — the "red brick" fix
# --------------------------------------------------------------------------- #
def gpu_env(mode: str = "auto") -> dict:
    """EGL vendor env. 'auto' pairs NVIDIA's vendor with the NVIDIA node when the
    Asus MUX routes eDP to the dGPU (else Mesa mis-drives it → garbage)."""
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


def outputs() -> list[str]:
    return sorted(p.name.split("-", 1)[1] for p in Path("/sys/class/drm").glob("card*-*")
                  if (p / "status").is_file() and (p / "status").read_text().strip() == "connected")


# --------------------------------------------------------------------------- #
#  The render process
# --------------------------------------------------------------------------- #
# Route each wallpaper to whatever actually renders it: mpvpaper for video,
# swww for stills, linux-wallpaperengine for WE scenes (best-effort).
_BACKENDS = ("linux-wallpaper", "mpvpaper")     # process comms fossypaper manages


def _video_file(folder: Path) -> Path | None:
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        f = next(iter(sorted(folder.glob(ext))), None)
        if f:
            return f
    return None


def _image_file(folder: Path) -> Path | None:
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        for f in sorted(folder.glob(ext)):
            if "preview" not in f.name.lower():
                return f
    return None


def backend_of(wid: str) -> tuple[str, bool]:
    """(backend, installed): how this wallpaper renders + whether its tool exists.
    mpvpaper=video (solid), swww=image (solid), wpe=WE scene (best-effort)."""
    folder = WE_DIR / wid
    if _video_file(folder):
        return "mpvpaper", bool(shutil.which("mpvpaper"))
    if (folder / "scene.pkg").is_file():
        return "wpe", have_renderer()
    if _image_file(folder):
        return "swww", bool(shutil.which("swww"))
    return "wpe", have_renderer()


def backends_status() -> dict:
    return {"wpe": have_renderer(), "mpvpaper": bool(shutil.which("mpvpaper")),
            "swww": bool(shutil.which("swww"))}


def is_running() -> bool:
    return any(subprocess.run(["pgrep", "-x", c], capture_output=True).stdout.strip()
               for c in _BACKENDS)


def stop():
    for c in _BACKENDS:
        subprocess.run(["pkill", "-x", c], capture_output=True)
    if shutil.which("swww"):
        subprocess.run(["swww", "clear"], capture_output=True, timeout=5)


def build_argv(wid: str, opts: dict) -> list[str]:
    a = [BIN, "--screen-root", opts.get("output", "eDP-1"), "--bg", wid,
         "--layer", opts.get("layer", "bottom"), "--fps", str(opts.get("fps", 30))]
    if opts.get("scaling"):        a += ["--scaling", opts["scaling"]]
    if opts.get("silent"):         a += ["--silent"]
    else:                          a += ["--volume", str(opts.get("volume", 15))]
    if opts.get("no_particles"):   a += ["--disable-particles"]
    if opts.get("no_parallax"):    a += ["--disable-parallax"]
    if opts.get("no_mouse"):       a += ["--disable-mouse"]
    for k, v in (opts.get("properties") or {}).items():
        a += ["--set-property", f"{k}={v}"]
    return a


def start(wid: str, opts: dict):
    """Apply a wallpaper via the right backend for its kind."""
    stop()
    folder = WE_DIR / wid
    vf = _video_file(folder)
    if vf and shutil.which("mpvpaper"):
        return _start_mpvpaper(vf, opts)
    imgf = None if (folder / "scene.pkg").is_file() else _image_file(folder)
    if imgf and shutil.which("swww"):
        return _start_swww(imgf, opts)
    return _start_wpe(wid, opts)


def _start_wpe(wid: str, opts: dict):
    env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
    subprocess.Popen(build_argv(wid, opts), env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _start_mpvpaper(video: Path, opts: dict):
    env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
    mo = ["loop-file=inf", "hwdec=auto", "vo=gpu",
          "no-audio" if opts.get("silent", True) else f"volume={opts.get('volume', 15)}"]
    subprocess.Popen(["mpvpaper", "-o", " ".join(mo), opts.get("output", "eDP-1"), str(video)],
                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _start_swww(image: Path, opts: dict):
    import time
    if subprocess.run(["pgrep", "-x", "swww-daemon"], capture_output=True).returncode != 0:
        subprocess.Popen(["swww-daemon"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(0.6)
    subprocess.run(["swww", "img", "--outputs", opts.get("output", "eDP-1"),
                    "--transition-type", "fade", str(image)], capture_output=True)


def screenshot(wid: str, out: Path, opts: dict) -> bool:
    """Render one frame to a PNG (for palette extraction / previews)."""
    env = dict(os.environ); env.update(gpu_env(opts.get("gpu", "auto")))
    try:
        subprocess.run([BIN, "--screenshot", str(out), "--screenshot-delay", "90",
                        "--silent", wid], env=env, capture_output=True, timeout=40)
        return out.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


# --------------------------------------------------------------------------- #
#  Theme sync — palette out to shells/compositors
# --------------------------------------------------------------------------- #
def theme_backends() -> list[str]:
    return [t for t in ("matugen", "wallust", "wal") if shutil.which(t)]


def sync_theme(wid: str, opts: dict, backend: str = "auto") -> tuple[bool, str]:
    """Grab a frame of the wallpaper and hand it to a palette generator, which
    themes the shells/apps the user has templates for."""
    STATE.mkdir(parents=True, exist_ok=True)
    frame = STATE / "current-frame.png"
    if not screenshot(wid, frame, opts):
        return False, "couldn't render a frame to theme from"
    avail = theme_backends()
    if backend == "auto":
        backend = avail[0] if avail else ""
    if not backend:
        return False, ("no palette generator found — install one of: matugen, wallust, "
                       "or python-pywal (then fossypaper themes your shells from the wallpaper)")
    if backend == "builtin":
        pal = extract_palette(frame)
        write_palette_files(pal)
        return True, f"extracted {len(pal)} colours → {STATE}/colors.* (wire your shells to these)"
    cmds = {"matugen": ["matugen", "image", str(frame)],
            "wallust": ["wallust", "run", str(frame)],
            "wal": ["wal", "-i", str(frame), "-n"]}
    try:
        r = subprocess.run(cmds[backend], capture_output=True, text=True, timeout=60)
        return (r.returncode == 0), (f"themed via {backend}" if r.returncode == 0
                                     else f"{backend} failed: {r.stderr.strip()[:200]}")
    except (OSError, subprocess.SubprocessError, KeyError) as e:
        return False, f"theme sync error: {e}"


# --------------------------------------------------------------------------- #
#  Built-in palette extraction (no external tool needed) — reads the pixels
# --------------------------------------------------------------------------- #
def extract_palette(png: Path, n: int = 8) -> list[str]:
    """Cluster the wallpaper's own pixels into n dominant colours (hex), ordered
    most-common first. Pure PIL — no matugen/pywal required."""
    from PIL import Image
    img = Image.open(png).convert("RGB")
    img.thumbnail((200, 200))
    q = img.quantize(colors=n, method=Image.Quantize.FASTOCTREE)
    pal = q.getpalette() or []
    hexes = []
    for count, idx in sorted(q.getcolors() or [], reverse=True):
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
    """Pick semantic roles from the palette: background, foreground, accent."""
    by_lum = sorted(pal, key=_lum)
    return {
        "background": by_lum[0],
        "foreground": by_lum[-1],
        "accent": max(pal, key=_sat),
        "palette": pal,
    }


def write_palette_files(pal: list[str]) -> None:
    """Emit the palette in a few shell-friendly formats under the state dir, so
    any shell/compositor can source it (colors.sh), read it (colors.json), or
    include it as CSS (colors.css). pywal-compatible names too."""
    STATE.mkdir(parents=True, exist_ok=True)
    roles = derive_roles(pal)
    # pad to 16 like a terminal palette
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
        ":root{\n" + f"  --background:{roles['background']};\n  --foreground:{roles['foreground']};\n"
        f"  --accent:{roles['accent']};\n"
        + "".join(f"  --color{i}:{c};\n" for i, c in enumerate(cols)) + "}\n")


# --------------------------------------------------------------------------- #
#  Login daemon + SDDM background
# --------------------------------------------------------------------------- #
SERVICE = Path.home() / ".config/systemd/user/fossypaper.service"


def ensure_wayland_env() -> None:
    """A login service may start before the compositor exported WAYLAND_DISPLAY.
    Point it at the first wayland socket in the runtime dir if it's unset."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for s in sorted(Path(rt).glob("wayland-*")):
        if not s.name.endswith(".lock"):
            os.environ["WAYLAND_DISPLAY"] = s.name
            os.environ.setdefault("XDG_RUNTIME_DIR", rt)
            return


def apply_current() -> bool:
    """Restore the saved wallpaper — the login daemon's job."""
    from . import config
    ensure_wayland_env()
    cfg = config.load()
    wid = cfg.get("current")
    if not wid or not (WE_DIR / wid).is_dir():
        return False
    o = config.opts(cfg)
    o["properties"] = cfg.get("properties", {}).get(wid, {})
    start(wid, o)
    return True


def install_service() -> Path:
    """Enable a systemd --user unit that restores the wallpaper each login."""
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("fossypaper") or str(Path.home() / ".local/bin/fossypaper")
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
    subprocess.run(["systemctl", "--user", "disable", "--now", "fossypaper.service"], capture_output=True)
    if SERVICE.exists():
        SERVICE.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


def sddm_prepare(wid: str, opts: dict) -> tuple[bool, str, dict]:
    """Render a still of the wallpaper for the SDDM login background and stage a
    drop-in. SDDM is system-level (needs root), so we render + hand back the
    exact install commands rather than touching /usr ourselves."""
    STATE.mkdir(parents=True, exist_ok=True)
    still = STATE / "sddm-background.png"
    if not screenshot(wid, still, opts):
        # fall back to the wallpaper's own preview if the live render fails
        from . import config  # noqa
        prev = next(iter((WE_DIR / wid).glob("preview.*")), None)
        if prev:
            try:
                from PIL import Image
                Image.open(prev).convert("RGB").save(still)
            except Exception:
                return False, "couldn't produce a still image", {}
        else:
            return False, "couldn't produce a still image", {}
    conf = STATE / "sddm-fossypaper.conf"
    conf.write_text("[Theme]\n# point your SDDM theme's background at the still below,\n"
                    "# or use a theme that honours [General] background=\n"
                    f"\n[General]\nbackground={_SDDM_DEST}\n")
    return True, str(still), {
        "still": str(still), "conf": str(conf),
        "install": [f"sudo install -Dm644 {still} {_SDDM_DEST}",
                    f"sudo install -Dm644 {conf} /etc/sddm.conf.d/10-fossypaper.conf"]}


_SDDM_DEST = "/usr/share/sddm/themes/fossypaper-bg.png"
