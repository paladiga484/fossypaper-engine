"""fossypaper CLI — scriptable subcommands over the engine.

Every listing command takes `--json`, because the Noctalia plugin (and any
script you write) drives fossypaper through this CLI. Machine output is a
contract; the pretty output is not.
"""
from __future__ import annotations

import json
import sys

from . import config, engine, properties, sources

def _emit(obj, as_json: bool, pretty):
    if as_json:
        json.dump(obj, sys.stdout, indent=None if getattr(sys.stdout, "isatty", lambda: False)()
                  is False else 2)
        sys.stdout.write("\n")
    else:
        pretty()


def _wp_dict(w, thumbs=False) -> dict:
    be, ok = engine.backend_for(w)
    d = {"id": w.id, "title": w.title, "type": w.type, "backend": be,
         "usable": ok, "video": w.video, "audio": w.audio,
         "preview": str(w.preview) if w.preview else "",
         "folder": str(w.folder),
         "reason": engine.why_unsupported(w),
         "note": engine.render_note(w)}
    if thumbs:
        t = engine.thumbnail(w.id)
        d["thumb"] = str(t) if t else ""
    return d


# --------------------------------------------------------------------------- #
def cmd_list(a):
    lib = engine.scan_library()
    rows = [_wp_dict(w, a.thumbs) for w in lib]
    if a.usable_only:
        rows = [r for r in rows if r["usable"]]
    if a.type:
        rows = [r for r in rows if r["type"] == a.type]
    if a.search:
        q = a.search.lower()
        rows = [r for r in rows if q in r["title"].lower() or q in r["id"].lower()]
    cur = config.load().get("current", "")

    def pretty():
        if not rows:
            print("no wallpapers matched")
            return
        for r in rows:
            mark = "*" if r["id"] == cur else " "
            # what it *is*, not what happens to render it — a still routed
            # through mpvpaper is still a still
            state = r["type"] if r["usable"] else "need " + r["backend"]
            print(f"{mark} {state:11} {r['id']:12} {r['title']}")
        print(f"\n{len(rows)} wallpaper(s) · roots: {engine.WE_DIR}")
    _emit(rows, a.json, pretty)
    return 0


def _resolve(cfg: dict, ref: str) -> tuple[str, str]:
    """An id, `current`, an exact title, or a piece of one that fits exactly
    one wallpaper. Returns (id, error)."""
    ref = (ref or "").strip()
    if ref in ("", "current", "."):
        return (cfg.get("current", ""), "" if cfg.get("current") else "nothing applied yet")
    if engine.find(ref):
        return ref, ""
    lib = engine.scan_library()
    exact = [w for w in lib if w.title.lower() == ref.lower()]
    hits = exact or [w for w in lib if ref.lower() in w.title.lower()]
    if len(hits) == 1:
        return hits[0].id, ""
    if not hits:
        return "", f"no wallpaper matches {ref!r}"
    return "", f"{len(hits)} wallpapers match {ref!r}: " + ", ".join(w.title for w in hits[:5])


def cmd_apply(a):
    cfg = config.load()
    a.id, err = _resolve(cfg, a.id)
    if err:
        _emit({"ok": False, "id": "", "message": err}, a.json, lambda: print("! " + err))
        return 1
    o = config.opts(cfg)
    o["properties"] = cfg.get("properties", {}).get(a.id, {})
    ok, msg = engine.start(a.id, o)
    if ok:
        cfg["current"] = a.id
        config.save(cfg)
        if cfg.get("theme_on_apply"):
            engine.sync_theme(a.id, o, cfg.get("theme_backend", "builtin"))
    w = engine.find(a.id)
    _emit({"ok": ok, "id": a.id, "title": w.title if w else "", "message": msg}, a.json,
          lambda: print(("+ " if ok else "! ") + (f"{w.title} — " if w else "") + msg))
    return 0 if ok else 1


def cmd_off(a):
    engine.stop()
    _emit({"ok": True, "running": False}, a.json, lambda: print("wallpaper off"))
    return 0


def cmd_toggle(a):
    if engine.is_running():
        return cmd_off(a)
    cfg = config.load()
    if not cfg.get("current"):
        _emit({"ok": False, "message": "nothing to restore"}, a.json,
              lambda: print("! nothing applied yet"))
        return 1
    a.id = cfg["current"]
    return cmd_apply(a)


def cmd_status(a):
    cfg = config.load()
    wid = cfg.get("current") or ""
    w = engine.find(wid) if wid else None
    st = {"running": engine.is_running(), "current": wid,
          "title": w.title if w else "", "backends": engine.backends_status(),
          "outputs": engine.outputs(), "palette": engine.palette()}
    _emit(st, a.json, lambda: print(
        ("running" if st["running"] else "stopped") + " · current: "
        + (st["title"] or wid or "-") + "\noutputs: " + (", ".join(st["outputs"]) or "-")
        + "\nbackends: " + ", ".join(f"{k}={'yes' if v else 'no'}"
                                     for k, v in st["backends"].items())))
    return 0


