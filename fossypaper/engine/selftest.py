"""`fossypaper doctor --live`: apply wallpapers for real and check the result.

Meant to be run by the person sitting at the desktop — it changes the
wallpaper for a few seconds per kind, then puts the saved one back. On
layer-shell compositors it asks the compositor itself where each renderer's
surface ended up (layer, output), grabs the screen with `grim` to make sure
something other than black is showing, and checks the renderer is still
alive. Everything it learns goes to `selftest.txt` in the state directory.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import library, render, session, tools

REPORT = tools.STATE / "selftest.txt"

# layer-shell namespaces the renderers register their surfaces under
NAMESPACES = {"wpe": ("linux-wallpaperengine",), "mpvpaper": ("mpvpaper",),
              "swww": ("swww-daemon",)}
_HYPR_LEVELS = {"0": "background", "1": "bottom", "2": "top", "3": "overlay"}


def layers() -> list[dict]:
    """Every layer-shell surface the compositor knows: namespace, layer, output."""
    comp = session.compositor()
    try:
        if comp == "hyprland" and tools.which("hyprctl"):
            r = subprocess.run(["hyprctl", "layers", "-j"], capture_output=True, text=True, timeout=5)
            return parse_hypr_layers(json.loads(r.stdout))
        if comp == "niri" and tools.which("niri"):
            r = subprocess.run(["niri", "msg", "-j", "layers"], capture_output=True, text=True,
                               timeout=5)
            return parse_niri_layers(json.loads(r.stdout))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return []


def parse_hypr_layers(data) -> list[dict]:
    out = []
    for output, mon in (data or {}).items():
        for level, surfaces in ((mon or {}).get("levels") or {}).items():
            for s in surfaces or []:
                out.append({"namespace": s.get("namespace", ""), "output": output,
                            "layer": _HYPR_LEVELS.get(str(level), str(level))})
    return out


def parse_niri_layers(data) -> list[dict]:
    out = []
    for s in data or []:
        out.append({"namespace": s.get("namespace", ""), "output": s.get("output", ""),
                    "layer": str(s.get("layer", "")).lower()})
    return out


def ours(surfaces: list[dict], backend: str) -> list[dict]:
    names = NAMESPACES.get(backend, ())
    return [s for s in surfaces if any(s["namespace"].startswith(n) for n in names)]


def brightness(png: Path) -> float | None:
    """Mean luminance 0..255 of a screenshot, or None if we couldn't take one."""
    try:
        from PIL import Image, ImageStat
        with Image.open(png) as img:
            return ImageStat.Stat(img.convert("L").resize((160, 90))).mean[0]
    except (OSError, ImportError):
        return None


def grab(output: str) -> Path | None:
    if not tools.which("grim"):
        return None
    out = tools.STATE / f"selftest-{output or 'all'}.png"
    argv = ["grim"] + (["-o", output] if output else []) + [str(out)]
    try:
        r = subprocess.run(argv, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out if r.returncode == 0 and out.is_file() else None


def pick_samples(lib: list) -> dict:
    """One usable wallpaper per kind, preferring the saved current one."""
    picks = {}
    for w in lib:
        be, ok = render.backend_for(w)
        k = library.kind_of(w)
        if ok and k not in picks:
            picks[k] = w
    return picks


def check(wp, opts: dict, settle: float = 4.0) -> dict:
    """Apply one wallpaper and look at what actually happened."""
    be, _ok = render.backend_for(wp)
    res = {"id": wp.id, "title": wp.title, "kind": library.kind_of(wp), "backend": be,
           "problems": []}
    ok, msg = render.start(wp.id, opts)
    res["apply"] = msg
    if not ok:
        res["problems"].append(f"didn't start: {msg}")
        return res
    time.sleep(settle)
    if not render.is_running():
        res["problems"].append("renderer died after starting: "
                               + ("; ".join(render.render_log_tail(3)) or "no log"))
    want_layer = session.resolved_layer(opts)
    surf = ours(layers(), be)
    res["surfaces"] = surf
    if session.compositor() in ("hyprland", "niri"):
        if not surf:
            res["problems"].append("the compositor shows no surface for the renderer")
        for s in surf:
            if s["layer"] != want_layer:
                res["problems"].append(f"{s['namespace']} is on the {s['layer']} layer, "
                                       f"expected {want_layer}")
    shot = grab((opts.get("output") or "").strip())
    lum = brightness(shot) if shot else None
    res["brightness"] = lum
    if lum is not None and lum < 3:
        res["problems"].append("the screen is black where the wallpaper should be "
                               "(something above it, or it isn't drawing)")
    log = " ".join(render.render_log_tail(6)).lower()
    if "fullscreen detection not supported" in log:
        res["problems"].append("renderer can't see fullscreen windows here, so it won't "
                               "pause behind games")
    return res


def run(opts: dict, current: str = "") -> tuple[list[dict], str]:
    """Check one wallpaper of each kind, restore `current`, write the report."""
    tools.STATE.mkdir(parents=True, exist_ok=True)
    head = {
        "compositor": session.compositor(), "host": session.host(opts),
        "layer": f"{opts.get('layer', 'auto')} -> {session.resolved_layer(opts)}",
        "backdrop_shell": session.backdrop_shell() or "none",
        "outputs": ", ".join(session.outputs()) or "none",
        "pinned_output_missing": session.missing_output(opts) or "no",
        "other_surfaces": sorted({f"{s['namespace']}@{s['layer']}" for s in layers()}),
    }
    results = []
    for kind, wp in sorted(pick_samples(library.scan_library()).items()):
        results.append(check(wp, {**opts, "properties": {}}))
    if current:
        render.start(current, opts)
    lines = [f"{k:22} {v}" for k, v in head.items()] + [""]
    for r in results:
        lines.append(f"[{'ok' if not r['problems'] else '!!'}] {r['kind']:6} {r['title'][:50]}"
                     f"  via {r['backend']}")
        for s in r.get("surfaces", []):
            lines.append(f"       surface {s['namespace']} on {s['output']} ({s['layer']})")
        if r.get("brightness") is not None:
            lines.append(f"       screen brightness {r['brightness']:.0f}/255")
        for p in r["problems"]:
            lines.append(f"       ! {p}")
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text)
    return results, text
