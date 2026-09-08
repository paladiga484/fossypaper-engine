"""lazypaper — fossypaper's terminal side.

Same shape as it always had: library on the left, details on the right, keybinds
along the bottom. What's new is depth, not layout — a property editor for the
wallpaper's own knobs, and a browser over Wallhaven and the Steam Workshop.

On colour: lazypaper never sets a background and never sets bold. It draws with
the sixteen colours your terminal already defines, on your terminal's own
background, so it looks like the rest of your setup instead of fighting it.
The selection bar is reverse video for the same reason — reverse is correct on
a light theme and a dark one, a hardcoded pair is correct on one of them.
"""
from __future__ import annotations

import curses
import os
import textwrap
import unicodedata

# ncurses waits a full second after ESC to see whether an escape *sequence* is
# arriving. That makes the back key feel broken. 25ms is plenty on a local tty.
os.environ.setdefault("ESCDELAY", "25")

from . import config, engine, properties, sources

# --------------------------------------------------------------------------- #
#  Wordmark — cut by hand, ASCII only, in the manner of the classic figlet slabs
# --------------------------------------------------------------------------- #
LOGO = [
    r"  _                    ____                       ",
    r" | |    __ _  ____ _  _|  _ \ __ _  ____   ___ _ _ ",
    r" | |   / _` ||_  /| || | |_) / _` || '_ \ / _ \ '_|",
    r" | |__| (_| | / /_| || |  __/ (_| || |_) |  __/ |  ",
    r" |____|\__,_|/____|\_, |_|   \__,_|| .__/ \___|_|  ",
    r"                   |___/           |_|             ",
]
LOGO_SMALL = [
    r" |  _  _   |) _  _  _  _",
    r" | (_|/_\/ |'(_||_)(/_|",
    r"        _/         |    ",
]

FPS_CYCLE = [30, 60, 24, 45, 90, 120, 144]
LAYER_CYCLE = ["bottom", "background", "top"]
GPU_CYCLE = ["auto", "nvidia", "mesa"]
SCALE_CYCLE = ["", "default", "stretch", "fit", "fill"]
AUDIO_CYCLE = ["auto", "always", "never"]
TAG = {"wpe": "scene", "mpvpaper": "video", "swww": "image",
       "web": " web ", "application": " app "}

# colour roles -> curses pair ids. Foregrounds only; the background stays the
# terminal's own (-1), which is what makes this match your theme.
ACCENT, OK, WARN, DIM, VALUE, ERR = 1, 2, 3, 4, 5, 6


def _init_colors():
    curses.start_color()
    curses.use_default_colors()
    for pair, fg in ((ACCENT, curses.COLOR_CYAN), (OK, curses.COLOR_GREEN),
                     (WARN, curses.COLOR_YELLOW), (DIM, curses.COLOR_BLUE),
                     (VALUE, curses.COLOR_MAGENTA), (ERR, curses.COLOR_RED)):
        curses.init_pair(pair, fg, -1)


def A(role=0):
    return curses.color_pair(role)


SEL = curses.A_REVERSE


