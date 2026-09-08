"""fossypaper CLI — scriptable subcommands over the engine."""
import argparse, json
from . import engine, config


def _find(wid):
    return next((w for w in engine.scan_library() if w.id == wid), None)


def cmd_list(a):
    tag = {"wpe": "scene", "mpvpaper": "video", "swww": "image"}
    for w in engine.scan_library():
        be, ok = engine.backend_of(w.id)
        if a.gl_only and not ok:
            continue
        print(f"{tag[be]:5} {'✓' if ok else '✗ need '+be:8}  {w.id}  {w.title}")


def cmd_apply(a):
    cfg = config.load(); o = config.opts(cfg)
    w = _find(a.id)
    if not w:
        print("no such wallpaper:", a.id); return 1
    be, ok = engine.backend_of(a.id)
    if not ok:
        print(f"⚠ this one renders via {be}, which isn't installed — paru -S {be}")
    o["properties"] = cfg.get("properties", {}).get(a.id, {})
    engine.start(a.id, o)
    cfg["current"] = a.id; config.save(cfg)
    print("applied:", w.title)


def cmd_off(a):
    engine.stop(); print("off")


def cmd_status(a):
    cfg = config.load()
    print(("running" if engine.is_running() else "stopped") + " · current: "
          + (cfg.get("current") or "—"))


def cmd_theme(a):
    cfg = config.load(); wid = a.id or cfg.get("current")
    if not wid:
        print("no wallpaper set — apply one first, or pass an id"); return 1
    ok, msg = engine.sync_theme(wid, config.opts(cfg),
                                a.backend or cfg.get("theme_backend", "builtin"))
    print(("✓ " if ok else "✗ ") + msg); return 0 if ok else 1


def cmd_props(a):
    ps = engine.list_properties(a.id)
    if not ps:
        print("no properties (or wallpaper not found)"); return 1
    for p in ps:
        rng = f"  [{p.mn}..{p.mx}]" if p.kind == "slider" else ""
        print(f"{p.key:20} {p.kind:10} = {p.value}{rng}   ({p.text})")


def cmd_download(a):
    ok, msg = engine.download_workshop(a.id); print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


def cmd_config(a):
    cfg = config.load()
    if a.set:
        for kv in a.set:
            k, _, v = kv.partition("=")
            cfg[k] = True if v == "true" else False if v == "false" else int(v) if v.lstrip("-").isdigit() else v
        config.save(cfg); print("saved")
    else:
        print(json.dumps(cfg, indent=2))


def cmd_daemon(a):
    ok = engine.apply_current()
    print("restored wallpaper" if ok else "nothing saved to restore")
    return 0


def cmd_autostart(a):
    if a.state == "off":
        engine.remove_service(); print("login autostart disabled")
    else:
        p = engine.install_service()
        print(f"login autostart enabled → {p}\n(restores your current wallpaper each login)")
    return 0


def cmd_sddm(a):
    cfg = config.load(); wid = a.id or cfg.get("current")
    if not wid:
        print("no wallpaper set — apply one first, or pass an id"); return 1
    ok, msg, info = engine.sddm_prepare(wid, config.opts(cfg))
    if not ok:
        print("✗", msg); return 1
    print("✓ rendered SDDM login background:", info["still"])
    print("install it (SDDM is system-level, needs root):")
    for c in info["install"]:
        print("   ", c)
    print("   then set that theme/background as Current in /etc/sddm.conf.d/")
    return 0


def cmd_gui(a):
    from .app import main as m; return m()


def cmd_tui(a):
    from .tui import main as m; return m()


def build_parser():
    p = argparse.ArgumentParser(prog="fossypaper",
                                description="fossypaper-engine — a FOSS Wallpaper Engine manager")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("list", help="list the library"); s.add_argument("--gl-only", action="store_true"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("apply", help="apply a wallpaper"); s.add_argument("id"); s.set_defaults(fn=cmd_apply)
    sub.add_parser("off", help="turn the wallpaper off").set_defaults(fn=cmd_off)
    sub.add_parser("status", help="running state + current").set_defaults(fn=cmd_status)
    s = sub.add_parser("theme", help="sync a colour theme from the wallpaper's pixels")
    s.add_argument("id", nargs="?"); s.add_argument("--backend", choices=["builtin", "matugen", "wallust", "wal"]); s.set_defaults(fn=cmd_theme)
    s = sub.add_parser("props", help="list a wallpaper's customizable properties"); s.add_argument("id"); s.set_defaults(fn=cmd_props)
    s = sub.add_parser("download", help="pull a wallpaper from the Workshop web"); s.add_argument("id"); s.set_defaults(fn=cmd_download)
    s = sub.add_parser("config", help="show or set settings"); s.add_argument("--set", nargs="*", metavar="KEY=VAL"); s.set_defaults(fn=cmd_config)
    sub.add_parser("daemon", help="restore the saved wallpaper (used by the login service)").set_defaults(fn=cmd_daemon)
    s = sub.add_parser("autostart", help="enable/disable restoring the wallpaper at login")
    s.add_argument("state", nargs="?", choices=["on", "off"], default="on"); s.set_defaults(fn=cmd_autostart)
    s = sub.add_parser("sddm", help="render the wallpaper as an SDDM login background")
    s.add_argument("id", nargs="?"); s.set_defaults(fn=cmd_sddm)
    sub.add_parser("gui", help="launch the Qt GUI").set_defaults(fn=cmd_gui)
    sub.add_parser("tui", help="launch the lazypaper TUI").set_defaults(fn=cmd_tui)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    if not getattr(a, "fn", None):
        return cmd_gui(a)          # bare `fossypaper` → the GUI
    return a.fn(a) or 0
