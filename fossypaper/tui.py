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
    r" _                                        ",
    r"| |__ _ ____  _ _ __  __ _ _ __  ___ _ _  ",
    r"| / _` |_  / || | '_ \/ _` | '_ \/ -_) '_|",
    r"|_\__,_/___|\_, | .__/\__,_| .__/\___|_|  ",
]
LOGO_TINY = ["lazypaper"]

FPS_CYCLE = [30, 60, 24, 45, 90, 120, 144]
LAYER_CYCLE = ["auto", "bottom", "background", "top"]
HOST_CYCLE = ["auto", "layer", "plasma", "gnome", "x11"]
GPU_CYCLE = ["auto", "nvidia", "mesa"]
SCALE_CYCLE = ["", "default", "stretch", "fit", "fill"]
AUDIO_CYCLE = ["auto", "always", "never"]
TAG = {"wpe": "scene", "mpvpaper": "video", "swww": "still",
       "web": " web ", "application": " app "}
KINDS = [None, "scene", "video", "still", "audio"]       # c cycles the library through these
KIND_LABEL = {None: "", "scene": "scenes", "video": "video", "still": "stills", "audio": "reactive"}
SORTS = ["title", "new", "kind"]                          # S cycles
SORT_LABEL = {"title": "", "new": "newest", "kind": "by kind"}


def _utf8() -> bool:
    import locale
    return "utf" in (locale.getpreferredencoding(False) or "").lower()


# Box drawing and marks: rounded Unicode where the terminal speaks UTF-8,
# plain ASCII where it doesn't (a linux console, a stripped-down ssh).
if _utf8():
    BOX = ("╭", "╮", "╰", "╯", "─", "│")
    NOW, HAVE, BLOCK, PLAY, STOP = "▶", "✓", "██", "▶ playing", "■ stopped"
else:
    BOX = ("+", "+", "+", "+", "-", "|")
    NOW, HAVE, BLOCK, PLAY, STOP = "*", "*", "##", "playing", "stopped"


def kind_of(w) -> str:
    be, _ok = engine.backend_for(w)
    if be == "mpvpaper" and w.type == "image":
        return "still"
    return TAG.get(be, be)

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


_VS16, _ZWJ = "\ufe0f", "\u200d"


