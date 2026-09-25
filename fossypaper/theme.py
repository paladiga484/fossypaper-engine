"""fossypaper.theme — how the GUI looks, and how you change it.

Flat colour only. Every surface is one solid fill with a one-pixel border; there
are no gradients anywhere in this file and none should be added. Gradients hide
the edges of things, and a wallpaper manager is a window full of edges.

A theme is six colours. That's the whole model, which is why "follow the
wallpaper" can generate one from an extracted palette without a special case.
"""
from __future__ import annotations

THEMES = {
    # name        bg         surface    line       text       dim        accent
    "obsidian": ("#0f1013", "#17191e", "#2a2e37", "#e6e6ea", "#8b8f9a", "#c8a24b"),
    "void":     ("#000000", "#0b0b0d", "#232326", "#d8d8dc", "#75757c", "#7f9cf5"),
    "ashen":    ("#161616", "#1f1f1f", "#333333", "#dcdcdc", "#8a8a8a", "#9a8c98"),
    "ember":    ("#120e0c", "#1b1614", "#33291f", "#ece2d8", "#94867a", "#c05621"),
    "bone":     ("#f4f2ed", "#ffffff", "#d8d4cb", "#1c1b19", "#6b675f", "#7a5c2e"),
    # the darker shelf
    "carcosa":  ("#0b0a07", "#13110c", "#2a261b", "#e8e0c8", "#8f8670", "#d9b72b"),
    "abyssal":  ("#050a0c", "#0a1316", "#1a2a2f", "#d6e4e6", "#6f8a8f", "#3fbfa8"),
    "bloodmoon": ("#0a0607", "#130c0d", "#2c1a1c", "#eadcdc", "#917a7b", "#c3263a"),
    "eldritch": ("#08070c", "#100e17", "#241f33", "#e0dcf0", "#827c99", "#9bd34f"),
}
DEFAULT = "obsidian"
ROLES = ("bg", "surface", "line", "text", "dim", "accent")


def palette(name: str = DEFAULT, accent: str = "", follow: list | None = None) -> dict:
    """Resolve a theme to its six colours.

    `follow` is a wallpaper palette (from `fossypaper theme`); when given, the
    surfaces are taken from the wallpaper's own darkest tones and the accent
    from its most saturated one — so the app sits inside the wallpaper instead
    of on top of it.
    """
    if follow:
        base = _from_wallpaper(follow)
    else:
        base = dict(zip(ROLES, THEMES.get(name, THEMES[DEFAULT])))
    if accent and _valid(accent):
        base["accent"] = accent
    return base


def _valid(hexv: str) -> bool:
    return (isinstance(hexv, str) and len(hexv) == 7 and hexv[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in hexv[1:]))


