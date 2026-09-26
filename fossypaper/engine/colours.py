"""Theme sync: a colour palette from the wallpaper's own pixels."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import media, tools


# --------------------------------------------------------------------------- #
#  Theme sync — palette out to shells/compositors
# --------------------------------------------------------------------------- #
def theme_backends() -> list[str]:
    return ["builtin"] + [t for t in ("matugen", "wallust", "wal") if tools.which(t)]


def sync_theme(wid: str, opts: dict, backend: str = "builtin") -> tuple[bool, str]:
    tools.STATE.mkdir(parents=True, exist_ok=True)
    frame = tools.STATE / "current-frame.png"
    if not media.screenshot(wid, frame, opts):
        return False, "couldn't produce a frame to theme from"
    avail = theme_backends()
    if backend in ("", "auto"):
        backend = avail[-1]
    if backend not in avail:
        return False, (f"{backend} isn't installed — available: {', '.join(avail)}")
    if backend == "builtin":
        pal = extract_palette(frame)
        write_palette_files(pal)
        return True, f"extracted {len(pal)} colours → {tools.STATE}/colors.*"
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
    tools.STATE.mkdir(parents=True, exist_ok=True)
    roles = derive_roles(pal)
    cols = (pal * 16)[:16]
    (tools.STATE / "colors.json").write_text(json.dumps(
        {"special": {"background": roles["background"], "foreground": roles["foreground"],
                     "cursor": roles["accent"]},
         "colors": {f"color{i}": c for i, c in enumerate(cols)},
         "roles": roles}, indent=2))
    (tools.STATE / "colors.sh").write_text(
        f"# fossypaper palette\nbackground='{roles['background']}'\n"
        f"foreground='{roles['foreground']}'\naccent='{roles['accent']}'\n"
        + "".join(f"color{i}='{c}'\n" for i, c in enumerate(cols)))
    (tools.STATE / "colors.css").write_text(
        ":root{\n" + f"  --background:{roles['background']};\n"
        f"  --foreground:{roles['foreground']};\n  --accent:{roles['accent']};\n"
        + "".join(f"  --color{i}:{c};\n" for i, c in enumerate(cols)) + "}\n")


def palette() -> list[str]:
    """The palette from the last theme sync, if there is one."""
    try:
        return json.loads((tools.STATE / "colors.json").read_text())["roles"]["palette"]
    except (OSError, ValueError, KeyError):
        return []
