"""Single frames: stills for hosts that can't animate, and library thumbnails."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from . import library, session, tools


def _still_for(wp: library.Wallpaper, opts: dict) -> Path | None:
    """A frame to show where this wallpaper can't animate."""
    out = tools.STATE / "stills" / f"{wp.id}.png"
    if out.is_file() and out.stat().st_size > 0:
        return out
    return out if screenshot(wp.id, out, opts) else None


def screenshot(wid: str, out: Path, opts: dict) -> bool:
    """Render one frame to a PNG (palette extraction, SDDM stills, previews).

    Only scenes can be rendered this way; for video and stills the source file
    already *is* a frame, so take it from there instead of spinning up GL.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    wp = library.find(wid)
    if wp is not None and wp.entry is not None:
        suffix = wp.entry.suffix.lower()
        if suffix in library._IMAGE_EXT:
            return _still_from_image(wp.entry, out)
        if suffix in library._VIDEO_EXT and tools.which("ffmpeg"):
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
                                "-i", str(wp.entry), "-frames:v", "1", str(out)],
                               capture_output=True, timeout=60)
            if out.is_file() and r.returncode == 0:
                return True
    if tools.have_renderer():
        env = dict(os.environ); env.update(session.gpu_env(opts.get("gpu", "auto")))
        render_id = wp.render_id if wp is not None else wid
        out.unlink(missing_ok=True)
        if _render_one_frame([tools.BIN, "--screenshot", str(out), "--screenshot-delay", "90",
                              "--silent", render_id], env, out):
            return True
    # Last resort: the wallpaper's own preview is a fair likeness of its palette.
    if wp is not None and wp.preview is not None:
        return _still_from_image(wp.preview, out)
    return False


def _render_one_frame(argv, env, out: Path, timeout: float = 60) -> bool:
    """The renderer writes its screenshot and then keeps running. Wait for the
    file to land and stop growing, then put it down — rather than sitting out
    the whole timeout on every apply."""
    try:
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError:
        return False
    deadline, last = time.monotonic() + timeout, -1
    try:
        while time.monotonic() < deadline and proc.poll() is None:
            size = out.stat().st_size if out.is_file() else -1
            if size > 0 and size == last:
                break
            last = size
            time.sleep(0.3)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
    return out.is_file() and out.stat().st_size > 0


THUMBS = Path.home() / ".cache/fossypaper/library"


def thumbnail(wid: str, width: int = 240) -> Path | None:
    """A plain PNG of a wallpaper's preview, cached.

    Previews in a Workshop library are mostly animated GIFs. Anything that just
    wants to *show* one — the Noctalia panel, a launcher row — wants a still in
    a format everything reads, so bake one once and hand back the path.
    """
    wp = library.find(wid)
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