def _lum(hx: str) -> float:
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _sat(hx: str) -> float:
    r, g, b = (int(hx[i:i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    br, bg, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (round(ar + (br - ar) * t),
                              round(ag + (bg - ag) * t),
                              round(ab + (bb - ab) * t))


def _from_wallpaper(pal: list) -> dict:
    pal = [c for c in pal if _valid(c)] or ["#101014", "#e0e0e0"]
    by_lum = sorted(pal, key=_lum)
    bg = by_lum[0]
    text = by_lum[-1]
    if _lum(text) - _lum(bg) < 90:                     # never ship unreadable
        text = "#f0f0f2" if _lum(bg) < 128 else "#101012"
    accent = max(pal, key=_sat)
    if _sat(accent) < 0.15:
        accent = THEMES[DEFAULT][5]
    light = _lum(bg) > 128
    return {"bg": bg,
            "surface": mix(bg, "#ffffff" if not light else "#000000", 0.06),
            "line": mix(bg, text, 0.22),
            "text": text,
            "dim": mix(bg, text, 0.55),
            "accent": accent}


_ARROW = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6" viewBox="0 0 10 6">'
          '<path d="{d}" fill="{c}"/></svg>')


def arrow_icons(p: dict, where) -> dict:
    """Qt stylesheets can't draw a CSS border-triangle (it comes out as a bar),
    so the combo and spin arrows are two tiny SVGs in the theme's dim colour,
    written once per colour under `where`."""
    from pathlib import Path
    where = Path(where)
    where.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, d in (("down", "M0 0h10L5 6z"), ("up", "M0 6h10L5 0z")):
        f = where / f"arrow-{name}-{p['dim'].lstrip('#')}.svg"
        if not f.is_file():
            f.write_text(_ARROW.format(d=d, c=p["dim"]))
        out[name] = f.as_posix()
    return out


def stylesheet(p: dict, font: str = "", card_w: int = 224, arrows: dict | None = None) -> str:
    """Qt stylesheet. Flat fills, hairline borders, no gradients."""
    family = f"font-family:'{font}';" if font else ""
    sel = mix(p["surface"], p["accent"], 0.28)
    return _base(p, family, sel) + (_arrow_rules(arrows) if arrows else "")


def _arrow_rules(a: dict) -> str:
    return f"""
QComboBox::down-arrow {{ image:url({a['down']}); width:10px; height:6px; border:0; margin-right:6px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image:url({a['up']}); width:8px; height:5px; border:0; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image:url({a['down']}); width:8px; height:5px; border:0; }}
"""


def _base(p: dict, family: str, sel: str) -> str:
    return f"""
* {{ outline: none; }}
QWidget {{ background:{p['bg']}; color:{p['text']}; {family} font-size:13px; }}
QMainWindow, QDialog {{ background:{p['bg']}; }}

QToolBar {{ background:{p['surface']}; border:0; border-bottom:1px solid {p['line']};
            spacing:6px; padding:6px 8px; }}
QToolBar QToolButton {{ background:{p['surface']}; color:{p['text']};
            border:1px solid transparent; padding:5px 10px; }}
QToolBar QToolButton:hover {{ border:1px solid {p['line']}; }}
QToolBar QToolButton:pressed {{ background:{sel}; }}

QPushButton {{ background:{p['surface']}; color:{p['text']};
            border:1px solid {p['line']}; padding:5px 12px; }}
QPushButton:hover {{ border:1px solid {p['accent']}; }}
QPushButton:pressed {{ background:{sel}; }}
QPushButton:disabled {{ color:{p['dim']}; border-color:{p['line']}; }}
QPushButton#primary {{ border:1px solid {p['accent']}; color:{p['accent']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background:{p['bg']}; color:{p['text']};
            border:1px solid {p['line']}; padding:4px 6px; selection-background-color:{sel}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border:1px solid {p['accent']}; }}
QComboBox QAbstractItemView {{ background:{p['surface']}; color:{p['text']};
            border:1px solid {p['line']}; selection-background-color:{sel}; }}
QComboBox::drop-down {{ border:0; width:18px; }}
QComboBox::down-arrow {{ width:0; height:0; margin-right:6px;
            border-left:4px solid transparent; border-right:4px solid transparent;
            border-top:5px solid {p['dim']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
            background:{p['surface']}; border:1px solid {p['line']}; width:14px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width:0; height:0;
            border-left:3px solid transparent; border-right:3px solid transparent;
            border-bottom:4px solid {p['dim']}; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width:0; height:0;
            border-left:3px solid transparent; border-right:3px solid transparent;
            border-top:4px solid {p['dim']}; }}

QCheckBox {{ spacing:8px; }}
QCheckBox::indicator {{ width:14px; height:14px; background:{p['bg']};
            border:1px solid {p['line']}; }}
QCheckBox::indicator:checked {{ background:{p['accent']}; border:1px solid {p['accent']}; }}

QSlider::groove:horizontal {{ background:{p['line']}; height:2px; }}
QSlider::handle:horizontal {{ background:{p['accent']}; width:10px; height:14px; margin:-6px 0; }}
QSlider::sub-page:horizontal {{ background:{p['accent']}; height:2px; }}

QScrollArea {{ border:0; }}
QScrollBar:vertical {{ background:{p['bg']}; width:10px; margin:0; }}
QScrollBar::handle:vertical {{ background:{p['line']}; min-height:28px; }}
QScrollBar::handle:vertical:hover {{ background:{p['dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; width:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:{p['bg']}; }}
QScrollBar:horizontal {{ background:{p['bg']}; height:10px; }}
QScrollBar::handle:horizontal {{ background:{p['line']}; min-width:28px; }}

QFrame#card {{ background:{p['surface']}; border:1px solid {p['line']}; }}
QFrame#card:hover {{ border:1px solid {p['dim']}; }}
QFrame#card[selected="true"] {{ border:1px solid {p['accent']}; }}
QFrame#card[current="true"] {{ border:1px solid {p['accent']}; }}
QLabel#cardTitle {{ background:transparent; color:{p['text']}; font-size:12px; }}
QLabel#badgeOk {{ background:transparent; color:{p['dim']}; font-size:11px; }}
QLabel#badgeWarn {{ background:transparent; color:{p['accent']}; font-size:11px; }}
QLabel#thumb {{ background:{p['bg']}; border:0; }}
QLabel#heading {{ color:{p['accent']}; font-size:12px; }}
QLabel#muted {{ color:{p['dim']}; }}
QLabel#doc {{ color:{p['text']}; }}

QDockWidget {{ titlebar-close-icon:none; titlebar-normal-icon:none; }}
QDockWidget::title {{ background:{p['surface']}; color:{p['dim']};
            border-bottom:1px solid {p['line']}; padding:6px; text-align:left; }}
QTabWidget::pane {{ border:1px solid {p['line']}; }}
QTabBar::tab {{ background:{p['bg']}; color:{p['dim']};
            border:1px solid {p['line']}; padding:5px 14px; margin-right:2px; }}
QTabBar::tab:selected {{ background:{p['surface']}; color:{p['text']};
            border-bottom:1px solid {p['accent']}; }}
QStatusBar {{ background:{p['surface']}; color:{p['dim']};
            border-top:1px solid {p['line']}; }}
QStatusBar::item {{ border:0; }}
QToolTip {{ background:{p['surface']}; color:{p['text']};
            border:1px solid {p['line']}; padding:4px; }}
QGroupBox {{ border:1px solid {p['line']}; margin-top:14px; padding-top:8px; }}

/* ── now playing ───────────────────────────────────────────────────── */
QFrame#hero {{ background:{p['surface']}; border:0; border-bottom:1px solid {p['line']}; }}
QFrame#hero QLabel, QWidget#dockHead QLabel, QWidget#cardFoot QLabel {{ background:transparent; }}
QLabel#heroArt {{ background:{p['bg']}; border:1px solid {p['line']}; color:{p['dim']}; }}
QLabel#heroState {{ color:{p['dim']}; font-size:10px; letter-spacing:3px; font-weight:600; }}
QLabel#heroState[live="true"] {{ color:{p['accent']}; }}
QLabel#heroTitle {{ color:{p['text']}; font-size:21px; }}

/* ── navigation ────────────────────────────────────────────────────── */
QWidget#navbar {{ background:{p['bg']}; border-bottom:1px solid {p['line']}; }}
QPushButton#navtab {{ background:transparent; border:0; border-bottom:2px solid transparent;
            color:{p['dim']}; padding:6px 12px; font-size:13px; letter-spacing:1px; }}
QPushButton#navtab:hover {{ color:{p['text']}; }}
QPushButton#navtab:checked {{ color:{p['text']}; border-bottom:2px solid {p['accent']}; }}
QPushButton#chip {{ background:transparent; color:{p['dim']}; border:1px solid {p['line']};
            border-radius:11px; padding:3px 11px; font-size:12px; }}
QPushButton#chip:hover {{ color:{p['text']}; border-color:{p['dim']}; }}
QPushButton#chip:checked {{ background:{p['accent']}; color:{p['bg']}; border-color:{p['accent']}; }}
QPushButton#flat {{ background:transparent; border:1px solid transparent; color:{p['dim']};
            padding:5px 9px; }}
QPushButton#flat:hover {{ color:{p['text']}; border:1px solid {p['line']}; }}
QLabel#empty {{ color:{p['dim']}; font-size:15px; }}

/* ── cards ─────────────────────────────────────────────────────────── */
QFrame#card {{ padding:0; }}
QFrame#card[selected="true"] {{ border:1px solid {p['text']}; }}
QFrame#card[current="true"] {{ border:1px solid {p['accent']}; }}
QFrame#nowbar {{ background:{p['surface']}; border:0; }}
QFrame#nowbar[current="true"] {{ background:{p['accent']}; }}
QWidget#cardFoot {{ background:{p['surface']}; }}
QLabel#badgeOk, QLabel#badgeWarn {{ font-size:10px; letter-spacing:1px; font-weight:600; }}
QFrame#card[current="true"] QLabel#badgeOk {{ color:{p['accent']}; }}

/* ── dock ──────────────────────────────────────────────────────────── */
QWidget#dockHead {{ background:{p['surface']}; border-bottom:1px solid {p['line']}; }}
QLabel#dockTitle {{ color:{p['text']}; font-size:16px; }}
QLabel#caption {{ color:{p['dim']}; font-size:10px; letter-spacing:3px; font-weight:600;
            padding:12px 14px 4px 14px; }}

QMenu {{ background:{p['surface']}; color:{p['text']}; border:1px solid {p['line']}; padding:4px; }}
QMenu::item {{ padding:5px 18px; background:transparent; }}
QMenu::item:selected {{ background:{sel}; }}
QMenu::item:disabled {{ color:{p['dim']}; }}
QMenu::separator {{ height:1px; background:{p['line']}; margin:4px 6px; }}
QGroupBox::title {{ color:{p['accent']}; subcontrol-origin:margin; left:8px; padding:0 4px; }}
"""