def _glyphs(s: str):
    """(text, columns) per glyph as a terminal lays it out. CJK is two cells.
    U+FE0F (emoji presentation) is dropped: kitty widens `🕊️` to two cells,
    tmux and others keep it at one, and a title like `[🕊️] Columbina`
    shoved the rest of its row sideways on whichever one we guessed wrong.
    Without the selector every terminal agrees. Combining marks and joiners
    ride along at zero width."""
    s = str(s)
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == _VS16:
            i += 1
            continue
        w = 0 if unicodedata.combining(ch) or ch == _ZWJ else \
            (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        j = i + 1
        while j < len(s) and (s[j] == _ZWJ or unicodedata.combining(s[j])):
            j += 1
        yield s[i:j], w
        i = j


def cells(s: str) -> int:
    """Terminal columns a string occupies. CJK titles are two cells per glyph,
    and this library is full of them — measuring in characters puts the badge
    column and the right border in different places on every other row."""
    return sum(w for _g, w in _glyphs(s))


def fit(s: str, width: int) -> str:
    """Truncate to `width` *columns*, never splitting a wide glyph in half."""
    if width <= 0:
        return ""
    out, n = [], 0
    for g, c in _glyphs(s):
        if n + c > width:
            break
        out.append(g)
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


def _box(win, y, x, h, w, title="", tail=""):
    if h < 2 or w < 4:
        return
    tl, tr, bl, br, hz, vt = BOX
    _put(win, y, x, tl + hz * (w - 2) + tr, A(DIM))
    for i in range(1, h - 1):
        _put(win, y + i, x, vt, A(DIM))
        _put(win, y + i, x + w - 1, vt, A(DIM))
    _put(win, y + h - 1, x, bl + hz * (w - 2) + br, A(DIM))
    if title:
        _put(win, y, x + 2, f" {title} ", A(ACCENT))
    if tail:
        _put(win, y, x + 4 + cells(title) + 1, f" {tail} ", A(DIM))


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
        # Both of these used to be recomputed on every keypress: `pgrep` is a
        # fork+exec and a property schema is a JSON parse. Holding `j` down
        # should not cost either.
        self.running = engine.is_running()
        self._knobs: dict = {}
        self.kind = None
        self.sort = self.cfg.get("ui_sort", "title") if self.cfg.get("ui_sort") in SORTS else "title"
        self.scr = None

    def knobs(self, wid: str) -> int:
        if wid not in self._knobs:
            self._knobs[wid] = sum(p.editable for p in engine.list_properties(wid))
        return self._knobs[wid]

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
        f = self.filter.lower()
        out = []
        for w in self.lib:
            if self.kind == "audio" and not w.audio:
                continue
            if self.kind and self.kind != "audio" and kind_of(w) != self.kind:
                continue
            if f and f not in w.title.lower() and f not in w.id \
                    and not any(f in str(t).lower() for t in w.tags):
                continue
            out.append(w)
        if self.sort == "new":
            out.sort(key=lambda w: _mtime(w.folder), reverse=True)
        elif self.sort == "kind":
            out.sort(key=lambda w: (kind_of(w), w.title.lower()))
        return out

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
    logo = LOGO if W >= 92 else LOGO_SMALL if W >= 70 else LOGO_TINY
    for i, ln in enumerate(logo):
        _put(scr, i, 1, ln, A(ACCENT))
    bx = max(len(ln) for ln in logo) + 4
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
    _put(scr, 2, bx, "state   ", A(DIM))
    _put(scr, 2, bx + 9, PLAY if st.running else STOP,
         A(OK) if st.running else A(DIM))
    if st.palette:
        _put(scr, 3, bx, "palette ", A(DIM))
        for i, hexv in enumerate(st.palette[:8]):
            _put(scr, 3, bx + 9 + i * 3, BLOCK, A(_swatch(i, hexv)))
    # the status column is four rows; a one-line wordmark must not let the
    # panes start on top of it
    return max(len(logo), 4)


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

    title = f"LIBRARY {len(view)}"
    tail = "  ".join(x for x in (KIND_LABEL[st.kind], SORT_LABEL[st.sort],
                                 f"/{st.filter}" if st.filter else "") if x)
    _box(scr, top_y, 1, listh, listw, title, tail)
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
        mark = NOW if w.id == cur else " "
        badge = kind_of(w)[:5]
        line = pad(f" {mark} {w.title}", listw - 11) + "  "
        if i == st.sel:
            _put(scr, top_y + 1 + r, 2, pad(line + badge, listw - 3), SEL)
        else:
            _put(scr, top_y + 1 + r, 2, line, A(ACCENT) if w.id == cur else A())
            _put(scr, top_y + 1 + r, 2 + listw - 9, badge, A(DIM) if ok else A(WARN))

    rx = listw + 2
    rw = W - rx - 1
    if rw < 24:
        return
    _box(scr, top_y, rx, listh, rw, "DETAILS")
    w = st.current()
    if w is None:
        _put(scr, top_y + 2, rx + 3, "nothing matches" if st.lib else "library is empty", A(DIM))
        return
    be, ok = engine.backend_for(w)
    reason = engine.why_unsupported(w)
    renders = {"mpvpaper": "video -> mpvpaper", "swww": "image -> swww",
               "wpe": "scene -> linux-wallpaperengine",
               "missing_assets": "broken download", "missing_dependency": "needs base wallpaper"}.get(be, be)
    if not ok:
        renders += "  (unavailable)"
    y = top_y + 1
    kind = w.type + (" (video-backed)" if w.video and w.type != "video" else "")
    for k, v, col in (("title", w.title, VALUE), ("id", w.id, 0),
                      ("kind", kind, 0), ("renders", renders, 0 if ok else WARN),
                      ("audio", "reactive" if w.audio else "silent", 0),
                      ("knobs", str(st.knobs(w.id)), 0),
                      ("tags", ", ".join(str(t) for t in w.tags[:4]) or "-", DIM)):
        _put(scr, y, rx + 2, f"{k:>8}", A(DIM))
        _put(scr, y, rx + 11, fit(v, rw - 13), A(col))
        y += 1
    if reason:
        for ln in textwrap.wrap(reason, rw - 5)[:3]:
            _put(scr, y, rx + 2, ln, A(WARN)); y += 1
    note = engine.render_note(w)
    if note:
        for ln in textwrap.wrap(note, rw - 5)[:3]:
            _put(scr, y, rx + 2, ln, A(ACCENT)); y += 1

    y += 1
    _put(scr, y, rx + 2, "SETTINGS", A(ACCENT)); y += 1
    o = config.opts(st.cfg)
    h = engine.host(o)
    for k, v in (("d  desktop", (o["host"] if o["host"] != "auto" else "auto: " + h)),
                 ("f  fps", o["fps"]),
                 ("l  layer", (o["layer"] + (" -> " + engine.resolved_layer(o)
                                             if o["layer"] == "auto" else ""))
                  if h == "layer" else "n/a on " + h),
                 ("v  gpu", o["gpu"]),
                 ("s  scaling", o["scaling"] or "auto"),
                 ("o  output", o["output"] or "all"),
                 ("a  audio", o["audio_processing"]),
                 ("m  mute", "on" if o["silent"] else f"vol {o['volume']}"),
                 ("x  pause", ("on fullscreen" + ("  (blind on " + engine.compositor() + ")"
                                                   if engine.fullscreen_blind()
                                                   else "")) if o["fullscreen_pause"] else "never")):
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
        have = HAVE if row.installed() else " "
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
    ("/", "filter the library by title, id or tag (esc clears)"),
    ("c", "cycle kind: all, scenes, video, stills, reactive"),
    ("S", "cycle sort: a-z, newest, by kind"),
    ("z", "shuffle: apply a random one from what's shown"),
    ("r", "rescan the library"),
    ("d", "desktop: auto, layer-shell, Plasma, GNOME, X11 root"),
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
    "library": " j/k move   enter apply   space stop   z shuffle   c kind   S sort   / filter   p props   b browse   ? keys   q quit",
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
    w = next((x for x in st.lib if x.id == wid), None)
    st.status = f"starting {w.title if w else wid}..."
    if st.scr is not None:
        _draw(st.scr, st)             # start() waits to see the renderer live
    o = config.opts(st.cfg)
    o["properties"] = st.cfg.get("properties", {}).get(wid, {})
    ok, msg = engine.start(wid, o)
    if ok:
        st.cfg["current"] = wid
        config.save(st.cfg)
    st.running = ok or engine.is_running()
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
        if st.running:
            engine.stop(); st.running = False; st.status = "wallpaper off"
        elif st.cfg.get("current"):
            _apply(st, st.cfg["current"])
    elif k == ord("/"):
        st.typing = "filter"; st.buffer = st.filter
    elif k == ord("c"):
        st.kind = KINDS[(KINDS.index(st.kind) + 1) % len(KINDS)]
        st.sel = st.top = 0
        st.status = f"showing {KIND_LABEL[st.kind] or 'everything'}"
    elif k == ord("S"):
        st.sort = SORTS[(SORTS.index(st.sort) + 1) % len(SORTS)]
        st.cfg["ui_sort"] = st.sort
        config.save(st.cfg)
        st.sel = st.top = 0
        st.status = f"sorted {SORT_LABEL[st.sort] or 'a-z'}"
    elif k == ord("z"):
        import random
        cur = st.cfg.get("current", "")
        pool = [w for w in view if engine.backend_for(w)[1] and w.id != cur]
        if pool:
            pick = random.choice(pool)
            st.sel = view.index(pick)
            _apply(st, pick.id)
        else:
            st.status = "nothing else to shuffle to"
    elif k == ord("r"):
        st.lib = engine.scan_library()
        st._knobs.clear()
        engine.forget_tools()
        st.running = engine.is_running()
        st.status = f"{len(st.lib)} wallpapers"
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
    elif k == ord("d"):
        _cycle(st, "host", HOST_CYCLE, "desktop")
        engine.forget_tools()
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
            st._knobs.clear()
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
    # getch returns -1 after this long with no key. That tick is where the one
    # genuinely expensive check lives, instead of on the draw path.
    scr.timeout(2000)
    _init_colors()
    st = State()
    st.scr = scr
    while True:
        _draw(scr, st)
        k = scr.getch()
        if k == -1:
            st.running = engine.is_running()
            continue
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


def _mtime(folder) -> float:
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0.0


APP_ID = "lazypaper"


def window_argv(cmd: list[str]) -> list[str] | None:
    """How to open `cmd` in a terminal window of its own, tagged with app-id
    `lazypaper` so niri/Hyprland window rules can float and size it.

    Launchers on tiling compositors disagree about `Terminal=true` — some run
    the entry in no terminal at all — so the desktop entry asks for this
    instead. The desktop's own default terminal wins (xdg-terminal-exec),
    then whichever common one is installed."""
    if engine.which("xdg-terminal-exec"):
        return ["xdg-terminal-exec", f"--app-id={APP_ID}", f"--title={APP_ID}", "--", *cmd]
    for term, argv in (
            ("kitty", ["kitty", "--class", APP_ID, "--title", APP_ID]),
            ("foot", ["foot", "--app-id", APP_ID, "--title", APP_ID]),
            ("ghostty", ["ghostty", f"--class={APP_ID}", f"--title={APP_ID}", "-e"]),
            ("alacritty", ["alacritty", "--class", APP_ID, "--title", APP_ID, "-e"]),
            ("wezterm", ["wezterm", "start", "--class", APP_ID, "--"]),
            ("konsole", ["konsole", "-p", f"tabtitle={APP_ID}", "-e"]),
            ("gnome-terminal", ["gnome-terminal", f"--title={APP_ID}", "--"])):
        if engine.which(term):
            return [*argv, *cmd]
    return None


def open_window() -> int:
    import subprocess
    import sys
    argv = window_argv([sys.executable, "-m", "fossypaper", "tui"])
    if argv is None:
        print("lazypaper: no terminal found to open a window in "
              "(install kitty, foot, ghostty, alacritty or xdg-terminal-exec)")
        return 1
    subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ.copy())
    return 0


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
