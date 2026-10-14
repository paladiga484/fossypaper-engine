"""Work around linux-wallpaperengine's shader preprocessing, per wallpaper.

Two renderer behaviours make custom Workshop shaders fail to compile, and a
layer whose shader fails is simply not drawn — the "missing textures":

  * **Includes land in the wrong place.** Wallpaper Engine inlines
    `#include "common.h"` where it's written. linux-wallpaperengine collects
    every include and pastes them just before `main()` instead, so a shader
    that defines helper functions above `main` (using `M_PI`, `rotateVec2`,
    `saturate`…) references them before they exist.
  * **Its JSON parser is strict.** Wallpaper Engine accepts a `[COMBO]` or
    uniform annotation with a stray quote in it; nlohmann doesn't, and the
    combo's default silently vanishes, changing which `#if` branch compiles.

fossypaper doesn't touch the Workshop files. It builds an *overlay* folder —
the original `scene.pkg` symlinked in, plus corrected copies of only the
shaders that needed it — and points the renderer at that. The renderer reads
loose files in the wallpaper folder before the package, so the corrected
shaders win and everything else comes from the package as before.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import library, session

OVERLAYS = Path.home() / ".cache/fossypaper/overlay"
_SHADER_EXT = (".vert", ".frag", ".geom")
# Workshop shaders are as often CRLF as not: allow the \r before each line end
_INCLUDE = re.compile(r'^[ \t]*#include[ \t]+"([^"]+)"[ \t]*\r?$', re.M)
_ANNOT = re.compile(r"^(?P<head>[ \t]*//[ \t]*(?:\[COMBO\][ \t]*)?)(?P<json>\{.*\})[ \t]*(?P<cr>\r?)$", re.M)
# symbols a header may lean on from the shader that includes it
_SHADER_GLOBAL = re.compile(r"\b[gva]_[A-Za-z0-9_]+\b")


def repair_json(text: str) -> str | None:
    """Make a Wallpaper Engine annotation parse as strict JSON, or None."""
    fixed = re.sub(r",\s*([}\]])", r"\1", text)          # trailing commas
    for _ in range(4):
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError as e:
            # The usual authoring slip is a value missing its closing quote:
            #   "material":"some text,"combo":"X"
            # the parser only notices at `combo`, so close the string just
            # before the `,"` that precedes where it gave up.
            cut = fixed.rfind(',"', 0, e.pos)
            if cut <= 0 or fixed[cut - 1] == '"':
                return None
            fixed = fixed[:cut] + '"' + fixed[cut:]
    return None


def _fix_annotations(src: str) -> str:
    def one(m):
        body = m.group("json")
        fixed = repair_json(body)
        return m.group(0) if fixed is None or fixed == body else m.group("head") + fixed + m.group("cr")
    return _ANNOT.sub(one, src)


def _inline_includes(src: str, header, seen: frozenset = frozenset()) -> str:
    """Inline self-contained headers where they're included; for the rest,
    at least put their #defines there, and let the renderer place the code."""
    def one(m):
        name = m.group(1)
        text = header(name)
        if text is None or name in seen:
            return m.group(0)
        text = _inline_includes(text, header, seen | {name})
        if not _SHADER_GLOBAL.search(_strip_comments(text)):
            return f"// begin {name} (inlined by fossypaper)\n{text}\n// end {name}"
        defines = "\n".join(ln for ln in text.splitlines() if ln.lstrip().startswith("#define"))
        return (defines + "\n" if defines else "") + m.group(0)
    return _INCLUDE.sub(one, src)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


_VARYING = re.compile(r"^[ \t]*varying[ \t]+(\w+)[ \t]+(\w+)[ \t]*;[^\n]*$", re.M)
_MAIN = re.compile(r"\bvoid[ \t]+main[ \t]*\([^)]*\)\s*\{")


def _writable_varyings(src: str) -> str:
    """Fragment shaders that write to their inputs (`v_TexCoord.xz *= …`).
    Direct3D allows it; GLSL doesn't ("can't modify shader input"), and the
    whole layer fails. Give each written input a mutable copy, loaded at the
    top of main(), and point the name at the copy."""
    body = _strip_comments(src)
    written = []
    for typ, name in dict.fromkeys(_VARYING.findall(body)):
        if re.search(rf"(?<![\w.]){name}(\s*\.\s*[xyzwrgba]+)?\s*[-+*/]?=(?!=)", body):
            written.append((typ, name))
    if not written:
        return src
    done = set()

    def decl(m):
        typ, name = m.group(1), m.group(2)
        if (typ, name) not in written or name in done:
            return m.group(0)
        done.add(name)
        return (f"{m.group(0)}\n{typ} _fp_{name};\n#define _fp_have_{name} 1\n"
                f"#define {name} _fp_{name}")
    src = _VARYING.sub(decl, src)
    # an input declared inside an #if that isn't taken has no copy to load
    load = "".join(f"\n#ifdef _fp_have_{n}\n#undef {n}\n_fp_{n} = {n};\n#define {n} _fp_{n}\n#endif"
                   for _t, n in written)
    return _MAIN.sub(lambda m: m.group(0) + load, src)


