"""fossypaper.properties — the wallpaper's own customization schema.

Wallpaper Engine ships every knob a wallpaper exposes inside its own
`project.json`, under `general.properties`. Reading it there is instant, needs
no GL context, and works for video wallpapers too — where asking
`linux-wallpaperengine --list-properties` returns nothing (or blocks for 25s
and then returns nothing).

So: parse project.json first, fall back to the renderer only if a wallpaper
carries no schema of its own.

The schema also carries `condition` expressions ("theme.value == 4") that decide
whether a knob is even relevant. We evaluate them so the editors can hide what
the wallpaper itself considers irrelevant, instead of showing every knob at once.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Types WE emits. `group` and `text` are headings/labels, not inputs;
# `usershortcut` is a Windows-only hotkey binding that Linux can't honour.
EDITABLE = ("bool", "slider", "color", "combo", "textinput", "scenetexture")
DECORATIVE = ("group", "text")


@dataclass
class Property:
    key: str
    kind: str
    text: str
    value: str
    mn: float = 0.0
    mx: float = 1.0
    step: float = 0.01
    precision: int = 3
    fraction: bool = False
    options: list = field(default_factory=list)   # [(label, value), ...]
    condition: str = ""
    order: int = 0

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE


# --------------------------------------------------------------------------- #
#  project.json — the authoritative source
# --------------------------------------------------------------------------- #
def from_project(folder: Path) -> list[Property]:
    try:
        meta = json.loads((folder / "project.json").read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    general = meta.get("general") or {}
    raw = general.get("properties") or {}
    loc = _localization(general.get("localization"))
    props = []
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("type") or "text").lower()
        if kind not in EDITABLE and kind not in DECORATIVE:
            continue                      # usershortcut and friends: nothing we can drive
        if key in HIDDEN:
            continue
        props.append(Property(
            key=key,
            kind=kind,
            text=label(spec.get("text"), key, loc),
            value=_str(spec.get("value")),
            mn=_num(spec.get("min"), 0.0),
            mx=_num(spec.get("max"), 1.0),
            step=_num(spec.get("step"), 0.01) or 0.01,
            precision=int(_num(spec.get("precision"), 3)),
            fraction=bool(spec.get("fraction")),
            options=_options(spec.get("options")),
            condition=str(spec.get("condition") or ""),
            order=int(_num(spec.get("order"), 0)),
        ))
    props.sort(key=lambda p: (p.order, p.key))
    return props


# Wallpaper Engine's own UI accent. Every wallpaper carries it and the renderer
# ignores it, so offering it as a knob is a control that does nothing.
HIDDEN = ("schemecolor",)

# Wallpaper Engine's built-in string keys, for wallpapers that don't ship a
# localization table of their own.
_KNOWN = {
    "ui_browse_properties_scheme_color": "Scheme colour",
    "ui_browse_properties_alignment": "Alignment",
    "ui_browse_properties_audio_volume": "Volume",
    "ui_browse_properties_rate": "Playback rate",
}
_TAG = re.compile(r"<[^>]+>")


def _localization(raw) -> dict:
    """general.localization is {"en-us": {"ui_key": "Text"}, ...}."""
    if not isinstance(raw, dict):
        return {}
    for lang in ("en-us", "en", "en-gb"):
        table = raw.get(lang)
        if isinstance(table, dict):
            return {str(k): str(v) for k, v in table.items()}
    return {}


def label(text, key: str, loc: dict | None = None) -> str:
    """What to call a knob: the wallpaper's own translation, a known WE key, the
    text with its HTML stripped, or — last — a humanised key. Authors use
    headings like `<br></br><u><h4>Colour Settings:</h4></u>`; a label is not
    the place for markup."""
    t = str(text or "").strip()
    if loc and t in loc:
        t = loc[t]
    elif t in _KNOWN:
        t = _KNOWN[t]
    t = t.replace("[info]", "")
    t = " ".join(_TAG.sub(" ", t).split()).strip(" :")
    if not t or re.fullmatch(r"ui_[a-z0-9_]+", t):
        base = re.sub(r"^ui_(browse_)?(properties_)?", "", t or key)
        t = base.replace("_", " ").strip().capitalize() or key
    return t


def _str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    return str(v)


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _options(raw) -> list:
    """WE combos are [{"label": ..., "value": ...}]; be tolerant of plain lists."""
    out = []
    for o in raw or []:
        if isinstance(o, dict):
            out.append((str(o.get("label", o.get("value", ""))), _str(o.get("value"))))
        else:
            out.append((str(o), str(o)))
    return out


# --------------------------------------------------------------------------- #
#  Renderer fallback — for libraries whose project.json carries no schema
# --------------------------------------------------------------------------- #
_HDR = re.compile(r"^(\w+) - (color|slider|boolean|bool|textinput|combo|scene texture|texture)\s*$")
_ALIAS = {"boolean": "bool", "scene texture": "scenetexture", "texture": "scenetexture"}


def from_renderer(binary: str, wid: str, timeout: int = 25) -> list[Property]:
    try:
        out = subprocess.run([binary, "--list-properties", wid],
                             capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    props, cur = [], None
    for line in out.splitlines():
        h = _HDR.match(line.strip())
        if h:
            kind = _ALIAS.get(h.group(2), h.group(2))
            cur = Property(h.group(1), kind, h.group(1), "")
            props.append(cur)
            continue
        if cur is None:
            continue
        s = line.strip()
        if s.startswith("Text:"):      cur.text = s[5:].strip()
        elif s.startswith("Value:"):   cur.value = s[6:].strip()
        elif s.startswith("Min:"):     cur.mn = _num(s[4:], 0.0)
        elif s.startswith("Max:"):     cur.mx = _num(s[4:], 1.0)
        elif s.startswith("Step:"):    cur.step = _num(s[5:], 0.01) or 0.01
        elif s.startswith("Options:"): cur.options = [(o.strip(), o.strip())
                                                      for o in s[8:].split(",") if o.strip()]
    return props


# --------------------------------------------------------------------------- #
#  Conditions — "theme.value == 4", "custombgimage.value == true"
# --------------------------------------------------------------------------- #
_COND = re.compile(r"""^\s*(\w+)\.value\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$""")


def visible(prop: Property, values: dict) -> bool:
    """Should this knob be shown, given the current value of every other knob?

    Conditions are simple `<key>.value <op> <literal>` comparisons, optionally
    joined by && / ||. Anything we can't parse is treated as visible — showing a
    knob that should be hidden is a much smaller sin than hiding a live one.
    """
    cond = prop.condition.strip()
    if not cond:
        return True
    for clause_set in cond.split("||"):
        if all(_clause(c, values) for c in clause_set.split("&&")):
            return True
    return False


def _clause(clause: str, values: dict) -> bool:
    m = _COND.match(clause)
    if not m:
        return True
    key, op, literal = m.group(1), m.group(2), m.group(3).strip().strip('"\'')
    have = values.get(key)
    if have is None:
        return True                       # unknown dependency: don't hide on a guess
    a, b = _coerce(have), _coerce(literal)
    if type(a) is not type(b):
        a, b = str(have).lower(), str(literal).lower()
    try:
        return {"==": a == b, "!=": a != b, ">": a > b,
                "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    except TypeError:
        return True


def _coerce(v):
    s = str(v).strip().lower()
    if s in ("true", "false"):
        return s == "true"
    try:
        return float(s)
    except ValueError:
        return s


def defaults(props: list[Property]) -> dict:
    """The wallpaper's own default for every editable knob."""
    return {p.key: p.value for p in props if p.editable}


# --------------------------------------------------------------------------- #
#  Colour marshalling — WE stores colours as space-separated floats
# --------------------------------------------------------------------------- #
def color_to_rgb(value: str) -> tuple[float, float, float]:
    """'0.5 0.2 1' (or a comma-separated variant) → floats in 0..1."""
    parts = [p for p in re.split(r"[\s,]+", str(value).strip()) if p]
    out = []
    for p in parts[:3]:
        try:
            out.append(max(0.0, min(1.0, float(p))))
        except ValueError:
            out.append(0.0)
    while len(out) < 3:
        out.append(0.0)
    return tuple(out)


def rgb_to_color(r: float, g: float, b: float) -> str:
    """Back to WE's wire format. Three components, space separated — the
    renderer rejects a trailing alpha."""
    return f"{r:.6f} {g:.6f} {b:.6f}"