def cells(s: str) -> int:
    """Terminal columns a string occupies. CJK titles are two cells per glyph,
    and this library is full of them — measuring in characters puts the badge
    column and the right border in different places on every other row."""
    n = 0
    for ch in str(s):
        if unicodedata.combining(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def fit(s: str, width: int) -> str:
    """Truncate to `width` *columns*, never splitting a wide glyph in half."""
    if width <= 0:
        return ""
    out, n = [], 0
    for ch in str(s):
        c = 0 if unicodedata.combining(ch) else \
            (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if n + c > width:
            break
        out.append(ch)
        n += c
    return "".join(out)


def pad(s: str, width: int) -> str:
    """Truncate then pad to exactly `width` columns."""
    s = fit(s, width)
    return s + " " * max(0, width - cells(s))


def _put(win, y, x, s, attr=0):
    """Bounds-clipped addstr — never raises on edges or small terminals."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    s = str(s)
    if x < 0:
        s = s[-x:]
        x = 0
    s = fit(s, max(0, w - 1 - x))
    try:
        win.addstr(y, x, s, attr)
    except curses.error:
        pass


def _box(win, y, x, h, w, title=""):
    if h < 2 or w < 4:
        return
    _put(win, y, x, "+" + "-" * (w - 2) + "+", A(DIM))
    for i in range(1, h - 1):
        _put(win, y + i, x, "|", A(DIM))
        _put(win, y + i, x + w - 1, "|", A(DIM))
    _put(win, y + h - 1, x, "+" + "-" * (w - 2) + "+", A(DIM))
    if title:
        _put(win, y, x + 2, f" {title} ", A(ACCENT))


# --------------------------------------------------------------------------- #
class State:
    def __init__(self):
        self.cfg = config.load()
        self.lib = engine.scan_library()
        self.mode = "library"          # library | props | browse | help
        self.sel = 0
        self.top = 0
        self.filter = ""
        self.typing = None             # None | "filter" | "query" | "value"
        self.buffer = ""
        self.status = "ready"
        self.palette = engine.palette()

        # property editor
        self.props: list = []
        self.prop_sel = 0
        self.prop_top = 0
        self.prop_values: dict = {}
        self.prop_wid = ""

        # browser
        self.source = "wallhaven"
        self.query = ""
        self.page = 1
        self.last_page = 1
        self.rows: list = []
        self.row_sel = 0
        self.row_top = 0

    # -- library ---------------------------------------------------------- #
    def view(self):
        if not self.filter:
            return self.lib
        f = self.filter.lower()
        return [w for w in self.lib if f in w.title.lower() or f in w.id]

    def current(self):
        v = self.view()
        return v[self.sel] if v and 0 <= self.sel < len(v) else None

    def visible_props(self):
        return [p for p in self.props
                if p.editable and properties.visible(p, self.prop_values)]


# --------------------------------------------------------------------------- #
#  Drawing
# --------------------------------------------------------------------------- #
def _header(scr, st, W):
    logo = LOGO if W >= 92 else LOGO_SMALL
    for i, ln in enumerate(logo):
        _put(scr, i, 1, ln, A(ACCENT))
    bx = (len(logo[0]) if logo is LOGO_SMALL else 52) + 3
    if W - bx < 24:
        return len(logo)
    b = engine.backends_status()
    _put(scr, 0, bx, "backends", A(DIM))
    x = bx + 9
    for key, name in (("wpe", "wpe"), ("mpvpaper", "mpv"), ("swww", "swww")):
        _put(scr, 0, x, name, A(OK) if b[key] else A(WARN))
        x += len(name) + 2
    cur = st.cfg.get("current", "")
    title = next((w.title for w in st.lib if w.id == cur), "-")
    _put(scr, 1, bx, "now     ", A(DIM))
    _put(scr, 1, bx + 9, title[: max(0, W - bx - 11)], A(VALUE))
    running = engine.is_running()
    _put(scr, 2, bx, "state   ", A(DIM))
    _put(scr, 2, bx + 9, "playing" if running else "stopped", A(OK) if running else A(DIM))
    if st.palette:
        _put(scr, 3, bx, "palette ", A(DIM))
        for i, hexv in enumerate(st.palette[:8]):
            _put(scr, 3, bx + 9 + i * 3, "###", A(_swatch(i, hexv)))
    return len(logo)


_SWATCH_BASE = 20


def _swatch(i: int, hexv: str) -> int:
    """Approximate a palette colour with the terminal's own 256-colour cube, so
    the swatches are real colours instead of a fixed six."""
    if curses.COLORS < 256 or curses.COLOR_PAIRS < _SWATCH_BASE + 8:
        return ACCENT
    try:
        r, g, b = (int(hexv[k:k + 2], 16) for k in (1, 3, 5))
    except (ValueError, IndexError):
        return ACCENT
    idx = 16 + 36 * (r * 5 // 255) + 6 * (g * 5 // 255) + (b * 5 // 255)
    pair = _SWATCH_BASE + i
    try:
        curses.init_pair(pair, idx, -1)
    except curses.error:
        return ACCENT
    return pair


def _draw_library(scr, st, top_y, H, W):
    view = st.view()
    if st.sel >= len(view):
        st.sel = max(0, len(view) - 1)
    listw = max(30, min(W // 2, 60))
    listh = H - top_y - 2

    title = f"LIBRARY {len(view)}" + (f"  /{st.filter}" if st.filter else "")
    _box(scr, top_y, 1, listh, listw, title)
    rows = listh - 2
    if st.sel < st.top:
        st.top = st.sel
    elif st.sel >= st.top + rows:
        st.top = st.sel - rows + 1
    cur = st.cfg.get("current", "")
    for r in range(rows):
        i = st.top + r
        if i >= len(view):
            break
        w = view[i]
        be, ok = engine.backend_for(w)
        mark = "*" if w.id == cur else " "
        badge = TAG.get(be, be)[:5]
        line = pad(f" {mark} {w.title}", listw - 11) + "  "
        if i == st.sel:
            _put(scr, top_y + 1 + r, 2, pad(line + badge, listw - 3), SEL)
        else:
            _put(scr, top_y + 1 + r, 2, line, A())
            _put(scr, top_y + 1 + r, 2 + listw - 9, badge, A(OK) if ok else A(WARN))

    rx = listw + 2
    rw = W - rx - 1
    if rw < 24:
        return
    _box(scr, top_y, rx, listh, rw, "DETAILS")
    w = st.current()
    if w is None:
        _put(scr, top_y + 2, rx + 3, "library is empty", A(DIM))
        return
    be, ok = engine.backend_for(w)
    reason = engine.why_unsupported(w)
    renders = {"mpvpaper": "video -> mpvpaper", "swww": "image -> swww",
               "wpe": "scene -> linux-wallpaperengine"}.get(be, be)
    if not ok:
        renders += "  (unavailable)"
    y = top_y + 1
    kind = w.type + (" (video-backed)" if w.video and w.type != "video" else "")
    for k, v, col in (("title", w.title, VALUE), ("id", w.id, 0),
                      ("kind", kind, 0), ("renders", renders, 0 if ok else WARN),
                      ("audio", "reactive" if w.audio else "silent", 0),
                      ("knobs", str(sum(p.editable for p in engine.list_properties(w.id))), 0)):
        _put(scr, y, rx + 2, f"{k:>8}", A(DIM))
        _put(scr, y, rx + 11, fit(v, rw - 13), A(col))
        y += 1
    if reason:
        for ln in textwrap.wrap(reason, rw - 5)[:3]:
            _put(scr, y, rx + 2, ln, A(WARN)); y += 1

    y += 1
    _put(scr, y, rx + 2, "SETTINGS", A(ACCENT)); y += 1
    o = config.opts(st.cfg)
    for k, v in (("f  fps", o["fps"]), ("l  layer", o["layer"]), ("v  gpu", o["gpu"]),
                 ("s  scaling", o["scaling"] or "auto"),
                 ("o  output", o["output"] or "all"),
                 ("a  audio", o["audio_processing"]),
                 ("m  mute", "on" if o["silent"] else f"vol {o['volume']}"),
                 ("x  pause", "on fullscreen" if o["fullscreen_pause"] else "never")):
        if y >= top_y + listh - 2:
            break
        _put(scr, y, rx + 2, f"{k:<12}", A(DIM))
        _put(scr, y, rx + 15, str(v)[: rw - 17], A(VALUE))
        y += 1


def _draw_props(scr, st, top_y, H, W):
    listh = H - top_y - 2
    _box(scr, top_y, 1, listh, W - 2, f"PROPERTIES  {st.prop_wid}")
    vis = st.visible_props()
    if not vis:
        _put(scr, top_y + 2, 4, "this wallpaper exposes no adjustable properties", A(DIM))
        return
    st.prop_sel = max(0, min(st.prop_sel, len(vis) - 1))
    rows = listh - 3
    if st.prop_sel < st.prop_top:
        st.prop_top = st.prop_sel
    elif st.prop_sel >= st.prop_top + rows:
        st.prop_top = st.prop_sel - rows + 1
    for r in range(rows):
        i = st.prop_top + r
        if i >= len(vis):
            break
        p = vis[i]
        val = st.prop_values.get(p.key, p.value)
        if p.kind == "color":
            rr, gg, bb = properties.color_to_rgb(val)
            val = f"#{int(rr*255):02x}{int(gg*255):02x}{int(bb*255):02x}"
        label = " " + pad(p.text, 38) + " " + pad(p.kind, 11) + " "
        y = top_y + 1 + r
        if i == st.prop_sel:
            _put(scr, y, 2, pad(label + str(val), W - 5), SEL)
        else:
            _put(scr, y, 2, label, A())
            _put(scr, y, 2 + cells(label), fit(val, W - 8 - cells(label)), A(VALUE))
    p = vis[st.prop_sel]
    hint = {"slider": f"range {p.mn:g}..{p.mx:g} step {p.step:g}  ( h/l adjust )",
            "bool": "( space toggles )", "combo": "( h/l cycles )",
            "color": "( e types r g b, 0..1 )",
            "textinput": "( e edits )", "scenetexture": "( e sets a texture path )"}.get(p.kind, "")
    _put(scr, top_y + listh - 2, 3, f"{p.key}   {hint}"[: W - 6], A(DIM))


def _draw_browse(scr, st, top_y, H, W):
    listh = H - top_y - 2
    dl = sources.available()
    title = (f"BROWSE  {st.source}" + (f"  \"{st.query}\"" if st.query else "")
             + f"   page {st.page}/{st.last_page}")
    _box(scr, top_y, 1, listh, W - 2, title)
    if not st.rows:
        _put(scr, top_y + 2, 4, "press / to search, tab to switch source", A(DIM))
        _put(scr, top_y + 4, 4, "wallhaven  stills, downloaded straight into your library",
             A())
        _put(scr, top_y + 5, 4,
             "workshop   Wallpaper Engine scenes; " +
             ("steamcmd will fetch them" if dl["workshop_download"]
              else "opens in Steam to subscribe (no steamcmd here)"), A())
        return
    rows = listh - 2
    st.row_sel = max(0, min(st.row_sel, len(st.rows) - 1))
    if st.row_sel < st.row_top:
        st.row_top = st.row_sel
    elif st.row_sel >= st.row_top + rows:
        st.row_top = st.row_sel - rows + 1
    for r in range(rows):
        i = st.row_top + r
        if i >= len(st.rows):
            break
        row = st.rows[i]
        have = "*" if row.installed() else " "
        line = f" {have} {row.id:12} " + pad(row.title, 44) + " " + row.meta
        y = top_y + 1 + r
        if i == st.row_sel:
            _put(scr, y, 2, pad(line, W - 5), SEL)
        else:
            _put(scr, y, 2, line, A(OK) if row.installed() else A())


HELP = [
    ("j k  up down", "move"),
    ("g G", "first / last"),
    ("enter", "apply the selected wallpaper"),
    ("space", "stop / restart the wallpaper"),
    ("p", "property editor for this wallpaper"),
    ("b", "browse Wallhaven and the Steam Workshop"),
    ("t", "sync a colour theme from its pixels"),
    ("/", "filter the library (esc clears)"),
    ("r", "rescan the library"),
    ("f l v s o a m x", "fps, layer, gpu, scaling, output, audio, mute, pause"),
    ("R", "reset this wallpaper's properties"),
    ("?", "this help"),
    ("q", "quit"),
]


def _draw_help(scr, st, top_y, H, W):
    _box(scr, top_y, 1, H - top_y - 2, W - 2, "KEYS")
    for i, (k, v) in enumerate(HELP):
        y = top_y + 1 + i
        if y >= H - 3:
            break
        _put(scr, y, 4, f"{k:<18}", A(ACCENT))
        _put(scr, y, 23, v, A())
    _put(scr, H - 4, 4, "lazypaper drives the same config as fossypaper and the CLI.", A(DIM))


FOOTERS = {
    "library": " j/k move   enter apply   space stop   p props   b browse   t theme   / filter   ? keys   q quit",
    "props":   " j/k move   h/l adjust   space toggle   e edit   R reset   enter apply   esc back",
    "browse":  " j/k move   / search   tab source   n/N page   enter get   o open page   esc back",
    "help":    " any key returns",
}


def _draw(scr, st):
    scr.erase()
    H, W = scr.getmaxyx()
    if H < 12 or W < 50:
        _put(scr, 0, 0, "lazypaper needs a little more room", A(WARN))
        scr.refresh()
        return
    top_y = _header(scr, st, W) + 1
    {"library": _draw_library, "props": _draw_props,
     "browse": _draw_browse, "help": _draw_help}[st.mode](scr, st, top_y, H, W)

    _put(scr, H - 2, 2, st.status[: W - 4], A(OK) if not st.status.startswith("!") else A(ERR))
    if st.typing:
        prompt = {"filter": "filter", "query": "search", "value": "value"}[st.typing]
        _put(scr, H - 1, 0, pad(f" {prompt}: {st.buffer}_", W - 1), SEL)
    else:
        _put(scr, H - 1, 0, pad(FOOTERS[st.mode], W - 1), SEL)
    scr.refresh()


# --------------------------------------------------------------------------- #
#  Actions
# --------------------------------------------------------------------------- #
def _apply(st, wid):
    o = config.opts(st.cfg)
    o["properties"] = st.cfg.get("properties", {}).get(wid, {})
    ok, msg = engine.start(wid, o)
    if ok:
        st.cfg["current"] = wid
        config.save(st.cfg)
    st.status = ("" if ok else "! ") + msg


def _cycle(st, key, cycle, label):
    cur = st.cfg.get(key)
    nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else cycle[0]
    st.cfg[key] = nxt
    config.save(st.cfg)
    st.status = f"{label} -> {nxt or 'auto'}"


def _load_props(st, wid):
    st.props = engine.list_properties(wid)
    st.prop_wid = wid
    st.prop_sel = st.prop_top = 0
    saved = st.cfg.get("properties", {}).get(wid, {})
    st.prop_values = {**properties.defaults(st.props), **saved}


def _save_props(st):
    saved = st.cfg.setdefault("properties", {})
    base = properties.defaults(st.props)
    changed = {k: v for k, v in st.prop_values.items() if str(base.get(k)) != str(v)}
    if changed:
        saved[st.prop_wid] = changed
    else:
        saved.pop(st.prop_wid, None)
    config.save(st.cfg)


def _adjust(st, delta):
    vis = st.visible_props()
    if not vis:
        return
    p = vis[st.prop_sel]
    val = st.prop_values.get(p.key, p.value)
    if p.kind == "slider":
        try:
            n = float(val)
        except ValueError:
            n = p.mn
        n = max(p.mn, min(p.mx, n + delta * (p.step or 0.01)))
        st.prop_values[p.key] = f"{n:.{max(0, p.precision)}f}".rstrip("0").rstrip(".") or "0"
    elif p.kind == "bool":
        st.prop_values[p.key] = "false" if str(val).lower() == "true" else "true"
    elif p.kind == "combo" and p.options:
        vals = [v for _lb, v in p.options]
        i = vals.index(str(val)) if str(val) in vals else 0
        st.prop_values[p.key] = vals[(i + delta) % len(vals)]
    else:
        st.status = "press e to edit this one"
        return
    _save_props(st)
    st.status = f"{p.key} = {st.prop_values[p.key]}"


def _search(st):
    st.status = f"searching {st.source}..."
    try:
        if st.source == "wallhaven":
            st.rows, st.last_page = sources.wallhaven(
                st.query, st.page, st.cfg["wallhaven_sort"],
                purity=st.cfg["wallhaven_purity"], api_key=st.cfg["wallhaven_key"])
        else:
            st.rows, st.last_page = sources.workshop(st.query, st.page)
        st.row_sel = st.row_top = 0
        st.status = f"{len(st.rows)} result(s)"
    except sources.SourceError as e:
        st.rows = []
        st.status = f"! {e}"


# --------------------------------------------------------------------------- #
#  Input
# --------------------------------------------------------------------------- #
def _typing(st, k) -> bool:
    if k in (10, 13, curses.KEY_ENTER):
        mode, text = st.typing, st.buffer
        st.typing = None
        if mode == "filter":
            st.filter = text; st.sel = 0
        elif mode == "query":
            st.query = text; st.page = 1; _search(st)
        elif mode == "value":
            vis = st.visible_props()
            if vis:
                st.prop_values[vis[st.prop_sel].key] = text
                _save_props(st)
                st.status = f"{vis[st.prop_sel].key} = {text}"
        return True
    if k == 27:
        st.typing = None
        st.buffer = ""
        return True
    if k in (curses.KEY_BACKSPACE, 127, 8):
        st.buffer = st.buffer[:-1]
    elif 32 <= k < 127:
        st.buffer += chr(k)
    return True


def _keys_library(st, scr, k) -> bool:
    view = st.view()
    if k in (ord("j"), curses.KEY_DOWN):
        st.sel = min(st.sel + 1, max(0, len(view) - 1))
    elif k in (ord("k"), curses.KEY_UP):
        st.sel = max(st.sel - 1, 0)
    elif k == ord("g"):
        st.sel = 0
    elif k == ord("G"):
        st.sel = max(0, len(view) - 1)
    elif k in (10, 13, curses.KEY_ENTER) and view:
        _apply(st, view[st.sel].id)
    elif k == ord(" "):
        if engine.is_running():
            engine.stop(); st.status = "wallpaper off"
        elif st.cfg.get("current"):
            _apply(st, st.cfg["current"])
    elif k == ord("/"):
        st.typing = "filter"; st.buffer = st.filter
    elif k == ord("r"):
        st.lib = engine.scan_library(); st.status = f"{len(st.lib)} wallpapers"
    elif k == ord("p") and view:
        _load_props(st, view[st.sel].id)
        st.mode = "props"
        st.status = f"{len(st.visible_props())} adjustable"
    elif k == ord("b"):
        st.mode = "browse"
        st.status = "browse: / to search, tab to switch source"
    elif k == ord("t") and view:
        st.status = "rendering a frame + reading its colours..."
        _draw(scr, st)
        ok, msg = engine.sync_theme(view[st.sel].id, config.opts(st.cfg),
                                    st.cfg.get("theme_backend", "builtin"))
        st.palette = engine.palette()
        st.status = ("" if ok else "! ") + msg
    elif k == ord("f"):
        _cycle(st, "fps", FPS_CYCLE, "fps")
    elif k == ord("l"):
        _cycle(st, "layer", LAYER_CYCLE, "layer")
    elif k == ord("v"):
        _cycle(st, "gpu", GPU_CYCLE, "gpu")
    elif k == ord("s"):
        _cycle(st, "scaling", SCALE_CYCLE, "scaling")
    elif k == ord("a"):
        _cycle(st, "audio_processing", AUDIO_CYCLE, "audio processing")
    elif k == ord("m"):
        st.cfg["silent"] = not st.cfg.get("silent", True)
        config.save(st.cfg)
        st.status = "muted" if st.cfg["silent"] else f"volume {st.cfg['volume']}"
    elif k == ord("x"):
        st.cfg["fullscreen_pause"] = not st.cfg.get("fullscreen_pause", True)
        config.save(st.cfg)
        st.status = ("pauses behind fullscreen apps" if st.cfg["fullscreen_pause"]
                     else "keeps rendering behind fullscreen apps")
    elif k == ord("o"):
        outs = [""] + engine.outputs()
        cur = st.cfg.get("output", "")
        st.cfg["output"] = outs[(outs.index(cur) + 1) % len(outs)] if cur in outs else ""
        config.save(st.cfg)
        st.status = f"output -> {st.cfg['output'] or 'all'}"
    else:
        return False
    return True


def _keys_props(st, k) -> bool:
    vis = st.visible_props()
    if k in (ord("j"), curses.KEY_DOWN):
        st.prop_sel = min(st.prop_sel + 1, max(0, len(vis) - 1))
    elif k in (ord("k"), curses.KEY_UP):
        st.prop_sel = max(st.prop_sel - 1, 0)
    elif k in (ord("l"), curses.KEY_RIGHT):
        _adjust(st, +1)
    elif k in (ord("h"), curses.KEY_LEFT):
        _adjust(st, -1)
    elif k == ord(" "):
        _adjust(st, +1)
    elif k == ord("e") and vis:
        st.typing = "value"
        st.buffer = str(st.prop_values.get(vis[st.prop_sel].key, ""))
    elif k == ord("R"):
        st.prop_values = properties.defaults(st.props)
        _save_props(st)
        st.status = "properties back to the wallpaper's own defaults"
    elif k in (10, 13, curses.KEY_ENTER):
        _save_props(st)
        _apply(st, st.prop_wid)
    else:
        return False
    return True


def _keys_browse(st, scr, k) -> bool:
    if k in (ord("j"), curses.KEY_DOWN):
        st.row_sel = min(st.row_sel + 1, max(0, len(st.rows) - 1))
    elif k in (ord("k"), curses.KEY_UP):
        st.row_sel = max(0, st.row_sel - 1)
    elif k == ord("/"):
        st.typing = "query"; st.buffer = st.query
    elif k == 9:                                   # tab
        st.source = "workshop" if st.source == "wallhaven" else "wallhaven"
        st.page = 1
        if st.query:
            _search(st)
        else:
            st.rows = []; st.status = f"source -> {st.source}"
    elif k in (ord("n"), curses.KEY_NPAGE):
        st.page = min(st.page + 1, max(1, st.last_page)); _search(st)
    elif k in (ord("N"), curses.KEY_PPAGE):
        st.page = max(1, st.page - 1); _search(st)
    elif k in (10, 13, curses.KEY_ENTER) and st.rows:
        row = st.rows[st.row_sel]
        st.status = f"fetching {row.id}..."
        _draw(scr, st)
        ok, msg = sources.fetch(row)
        st.status = ("" if ok else "! ") + msg
        if ok:
            st.lib = engine.scan_library()
    elif k == ord("o") and st.rows:
        ok, msg = sources.open_in_steam(st.rows[st.row_sel]) \
            if st.rows[st.row_sel].source == "workshop" else (False, "")
        st.status = msg or f"{st.rows[st.row_sel].page_url}"
    else:
        return False
    return True


def run(scr):
    curses.curs_set(0)
    scr.keypad(True)
    _init_colors()
    st = State()
    while True:
        _draw(scr, st)
        k = scr.getch()
        if k == curses.KEY_RESIZE:
            continue
        if st.typing:
            _typing(st, k)
            continue
        if st.mode == "help":
            st.mode = "library"
            continue
        if k == ord("?"):
            st.mode = "help"
            continue
        if k == 27:                       # esc: back out one level
            if st.mode != "library":
                st.mode = "library"
            elif st.filter:
                st.filter = ""
            continue
        if k == ord("q"):
            if st.mode == "library":
                return
            st.mode = "library"
            continue
        handled = (_keys_library(st, scr, k) if st.mode == "library"
                   else _keys_props(st, k) if st.mode == "props"
                   else _keys_browse(st, scr, k))
        if not handled and k in (ord("b"), ord("p")) and st.mode != "library":
            st.mode = "library"


def main():
    if not engine.library_roots():
        print("lazypaper: no wallpaper library found.\n"
              f"looked in: {engine.WE_DIR}\n"
              "set FOSSYPAPER_LIBRARY, subscribe to wallpapers in Steam, or run\n"
              "  fossypaper browse wallhaven <search>")
        return 1
    try:
        curses.wrapper(run)
    except curses.error as e:
        print(f"lazypaper needs a real terminal ({e}).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