def _balance_conditionals(src: str) -> str:
    """Drop an `#endif` that closes nothing, and close what's left open.
    Wallpaper Engine's compiler shrugs these off; glslang rejects the shader."""
    out, depth, changed = [], 0, False
    for line in src.split("\n"):
        st = line.strip()
        if st.startswith("#if"):
            depth += 1
        elif st.startswith("#endif"):
            if depth == 0:
                out.append("// " + line.rstrip("\r") + "   (unmatched; dropped by fossypaper)")
                changed = True
                continue
            depth -= 1
        out.append(line)
    if depth > 0:
        out.extend(["#endif"] * depth)
        changed = True
    return "\n".join(out) if changed else src


def fix_shader(src: str, header, fragment: bool = False) -> str:
    """The corrected shader source. `header(name)` returns an include's text."""
    out = _inline_includes(_fix_annotations(_balance_conditionals(src)), header)
    return _writable_varyings(out) if fragment else out


def _header_lookup(pkg_files: dict, read, assets: str):
    def header(name: str):
        for rel in (f"shaders/{name}", name):
            if rel in pkg_files:
                return read(rel).decode("utf-8", "replace")
        if assets:
            p = Path(assets) / "shaders" / name
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
        return None
    return header


def overlay_for(wp, opts: dict) -> Path | None:
    """A folder to hand the renderer instead of the Workshop one, or None if
    no shader in this wallpaper needs correcting."""
    rw = library.find(wp.render_id) or wp
    pkg = rw.entry
    if pkg is None or pkg.suffix.lower() != ".pkg" or not pkg.is_file():
        return None
    try:
        entries = {n: (o, s) for n, o, s in library._pkg_entries(pkg)}
    except (OSError, ValueError):
        return None

    with open(pkg, "rb") as f:
        def read(name):
            o, s = entries[name]
            f.seek(o)
            return f.read(s)

        header = _header_lookup(entries, read, session.assets_dir(opts))
        patched = {}
        for name in entries:
            if name.endswith(_SHADER_EXT):
                src = read(name).decode("utf-8", "replace")
                out = fix_shader(src, header, fragment=name.endswith(".frag"))
                if out != src:
                    patched[name] = out
    if not patched:
        return None

    tag = hashlib.sha1(f"{pkg}:{pkg.stat().st_mtime_ns}:{_VERSION}".encode()).hexdigest()[:12]
    root = OVERLAYS / f"{rw.id}-{tag}"
    if not (root / ".complete").is_file():
        # an older overlay of the same wallpaper pins the old package's bytes
        # (hard links) — drop it once this one replaces it
        import shutil
        for stale in OVERLAYS.glob(f"{rw.id}-*") if OVERLAYS.is_dir() else ():
            if stale != root:
                shutil.rmtree(stale, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        for item in rw.folder.iterdir():
            # files only — and never symlinks: the renderer refuses to follow a
            # link out of the wallpaper folder ("Cannot find requested file")
            if item.is_file() and not (root / item.name).exists():
                _share(item, root / item.name)
        for name, text in patched.items():
            dest = root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        (root / ".complete").write_text("\n".join(sorted(patched)) + "\n")
    return root


_VERSION = 6       # bump when fix_shader changes, so old overlays are rebuilt


def _share(src: Path, dest: Path) -> None:
    """The same bytes at `dest` without doubling the disk: a hard link, else a
    reflink (btrfs/xfs across subvolumes), else a plain copy."""
    import os
    import shutil
    import subprocess
    try:
        os.link(src, dest)
        return
    except OSError:
        pass
    if subprocess.run(["cp", "--reflink=auto", str(src), str(dest)],
                      capture_output=True).returncode == 0:
        return
    shutil.copy2(src, dest)


def clear_overlays() -> int:
    import shutil
    n = 0
    if OVERLAYS.is_dir():
        for d in OVERLAYS.iterdir():
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n