def cmd_theme(a):
    cfg = config.load()
    wid = a.id or cfg.get("current")
    if not wid:
        print("no wallpaper set — apply one first, or pass an id")
        return 1
    ok, msg = engine.sync_theme(wid, config.opts(cfg),
                                a.backend or cfg.get("theme_backend", "builtin"))
    _emit({"ok": ok, "message": msg, "palette": engine.palette()}, a.json,
          lambda: print(("+ " if ok else "! ") + msg))
    return 0 if ok else 1


def cmd_props(a):
    ps = engine.list_properties(a.id)
    saved = config.load().get("properties", {}).get(a.id, {})
    live = {**properties.defaults(ps), **saved}
    rows = [{"key": p.key, "type": p.kind, "label": p.text,
             "value": live.get(p.key, p.value), "default": p.value,
             "min": p.mn, "max": p.mx, "step": p.step,
             "options": [{"label": lb, "value": v} for lb, v in p.options],
             "condition": p.condition,
             "visible": properties.visible(p, live),
             "editable": p.editable}
            for p in ps]
    if not a.all:
        rows = [r for r in rows if r["editable"] and r["visible"]]

    def pretty():
        if not rows:
            print("no customizable properties")
            return
        for r in rows:
            if r["type"] == "group":
                print(f"\n-- {r['label']} --")
                continue
            rng = f"  [{r['min']:g}..{r['max']:g}]" if r["type"] == "slider" else ""
            opt = "  {" + ", ".join(o["value"] for o in r["options"]) + "}" if r["options"] else ""
            print(f"  {r['key']:24} {r['type']:12} = {r['value']}{rng}{opt}   ({r['label']})")
    _emit(rows, a.json, pretty)
    return 0 if rows else 1


def cmd_set_prop(a):
    cfg = config.load()
    ps = {p.key: p for p in engine.list_properties(a.id)}
    store = cfg.setdefault("properties", {}).setdefault(a.id, {})
    for kv in a.pairs:
        k, _, v = kv.partition("=")
        if k not in ps:
            print(f"! {a.id} has no property {k!r}")
            return 1
        store[k] = v
    config.save(cfg)
    print(f"+ {len(a.pairs)} propert{'y' if len(a.pairs) == 1 else 'ies'} saved")
    if cfg.get("current") == a.id and engine.is_running():
        o = config.opts(cfg); o["properties"] = store
        engine.start(a.id, o)
        print("  re-applied")
    return 0


def cmd_browse(a):
    try:
        if a.source == "wallhaven":
            cfg = config.load()
            rows, last = sources.wallhaven(a.query, a.page, a.sort or cfg["wallhaven_sort"],
                                           purity=cfg["wallhaven_purity"],
                                           api_key=cfg["wallhaven_key"])
        else:
            rows, last = sources.workshop(a.query, a.page, a.sort or "trend")
    except sources.SourceError as e:
        _emit({"ok": False, "message": str(e), "rows": []}, a.json, lambda: print("!", e))
        return 1
    out = [{"source": r.source, "id": r.id, "local_id": r.local_id, "title": r.title,
            "meta": r.meta, "kind": r.kind, "thumb": r.thumb_url, "url": r.page_url,
            "installed": r.installed()} for r in rows]

    def pretty():
        for r in out:
            print(f"{'#' if r['installed'] else ' '} {r['id']:12} {r['title'][:46]:46} {r['meta']}")
        print(f"\npage {a.page}/{last}")
    _emit({"rows": out, "page": a.page, "last_page": last}, a.json, pretty)
    return 0


def cmd_get(a):
    try:
        if a.id.isdigit():
            det = sources.details([a.id])
            if not det:
                print("! no such Workshop item"); return 1
            row = sources._workshop_row(det[0])
            if row is None:
                print("! that item isn't a Wallpaper Engine wallpaper"); return 1
        else:
            row = sources.wallhaven_one(a.id)
            if row is None:
                print("! no Wallhaven wallpaper with id", a.id)
                return 1
        ok, msg = sources.fetch(row)
    except sources.SourceError as e:
        ok, msg = False, str(e)
    _emit({"ok": ok, "message": msg}, a.json, lambda: print(("+ " if ok else "! ") + msg))
    return 0 if ok else 1


