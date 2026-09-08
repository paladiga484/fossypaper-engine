"""Lazy Paper — fossypaper's LazyVim-flavoured terminal UI.

Two panes: the library on the left (j/k, badges for the backend each wallpaper
uses — WPE/MPV/IMG and whether it's installed), details + live settings + a
colour palette on the right, a keybind bar on the bottom. Filter with /, help
with ?, tweak render settings live (they persist to the shared config). Pure
stdlib curses, and every draw is bounds-clipped so odd terminal sizes never
crash it.
"""
from __future__ import annotations

import curses
import json

from . import engine, config

LOGO = [
    "╻  ┏━┓┏━┓╻ ╻  ┏━┓┏━┓┏━┓┏━┓┏━┓",
    "┃  ┣━┫┏━┛┗┳┛  ┣━┛┣━┫┣━┛┣╸ ┣┳┛",
    "┗━╸╹ ╹┗━╸ ╹   ╹  ╹ ╹╹  ┗━╸╹┗╸",
]
FPS_CYCLE = [30, 60, 24, 45, 90, 120]
LAYER_CYCLE = ["bottom", "background", "top"]
GPU_CYCLE = ["auto", "nvidia", "mesa"]
BADGE = {"wpe": "WPE", "mpvpaper": "MPV", "swww": "IMG"}


