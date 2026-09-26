"""The wallpaper library: roots, project.json, presets, scene packages."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import properties as props_mod
from . import tools


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
    if ps or props_mod.has_schema(folder):
        return ps
    return props_mod.from_renderer(tools.BIN, render_id) if tools.have_renderer() else []


def kind_of(wp: Wallpaper) -> str:
    """scene | video | image — what the file actually is, whatever the manifest says."""
    s = wp.entry.suffix.lower() if wp.entry is not None else ""
    if s in _VIDEO_EXT:
        return "video"
    if s in _IMAGE_EXT:
        return "image"
    return "scene"


SCENE_VIDEOS = Path.home() / ".cache/fossypaper/scene-video"


def _pkg_entries(pkg: Path) -> list[tuple[str, int, int]]:
    """(name, absolute offset, size) for every file in a WE scene.pkg:
    PKGV header, a count, then length-prefixed names with offset/size."""
    import struct
    with open(pkg, "rb") as f:
        def s():
            return f.read(struct.unpack("<I", f.read(4))[0]).decode("utf-8", "replace")
        if not s().startswith("PKGV"):
            return []
        out = []
        for _ in range(struct.unpack("<I", f.read(4))[0]):
            name = s()
            off, size = struct.unpack("<II", f.read(8))
            out.append((name, off, size))
        base = f.tell()
    return [(n, base + o, sz) for n, o, sz in out]


def scene_has_effects(wp: Wallpaper) -> bool:
    """Does any layer of this scene run an effect chain?

    The Plasma scene renderer caches "static" passes (9754ea4, "cache static
    render passes"). A layer's base image counts as static, but it is cached
    in the effect chain's ping-pong buffer, which the next effect overwrites.
    From the second frame on, the chain starts from its own previous output,
    so every shake/wave compounds forever and the picture melts into smears.
    Scenes with effects have to run with that cache off."""
    rw = find(wp.render_id) or wp
    pkg = rw.entry
    try:
        if pkg is not None and pkg.suffix.lower() == ".pkg":
            ent = {n: (o, sz) for n, o, sz in _pkg_entries(pkg)}
            name = next((n for n in ent if n == "scene.json"), None) or \
                next((n for n in ent if n.endswith(".json") and "/" not in n
                      and n != "project.json"), None)
            if name is None:
                return True
            with open(pkg, "rb") as f:
                f.seek(ent[name][0])
                scene = json.loads(f.read(ent[name][1]).decode("utf-8", "replace"))
        elif pkg is not None:
            scene = json.loads(pkg.with_suffix(".json").read_text(errors="replace"))
        else:
            return True
    except (OSError, ValueError, StopIteration, KeyError, __import__("struct").error):
        return True          # unknown: take the safe (uncached) path
    for obj in scene.get("objects") or []:
        for eff in (obj.get("effects") or []) if isinstance(obj, dict) else []:
            if isinstance(eff, dict) and eff.get("visible", True) is not False:
                return True
    return False


def scene_video(wp: Wallpaper) -> Path | None:
    """The MP4 inside a scene's biggest video texture, extracted to the cache.

    Wallpaper Engine stores video layers as .tex files wrapping a plain MP4,
    its byte length in the uint32 just before it. The Plasma scene renderer
    has no video decoder, so these scenes show static noise there; the video
    on its own is what actually moves."""
    rw = find(wp.render_id) or wp
    pkg = rw.entry
    if pkg is None or pkg.suffix.lower() != ".pkg" or not pkg.is_file():
        return None
    out = SCENE_VIDEOS / f"{rw.id}.mp4"
    if out.is_file() and out.stat().st_mtime >= pkg.stat().st_mtime and out.stat().st_size:
        return out
    import struct
    best = None
    try:
        with open(pkg, "rb") as f:
            for name, off, size in _pkg_entries(pkg):
                if not name.endswith(".tex") or size < 65536:
                    continue
                f.seek(off)
                head = f.read(4096)
                i = head.find(b"ftyp") - 4
                if i < 4:
                    continue
                length = struct.unpack("<I", head[i - 4:i])[0]
                if 0 < length <= size - i and (best is None or length > best[1]):
                    best = (off + i, length)
            if best is None:
                return None
            SCENE_VIDEOS.mkdir(parents=True, exist_ok=True)
            f.seek(best[0])
            tmp = out.with_suffix(".part")
            with open(tmp, "wb") as w:
                left = best[1]
                while left:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    w.write(chunk)
                    left -= len(chunk)
            tmp.replace(out)
    except (OSError, ValueError):
        return None
    return out


# --------------------------------------------------------------------------- #
#  Applying
# --------------------------------------------------------------------------- #
# Workshop authors ship notices as ordinary bool knobs: a "prompt box" over the
# art asking you to credit them, or "marketing words". Wallpaper Engine users
# click them off once; here that default is ours to set.
_NOTICE_OFF = re.compile(r"prompt\s*box|提示框|author'?s?\s*(notice|note)|作者(提示|公告)", re.I)
_NOTICE_ON = re.compile(r"hide\s*marketing|关闭.*营销", re.I)


def quiet_overrides(wp: Wallpaper) -> dict:
    """Property values that turn a wallpaper's author notices off."""
    out = {}
    for p in list_properties(wp.id):
        if p.kind != "bool":
            continue
        raw = f"{p.key} {p.text}"
        if _NOTICE_ON.search(raw):
            out[p.key] = "true"
        elif _NOTICE_OFF.search(raw):
            out[p.key] = "false"
    return out


def effective_properties(wp: Wallpaper, opts: dict) -> dict:
    """Quiet defaults, then the preset's own values, then what the user set by
    hand for this wallpaper — later wins."""
    quiet = quiet_overrides(wp) if opts.get("hide_author_notices", True) else {}
    return {**quiet, **wp.preset_overrides, **(opts.get("properties") or {})}