def cmd_config(a):
    cfg = config.load()
    if a.set:
        for kv in a.set:
            k, _, v = kv.partition("=")
            if k not in config.DEFAULTS:
                print(f"! unknown setting {k!r} — see `fossypaper config` for the list")
                return 1
            try:
                cfg[k] = config.coerce(k, v)
            except ValueError as e:
                print("!", e); return 1
        config.save(cfg)
        print("saved")
        return 0
    if a.clear_cache:
        n = sources.clear_cache() + engine.clear_thumbnails()
        print(f"cleared {n} cached image(s)")
        return 0
    if a.reset:
        config.save({**config.DEFAULTS,
                     **{k: cfg[k] for k in ("current", "properties") if k in cfg}})
        print("settings reset (library state kept)")
        return 0
    _emit(cfg, a.json, lambda: print(json.dumps(cfg, indent=2)))
    return 0


def cmd_daemon(a):
    ok, msg = engine.apply_current()
    print(msg)
    return 0


def cmd_autostart(a):
    if a.state == "off":
        engine.remove_service(); print("login autostart disabled")
    else:
        print(f"login autostart enabled -> {engine.install_service()}")
    return 0


def cmd_sddm(a):
    cfg = config.load()
    wid = a.id or cfg.get("current")
    if not wid:
        print("no wallpaper set — apply one first, or pass an id"); return 1
    ok, msg, info = engine.sddm_prepare(wid, config.opts(cfg))
    if not ok:
        print("!", msg); return 1
    print("+ rendered SDDM login background:", info["still"])
    print("install it (SDDM is system-level, needs root):")
    for c in info["install"]:
        print("   ", c)
    return 0


def cmd_doctor(a):
    """What's here, what isn't, and what that costs you."""
    b = engine.backends_status()
    lib = engine.scan_library()
    bad = [w for w in lib if not engine.backend_for(w)[1]]
    lines = [
        ("linux-wallpaperengine", b["wpe"], "WE scene wallpapers"),
        ("mpvpaper", b["mpvpaper"], "video wallpapers, and stills without swww"),
        ("swww", b["swww"], "still images (optional — mpvpaper covers it)"),
        ("steamcmd", bool(engine.which("steamcmd")),
         "downloading Workshop items in-app (optional — Steam can do it)"),
        ("ffmpeg", bool(engine.which("ffmpeg")),
         "palette extraction from video wallpapers"),
    ]
    print(f"library roots : {engine.WE_DIR}")
    print(f"wallpapers    : {len(lib)}  ({len(bad)} not renderable here)")
    print(f"outputs       : {', '.join(engine.outputs()) or 'none detected'}")
    comp = engine.compositor()
    o = config.opts(config.load())
    h = engine.host(o)
    print(f"compositor    : {comp}")
    print(f"desktop host  : {h}" + ("  (auto)" if o.get("host", "auto") == "auto" else "  (forced)"))
    if h == "layer":
        shell = engine.backdrop_shell()
        print(f"layer         : {engine.resolved_layer(o)}"
              + (f"  ({o['layer']}; {shell} draws its own backdrop)" if o["layer"] == "auto" and shell
                 else f"  ({o['layer']})"))
    print(f"theme backends: {', '.join(engine.theme_backends())}\n")
    for name, ok, why in lines:
        print(f"  [{'x' if ok else ' '}] {name:24} {why}")
    if h == "plasma":
        mod = engine.plasma_scene_module()
        print(f"  [{'x' if engine.plasma_plugin_installed() else ' '}] {'fossypaper Plasma wallpaper':24} "
              "registers as a real wallpaper type (./install.sh puts it in place)")
        print(f"  [{'x' if mod else ' '}] {'native scene renderer':24} live scenes on Plasma "
              "(optional: AUR wallpaper-engine-kde-plugin-git) — otherwise a still frame")
        print(f"  [{'x' if engine.assets_dir(o) else ' '}] {'WE assets folder':24} "
              "scenes need Wallpaper Engine's own assets (Steam install)")
        if mod:
            print("\n  ! the scene renderer package also adds its own wallpaper type. Keep using")
            print("    fossypaper's — only its SceneViewer is loaded. (The older catsout build's")
            print("    type runs an unauthenticated WebSocket helper; never select that one.)")
    if h == "gnome":
        print(f"  [{'x' if engine.hanabi_available() else ' '}] {'Hanabi extension':24} "
              "video wallpapers on GNOME — otherwise a still frame")
        print("\n  i GNOME has no layer-shell, so scenes show as a still frame there.")
    if h == "layer" and comp == "niri" and engine.resolved_layer(o) == "background":
        print("\n  i to keep the wallpaper visible in niri's overview, add to config.kdl:")
        print('      layer-rule {')
        print('          match namespace="^(linux-wallpaperengine|mpvpaper|swww-daemon.*)$"')
        print('          place-within-backdrop true')
        print('      }')
    if h == "layer" and o["layer"] == "auto" and engine.backdrop_shell():
        print(f"\n  i {engine.backdrop_shell()} draws its own wallpaper, so this sits one layer up")
        print("    (bottom). Turn the shell's wallpaper off and it becomes a true background.")
    if engine.fullscreen_blind():
        print(f"\n  ! {comp} gives the renderer no way to see fullscreen windows, so the")
        print("    wallpaper keeps rendering behind games. Turn it off before playing")
        print("    (`fossypaper off`, or `fossypaper toggle` on a hotkey).")
    if bad:
        print("\nnot renderable:")
        for w in bad[:10]:
            print(f"  {w.id:12} {w.title[:40]:40} {engine.why_unsupported(w) or 'missing tool'}")
    return 0