def _init_colors():
    curses.start_color(); curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)      # accent / brand
    curses.init_pair(2, curses.COLOR_GREEN, -1)     # ok / solid backend
    curses.init_pair(3, curses.COLOR_YELLOW, -1)    # warn / needs tool
    curses.init_pair(4, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selection
    curses.init_pair(5, curses.COLOR_WHITE, -1)     # normal
    curses.init_pair(6, curses.COLOR_BLUE, -1)      # dim/labels
    curses.init_pair(7, curses.COLOR_MAGENTA, -1)   # palette / values


def A(n):
    return curses.color_pair(n)


BOLD = curses.A_BOLD


def _put(win, y, x, s, attr=0):
    """Bounds-clipped addstr — never raises on edges / small terminals."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    s = str(s)
    if x < 0:
        s = s[-x:]; x = 0
    s = s[: max(0, w - 1 - x)]
    try:
        win.addstr(y, x, s, attr)
    except curses.error:
        pass


def _box(win, y, x, h, w, title=""):
    _put(win, y, x, "╭" + "─" * (w - 2) + "╮", A(6))
    for i in range(1, h - 1):
        _put(win, y + i, x, "│", A(6)); _put(win, y + i, x + w - 1, "│", A(6))
    _put(win, y + h - 1, x, "╰" + "─" * (w - 2) + "╯", A(6))
    if title:
        _put(win, y, x + 2, f"┤ {title} ├", A(1) | BOLD)


class State:
    def __init__(self):
        self.cfg = config.load()
        self.lib = engine.scan_library()
        self.sel = 0
        self.top = 0
        self.filter = ""
        self.query_mode = False
        self.help = False
        self.status = "ready — ⏎ apply · t theme · / filter · ? help"
        self.palette = []

    def view(self):
        if not self.filter:
            return self.lib
        f = self.filter.lower()
        return [w for w in self.lib if f in w.title.lower()]


def _backends_line(st):
    b = engine.backends_status()
    parts = []
    for k, name in (("wpe", "wpe"), ("mpvpaper", "mpv"), ("swww", "swww")):
        parts.append((f"{name}", 2 if b[k] else 3))
    return parts


def _draw(stdscr, st):
    stdscr.erase()
    H, W = stdscr.getmaxyx()
    view = st.view()
    if st.sel >= len(view):
        st.sel = max(0, len(view) - 1)

    # header: logo + backends + current
    for i, ln in enumerate(LOGO):
        _put(stdscr, i, 2, ln, A(1) | BOLD)
    bx = 34
    _put(stdscr, 0, bx, "fossypaper · backends:", A(6))
    cx = bx
    for name, col in _backends_line(st):
        _put(stdscr, 1, cx, name, A(col) | BOLD); cx += len(name) + 2
    cur = st.cfg.get("current", "")
    curt = next((w.title for w in st.lib if w.id == cur), "—")
    _put(stdscr, 2, bx, f"now: {curt[:W-bx-6]}", A(7))
    running = "▶ playing" if engine.is_running() else "■ stopped"
    _put(stdscr, 2, W - 12, running, A(2) if engine.is_running() else A(6))

    top_y = len(LOGO) + 1
    listw = max(32, W // 2)
    listh = H - top_y - 3

    # left: library
    ftitle = f"LIBRARY · {len(view)}" + (f" · /{st.filter}" if st.filter else "")
    _box(stdscr, top_y, 1, listh, listw, ftitle)
    rows = listh - 2
    if st.sel < st.top:
        st.top = st.sel
    elif st.sel >= st.top + rows:
        st.top = st.sel - rows + 1
    for r in range(rows):
        i = st.top + r
        if i >= len(view):
            break
        w = view[i]
        be, ok = engine.backend_of(w.id)
        badge = BADGE[be] + ("" if ok else "!")
        bcol = 2 if (ok and be in ("mpvpaper", "swww")) else 3
        mark = "▸" if i == st.sel else " "
        cursign = "●" if w.id == cur else " "
        line = f" {mark}{cursign} {w.title}"
        attr = A(4) | BOLD if i == st.sel else A(5)
        _put(stdscr, top_y + 1 + r, 2, line.ljust(listw - 8), attr)
        _put(stdscr, top_y + 1 + r, listw - 5, badge, (A(4) if i == st.sel else A(bcol)) | BOLD)

    # right: details + settings + palette
    rx = listw + 2
    rw = W - rx - 1
    _box(stdscr, top_y, rx, listh, rw, "DETAILS")
    if view:
        w = view[st.sel]
        be, ok = engine.backend_of(w.id)
        rends = {"mpvpaper": "video → mpvpaper", "swww": "image → swww",
                 "wpe": "scene → linux-wpe"}[be] + ("" if ok else "  (install it)")
        info = [("title", w.title), ("id", w.id), ("backend", rends)]
        for j, (k, v) in enumerate(info):
            _put(stdscr, top_y + 2 + j, rx + 3, f"{k:>8}: ", A(6))
            _put(stdscr, top_y + 2 + j, rx + 13, str(v)[: rw - 15],
                 A(3) if k == "backend" and not ok else A(5))
        o = config.opts(st.cfg)
        _put(stdscr, top_y + 6, rx + 3, "SETTINGS", A(1) | BOLD)
        sets = [("F  fps", o["fps"]), ("L  layer", o["layer"]),
                ("V  gpu", o["gpu"]), ("O  output", o["output"])]
        for j, (k, v) in enumerate(sets):
            _put(stdscr, top_y + 7 + j, rx + 3, f"{k:<10}", A(6))
            _put(stdscr, top_y + 7 + j, rx + 14, str(v), A(7) | BOLD)
        _put(stdscr, top_y + 12, rx + 3, "PALETTE  (t to extract)", A(1) | BOLD)
        for c, _hex in enumerate(st.palette[:8]):
            _put(stdscr, top_y + 13, rx + 3 + c * 3, "██", A(7))
        _put(stdscr, listh + top_y - 2, rx + 3, st.status[: rw - 5], A(2) | BOLD)

    # footer
    if st.query_mode:
        keys = f"  /{st.filter}▏   (type to filter · ⏎ done · esc clear)"
    elif st.help:
        keys = "  j/k move · ⏎ apply · o off · t theme · d download · F/L/V/O settings · / filter · r refresh · q quit"
    else:
        keys = "  j/k move   ⏎ apply   o off   t theme   F·L·V·O tweak   / filter   ? help   q quit"
    _put(stdscr, H - 1, 0, keys.ljust(W - 1), A(4) | BOLD)
    stdscr.refresh()


def run(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    _init_colors()
    st = State()

    while True:
        _draw(stdscr, st)
        k = stdscr.getch()

        if st.query_mode:                       # filter typing
            if k in (10, 13):
                st.query_mode = False
            elif k == 27:
                st.query_mode = False; st.filter = ""
            elif k in (curses.KEY_BACKSPACE, 127, 8):
                st.filter = st.filter[:-1]
            elif 32 <= k < 127:
                st.filter += chr(k)
            st.sel = 0
            continue

        view = st.view()
        if k in (ord("q"), 27):
            break
        elif k in (ord("j"), curses.KEY_DOWN):
            st.sel = min(st.sel + 1, max(0, len(view) - 1))
        elif k in (ord("k"), curses.KEY_UP):
            st.sel = max(st.sel - 1, 0)
        elif k == ord("g"):
            st.sel = 0
        elif k == ord("G"):
            st.sel = max(0, len(view) - 1)
        elif k == ord("/"):
            st.query_mode = True; st.filter = ""
        elif k == ord("?"):
            st.help = not st.help
        elif k in (10, 13, curses.KEY_ENTER) and view:
            w = view[st.sel]; be, ok = engine.backend_of(w.id)
            if not ok:
                st.status = f"needs {be} — paru -S {be}"
            else:
                o = config.opts(st.cfg); o["properties"] = st.cfg.get("properties", {}).get(w.id, {})
                engine.start(w.id, o)
                st.cfg["current"] = w.id; config.save(st.cfg)
                st.status = f"applied ✓  {w.title[:36]}"
        elif k == ord("o"):
            engine.stop(); st.status = "wallpaper off"
        elif k == ord("t") and view:
            st.status = "rendering a frame + extracting colours…"
            _draw(stdscr, st)
            ok, msg = engine.sync_theme(view[st.sel].id, config.opts(st.cfg), "builtin")
            st.status = msg
            try:
                st.palette = json.loads((engine.STATE / "colors.json").read_text())["roles"]["palette"]
            except Exception:
                st.palette = []
        elif k == ord("d") and view:
            st.status = "pulling from Workshop…"; _draw(stdscr, st)
            ok, msg = engine.download_workshop(view[st.sel].id); st.status = msg
        elif k == ord("r"):
            st.lib = engine.scan_library(); st.status = "refreshed"
        # live settings (persist to shared config)
        elif k == ord("F"):
            o = config.opts(st.cfg); st.cfg["fps"] = FPS_CYCLE[(FPS_CYCLE.index(o["fps"]) + 1) % len(FPS_CYCLE)] if o["fps"] in FPS_CYCLE else 60; config.save(st.cfg); st.status = f"fps → {st.cfg['fps']}"
        elif k == ord("L"):
            o = config.opts(st.cfg); st.cfg["layer"] = LAYER_CYCLE[(LAYER_CYCLE.index(o["layer"]) + 1) % len(LAYER_CYCLE)] if o["layer"] in LAYER_CYCLE else "bottom"; config.save(st.cfg); st.status = f"layer → {st.cfg['layer']}"
        elif k == ord("V"):
            o = config.opts(st.cfg); st.cfg["gpu"] = GPU_CYCLE[(GPU_CYCLE.index(o["gpu"]) + 1) % len(GPU_CYCLE)] if o["gpu"] in GPU_CYCLE else "auto"; config.save(st.cfg); st.status = f"gpu → {st.cfg['gpu']}"
        elif k == ord("O"):
            outs = engine.outputs() or ["eDP-1"]; o = config.opts(st.cfg)
            st.cfg["output"] = outs[(outs.index(o["output"]) + 1) % len(outs)] if o["output"] in outs else outs[0]; config.save(st.cfg); st.status = f"output → {st.cfg['output']}"


def main():
    if not engine.WE_DIR.is_dir():
        print(f"Lazy Paper: no wallpaper library at {engine.WE_DIR}\n"
              "Set FOSSYPAPER_LIBRARY, or subscribe to WE scenes so Steam syncs them.")
        return 1
    try:
        curses.wrapper(run)
    except curses.error as e:
        print(f"Lazy Paper needs a real terminal ({e}).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