def cmd_privacy(a):
    from pathlib import Path
    name = "TERMS.md" if a.terms else "PRIVACY.md"
    here = Path(__file__).resolve().parent
    for base in (here.parent / "docs", here / "docs", Path("/usr/share/doc/fossypaper")):
        doc = base / name
        if doc.is_file():
            print(doc.read_text())
            return 0
    print(f"{name} isn't installed alongside this copy. It lives in the "
          "repository under docs/, and the short version is: fossypaper opens "
          "no listening socket, sends nothing anywhere unless you use the "
          "browser, and has no telemetry.")
    return 1


def cmd_gui(a):
    from .app import main as m
    return m()


def cmd_tui(a):
    from .tui import main as m
    return m()


# --------------------------------------------------------------------------- #
def build_parser():
    import argparse
    p = argparse.ArgumentParser(
        prog="fossypaper",
        description="fossypaper-engine — a FOSS Wallpaper Engine manager")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("list", help="list the library")
    s.add_argument("--usable-only", action="store_true", help="only what can render here")
    s.add_argument("--type", help="scene | video | image | web")
    s.add_argument("--search", help="filter by title or id")
    s.add_argument("--thumbs", action="store_true",
                   help="also bake a plain PNG of each preview and report its path")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("apply", help="apply a wallpaper"); s.add_argument("id", nargs="?", default="current", help="an id, a title or part of one, or `current` (the default)"); s.set_defaults(fn=cmd_apply)
    sub.add_parser("off", help="turn the wallpaper off").set_defaults(fn=cmd_off)
    sub.add_parser("toggle", help="off if running, restore if not").set_defaults(fn=cmd_toggle)
    sub.add_parser("status", help="running state, current, outputs").set_defaults(fn=cmd_status)

    s = sub.add_parser("theme", help="sync a colour theme from the wallpaper's pixels")
    s.add_argument("id", nargs="?")
    s.add_argument("--backend", choices=["builtin", "matugen", "wallust", "wal"])
    s.set_defaults(fn=cmd_theme)

    s = sub.add_parser("props", help="a wallpaper's customizable properties")
    s.add_argument("id"); s.add_argument("--all", action="store_true",
                                         help="include hidden and non-editable entries")
    s.set_defaults(fn=cmd_props)

    s = sub.add_parser("set-prop", help="set properties on a wallpaper")
    s.add_argument("id"); s.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_set_prop)

    s = sub.add_parser("browse", help="search Wallhaven or the Steam Workshop")
    s.add_argument("source", choices=["wallhaven", "workshop"])
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--sort", help="wallhaven: toplist|relevance|date_added|views|random · "
                                  "workshop: trend|recent|popular")
    s.set_defaults(fn=cmd_browse)

    s = sub.add_parser("get", help="pull a wallpaper into your library by id")
    s.add_argument("id", help="a Workshop id (digits) or a Wallhaven id")
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("config", help="show or change settings")
    s.add_argument("--set", nargs="*", metavar="KEY=VAL")
    s.add_argument("--reset", action="store_true",
                   help="settings back to defaults (your library state is kept)")
    s.add_argument("--clear-cache", action="store_true",
                   help="delete every cached thumbnail, local and downloaded")
    s.set_defaults(fn=cmd_config)

    sub.add_parser("daemon", help="restore the saved wallpaper (login service)").set_defaults(fn=cmd_daemon)
    s = sub.add_parser("autostart", help="restore the wallpaper at login")
    s.add_argument("state", nargs="?", choices=["on", "off"], default="on"); s.set_defaults(fn=cmd_autostart)
    s = sub.add_parser("sddm", help="render the wallpaper as an SDDM login background")
    s.add_argument("id", nargs="?"); s.set_defaults(fn=cmd_sddm)

    sub.add_parser("doctor", help="what's installed, and what it costs you").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("privacy", help="what fossypaper sends, and where")
    s.add_argument("--terms", action="store_true", help="show the terms instead")
    s.set_defaults(fn=cmd_privacy)

    sub.add_parser("gui", help="launch the fossypaper GUI").set_defaults(fn=cmd_gui)
    sub.add_parser("tui", help="launch the lazypaper TUI").set_defaults(fn=cmd_tui)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if not getattr(a, "fn", None):
        return cmd_gui(a)          # bare `fossypaper` -> the GUI
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        return 130
