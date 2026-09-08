"""fossypaper — the desktop app.

Same bones as before: a toolbar, a grid of wallpapers, a properties dock on the
right, a status line at the bottom. What changed is what those bones carry — a
built-in browser for Wallhaven and the Steam Workshop, a property editor driven
by the wallpaper's own schema (including the conditions that say which knobs
matter), and a look you can change.

Flat surfaces throughout. See theme.py for why.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QMovie, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
    QStatusBar, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from . import config, engine, properties, sources, theme

DOCS = Path(__file__).resolve().parent.parent / "docs"


# --------------------------------------------------------------------------- #
#  Background work — the UI thread never waits on the network or on steamcmd
# --------------------------------------------------------------------------- #
class Job(QObject):
    """Runs one callable on a worker thread.

    `tag` rides along so the receiver can tell a fresh answer from a slow older
    one. Receivers must be bound methods of objects that live on the GUI thread:
    that is what makes Qt queue the signal back to it. Connect a bare lambda
    here and the slot runs *in the worker*, which for anything that touches a
    widget means the widget never draws.
    """
    done = Signal(object, object)          # tag, result
    failed = Signal(object, str)           # tag, message

    def __init__(self, fn, tag=None):
        super().__init__()
        self._fn, self._tag = fn, tag

    def run(self):
        try:
            self.done.emit(self._tag, self._fn())
        except Exception as e:                      # a browser must not take the app down
            self.failed.emit(self._tag, str(e))


class ThumbJob(QObject):
    """Thumbnails for a whole page, fetched one at a time on one thread and
    handed back as each lands. Thirty parallel threads to fetch thirty small
    JPEGs is a good way to make a browser feel worse than it is."""
    ready = Signal(str, object)
    finished = Signal()

    def __init__(self, rows):
        super().__init__()
        self._rows = [(r.id, r.thumb_url) for r in rows]
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        for rid, url in self._rows:
            if self._stop:
                break
            try:
                self.ready.emit(rid, sources.thumbnail(url))
            except Exception:
                self.ready.emit(rid, None)
        self.finished.emit()


class Jobs:
    """Keeps threads alive until they finish, and cleans up after them."""

    def __init__(self):
        self._live = []

    def start(self, fn, on_done, on_fail=None, tag=None):
        """`on_done` / `on_fail` must be bound methods of GUI-thread objects."""
        thread = QThread()
        job = Job(fn, tag)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.done.connect(on_done)
        if on_fail:
            job.failed.connect(on_fail)
        for sig in (job.done, job.failed):
            sig.connect(thread.quit)
        pair = (thread, job)
        thread.finished.connect(lambda: self._drop(pair))
        self._live.append(pair)
        thread.start()
        return pair

    def start_object(self, job):
        """Run a QObject worker that emits its own signals as it goes."""
        thread = QThread()
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.finished.connect(thread.quit)
        pair = (thread, job)
        thread.finished.connect(lambda: self._drop(pair))
        self._live.append(pair)
        thread.start()
        return pair

    def _drop(self, pair):
        if pair in self._live:
            self._live.remove(pair)

    def wait(self):
        for thread, _ in list(self._live):
            thread.quit()
            thread.wait(1500)


# --------------------------------------------------------------------------- #
#  Cards
# --------------------------------------------------------------------------- #
class Card(QFrame):
    """One wallpaper. Animated previews play on hover only — the library is
    forty of these and forty looping GIFs is not a free thing to ask for."""
    picked = Signal(object)
    opened = Signal(object)

    def __init__(self, wp, width=224):
        super().__init__()
        self.wp = wp
        self._movie = None
        self.setObjectName("card")
        h = int(width * 0.75)
        self.setFixedSize(width, h)
        self.setProperty("selected", False)
        self.setProperty("current", False)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedHeight(h - 46)
        self.thumb.setAlignment(Qt.AlignCenter)
        v.addWidget(self.thumb)
        row = QHBoxLayout()
        row.setSpacing(6)
        title = QLabel(wp.title)
        title.setObjectName("cardTitle")
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        be, ok = engine.backend_for(wp)
        label = {"wpe": "scene", "mpvpaper": "video", "swww": "image"}.get(be, be)
        badge = QLabel(label if ok else label + " !")
        badge.setObjectName("badgeOk" if ok else "badgeWarn")
        badge.setToolTip(engine.why_unsupported(wp) or
                         {"wpe": "Wallpaper Engine scene, via linux-wallpaperengine",
                          "mpvpaper": "video, via mpvpaper",
                          "swww": "still image, via swww"}.get(be, be)
                         + ("" if ok else " — not installed"))
        row.addWidget(title, 1)
        row.addWidget(badge)
        v.addLayout(row)
        self._load_preview(width - 12, h - 46)

    def _load_preview(self, w, h):
        p = self.wp.preview
        if not p or not p.is_file():
            self.thumb.setText("no preview")
            return
        if p.suffix.lower() == ".gif":
            self._movie = QMovie(str(p))
            self._movie.setScaledSize(QSize(w, h))
            self._movie.jumpToFrame(0)
            self.thumb.setPixmap(self._movie.currentPixmap())
            return
        pm = QPixmap(str(p))
        if pm.isNull():
            self.thumb.setText("no preview")
        else:
            self.thumb.setPixmap(pm.scaled(w, h, Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation))

    def enterEvent(self, e):
        if self._movie:
            self.thumb.setMovie(self._movie)
            self._movie.start()

    def leaveEvent(self, e):
        if self._movie:
            self._movie.stop()
            self.thumb.setPixmap(self._movie.currentPixmap())

    def mousePressEvent(self, e):
        self.picked.emit(self.wp)

    def mouseDoubleClickEvent(self, e):
        self.opened.emit(self.wp)

    def mark(self, selected=None, current=None):
        if selected is not None:
            self.setProperty("selected", selected)
        if current is not None:
            self.setProperty("current", current)
        self.style().unpolish(self)
        self.style().polish(self)


class ResultCard(QFrame):
    """One browser result. The thumbnail arrives later, from disk cache."""
    picked = Signal(object)

    def __init__(self, row, width=224):
        super().__init__()
        self.row = row
        self.setObjectName("card")
        h = int(width * 0.75)
        self.setFixedSize(width, h)
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        self.thumb = QLabel("...")
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedHeight(h - 46)
        self.thumb.setAlignment(Qt.AlignCenter)
        v.addWidget(self.thumb)
        line = QHBoxLayout()
        t = QLabel(row.title)
        t.setObjectName("cardTitle")
        t.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        t.setToolTip(f"{row.title}\n{row.meta}\n{row.page_url}")
        state = QLabel("in library" if row.installed() else row.kind)
        state.setObjectName("badgeOk" if row.installed() else "badgeWarn")
        line.addWidget(t, 1)
        line.addWidget(state)
        v.addLayout(line)
        meta = QLabel(row.meta)
        meta.setObjectName("muted")
        meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        v.addWidget(meta)

    def set_thumb(self, path):
        if not path:
            self.thumb.setText("no preview")
            return
        pm = QPixmap(str(path))
        if pm.isNull():
            self.thumb.setText("no preview")
            return
        self.thumb.setPixmap(pm.scaled(self.width() - 12, self.thumb.height(),
                                       Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))

    def mousePressEvent(self, e):
        self.picked.emit(self.row)


# --------------------------------------------------------------------------- #
#  Grid that reflows
# --------------------------------------------------------------------------- #
class CardGrid(QScrollArea):
    def __init__(self, card_width=224):
        super().__init__()
        self.setWidgetResizable(True)
        self.card_width = card_width
        self._widgets: list[QWidget] = []
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(12, 12, 12, 12)
        self.setWidget(self._host)
        self._cols = 0
        self._relayout = QTimer(self, singleShot=True, interval=60)
        self._relayout.timeout.connect(self._place)

    def set_cards(self, widgets):
        for w in self._widgets:
            self._grid.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
        self._widgets = list(widgets)
        self._cols = 0
        self._place()

    def _columns(self):
        return max(1, (self.viewport().width() - 24) // (self.card_width + 10))

    def _place(self):
        cols = self._columns()
        if cols == self._cols:
            return
        self._cols = cols
        for i, w in enumerate(self._widgets):
            self._grid.addWidget(w, i // cols, i % cols)
        self._grid.setRowStretch(len(self._widgets) // cols + 1, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout.start()


# --------------------------------------------------------------------------- #
#  Property editor
# --------------------------------------------------------------------------- #
class PropertyDock(QDockWidget):
    """Built from the wallpaper's own schema. Knobs whose `condition` isn't met
    are hidden, exactly as Wallpaper Engine hides them — showing all 77 at once
    is not an editor, it's a wall."""

    def __init__(self, apply_cb, parent=None):
        super().__init__("PROPERTIES", parent)
        self.apply_cb = apply_cb
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(300)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.setWidget(self.scroll)
        self.body = QWidget()
        self.form = QVBoxLayout(self.body)
        self.form.setContentsMargins(10, 10, 10, 10)
        self.form.setSpacing(8)
        self.scroll.setWidget(self.body)
        self.props: list = []
        self.values: dict = {}
        self.rows: dict = {}
        self.wid = ""

    def load(self, wid, saved: dict):
        self.wid = wid
        self.props = engine.list_properties(wid)
        self.values = {**properties.defaults(self.props), **(saved or {})}
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows = {}
        editable = [p for p in self.props if p.editable]
        if not editable:
            self.form.addWidget(_muted("this wallpaper exposes no adjustable knobs"))
            self.form.addStretch(1)
            return
        for p in self.props:
            if p.kind == "group":
                head = QLabel(p.text.upper())
                head.setObjectName("heading")
                self.form.addWidget(head)
                self.rows[p.key] = (p, None, head)
                continue
            if not p.editable:
                continue
            widget = self._widget(p)
            if widget is None:
                continue
            row = QWidget()
            lay = QVBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(3)
            lab = QLabel(p.text)
            lab.setWordWrap(True)
            lay.addWidget(lab)
            lay.addWidget(widget)
            self.form.addWidget(row)
            self.rows[p.key] = (p, widget, row)
        self.form.addStretch(1)
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 6, 0, 0)
        reset = QPushButton("Reset")
        reset.clicked.connect(self._reset)
        apply = QPushButton("Apply")
        apply.setObjectName("primary")
        apply.clicked.connect(self._apply)
        h.addWidget(reset)
        h.addStretch(1)
        h.addWidget(apply)
        self.form.addWidget(bar)
        self._refresh_conditions()

    def _widget(self, p):
        val = self.values.get(p.key, p.value)
        if p.kind == "bool":
            w = QCheckBox()
            w.setChecked(str(val).lower() in ("1", "true"))
            w.toggled.connect(lambda on, k=p.key: self._set(k, "true" if on else "false"))
            return w
        if p.kind == "slider":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            steps = max(1, int(round((p.mx - p.mn) / (p.step or 0.01))))
            sl = QSlider(Qt.Horizontal)
            sl.setRange(0, steps)
            spin = QDoubleSpinBox()
            spin.setRange(p.mn, p.mx)
            spin.setSingleStep(p.step or 0.01)
            spin.setDecimals(max(0, min(6, p.precision)))
            try:
                spin.setValue(float(val))
            except (TypeError, ValueError):
                spin.setValue(p.mn)
            sl.setValue(int(round((spin.value() - p.mn) / (p.step or 0.01))))
            sl.valueChanged.connect(
                lambda i, s=spin: s.setValue(p.mn + i * (p.step or 0.01)))
            spin.valueChanged.connect(
                lambda v, s=sl, k=p.key: (
                    s.blockSignals(True),
                    s.setValue(int(round((v - p.mn) / (p.step or 0.01)))),
                    s.blockSignals(False),
                    self._set(k, f"{v:g}")))
            h.addWidget(sl, 1)
            h.addWidget(spin)
            w._value = lambda s=spin: f"{s.value():g}"
            return w
        if p.kind == "color":
            w = QPushButton()
            w.setFixedHeight(24)
            r, g, b = properties.color_to_rgb(val)
            w._rgb = (r, g, b)

            def paint(btn=w):
                c = QColor.fromRgbF(*btn._rgb)
                btn.setText(c.name())
                btn.setStyleSheet(f"background:{c.name()}; color:"
                                  f"{'#000' if c.lightnessF() > 0.5 else '#fff'};")
            paint()

            def pick(_=False, btn=w, key=p.key):
                c = QColorDialog.getColor(QColor.fromRgbF(*btn._rgb), self, "Pick a colour")
                if c.isValid():
                    btn._rgb = (c.redF(), c.greenF(), c.blueF())
                    paint(btn)
                    self._set(key, properties.rgb_to_color(*btn._rgb))
            w.clicked.connect(pick)
            return w
        if p.kind == "combo":
            w = QComboBox()
            for label, value in p.options:
                w.addItem(label, value)
            i = w.findData(str(val))
            w.setCurrentIndex(i if i >= 0 else 0)
            w.currentIndexChanged.connect(
                lambda _i, c=w, k=p.key: self._set(k, str(c.currentData())))
            return w
        if p.kind in ("textinput", "scenetexture"):
            w = QLineEdit(str(val))
            w.setPlaceholderText("a texture path, relative to the wallpaper"
                                 if p.kind == "scenetexture" else "")
            w.textChanged.connect(lambda t, k=p.key: self._set(k, t))
            return w
        return None

    def _set(self, key, value):
        self.values[key] = value
        self._refresh_conditions()

    def _refresh_conditions(self):
        for key, (p, _widget, row) in self.rows.items():
            row.setVisible(properties.visible(p, self.values))

    def _reset(self):
        base = properties.defaults(self.props)
        self.apply_cb(self.wid, {})
        self.load(self.wid, {})
        self.values = dict(base)

    def _apply(self):
        base = properties.defaults(self.props)
        changed = {k: v for k, v in self.values.items() if str(base.get(k)) != str(v)}
        self.apply_cb(self.wid, changed)


def _muted(text):
    lab = QLabel(text)
    lab.setObjectName("muted")
    lab.setWordWrap(True)
    return lab


# --------------------------------------------------------------------------- #
#  Browser
# --------------------------------------------------------------------------- #
class Browser(QWidget):
    status = Signal(str)
    imported = Signal()

    def __init__(self, jobs: Jobs, cfg: dict, card_width=224):
        super().__init__()
        self.jobs = jobs
        self.cfg = cfg
        self.page = 1
        self.last_page = 1
        self.rows: list = []
        self.cards: dict = {}
        self.selected = None
        self._thumbs = None
        self._seq = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 10, 12, 10)
        h.setSpacing(8)
        self.tabs = QComboBox()
        self.tabs.addItem("Wallhaven", "wallhaven")
        self.tabs.addItem("Steam Workshop", "workshop")
        self.tabs.currentIndexChanged.connect(lambda _: self.search(reset=True))
        self.query = QLineEdit()
        self.query.setPlaceholderText("search — Wallhaven matches tags, so one or two words work best")
        self.query.returnPressed.connect(lambda: self.search(reset=True))
        go = QPushButton("Search")
        go.clicked.connect(lambda: self.search(reset=True))
        self.prev = QPushButton("<")
        self.prev.clicked.connect(lambda: self.step(-1))
        self.nxt = QPushButton(">")
        self.nxt.clicked.connect(lambda: self.step(+1))
        self.pagelabel = QLabel("—")
        self.pagelabel.setObjectName("muted")
        self.getbtn = QPushButton("Add to library")
        self.getbtn.setObjectName("primary")
        self.getbtn.setEnabled(False)
        self.getbtn.clicked.connect(self.fetch)
        for w in (self.tabs, self.query, go, self.prev, self.pagelabel, self.nxt, self.getbtn):
            h.addWidget(w, 1 if w is self.query else 0)
        v.addWidget(bar)
        self.grid = CardGrid(card_width)
        v.addWidget(self.grid, 1)

    @property
    def source(self):
        return self.tabs.currentData()

    def step(self, d):
        self.page = max(1, min(self.page + d, max(1, self.last_page)))
        self.search(reset=False)

    def search(self, reset=True):
        if reset:
            self.page = 1
        self.status.emit(f"searching {self.source}…")
        self.getbtn.setEnabled(False)
        q, page, src = self.query.text().strip(), self.page, self.source
        self._seq += 1
        seq = self._seq
        if src == "wallhaven":
            fn = lambda: sources.wallhaven(q, page, self.cfg.get("wallhaven_sort", ""),
                                           purity=self.cfg.get("wallhaven_purity", "sfw"),
                                           api_key=self.cfg.get("wallhaven_key", ""))
        else:
            fn = lambda: sources.workshop(q, page)
        self.jobs.start(fn, self._results, self._failed, tag=seq)

    def _failed(self, seq, msg):
        if seq is not None and seq != self._seq:
            return                        # an older request; its error is stale too
        self.status.emit(msg)

    def _results(self, seq, payload):
        if seq is not None and seq != self._seq:
            return                        # a slower earlier search landing late
        self.rows, self.last_page = payload
        self.pagelabel.setText(f"{self.page} / {self.last_page}")
        self.cards = {}
        widgets = []
        for row in self.rows:
            c = ResultCard(row, self.grid.card_width)
            c.picked.connect(self._pick)
            self.cards[row.id] = c
            widgets.append(c)
        self.grid.set_cards(widgets)
        if self._thumbs is not None:
            self._thumbs.cancel()
        self._thumbs = ThumbJob(self.rows)
        self._thumbs.ready.connect(self._thumb)
        self.jobs.start_object(self._thumbs)
        if not self.rows:
            self.status.emit("no results" + (
                " — Wallhaven matches tags; try a single word"
                if self.source == "wallhaven" and self.query.text().strip() else ""))
        else:
            self.status.emit(f"{len(self.rows)} result(s) on page {self.page}")

    def _thumb(self, rid, path):
        card = self.cards.get(rid)
        if card is not None:
            card.set_thumb(path)

    def _pick(self, row):
        self.selected = row
        self.getbtn.setEnabled(True)
        avail = sources.available()
        if row.source == "workshop" and not avail["workshop_download"]:
            self.getbtn.setText("Open in Steam")
        else:
            self.getbtn.setText("Add to library")
        self.status.emit(f"{row.title} · {row.meta}")

    def fetch(self):
        row = self.selected
        if row is None:
            return
        self.getbtn.setEnabled(False)
        self.status.emit(f"fetching {row.id}…")
        self.jobs.start(lambda r=row: sources.fetch(r), self._fetched,
                        self._failed, tag=self._seq)

    def _fetched(self, _tag, result):
        ok, msg = result
        self.status.emit(msg)
        self.getbtn.setEnabled(True)
        if ok:
            self.imported.emit()


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #
class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("fossypaper — settings")
        self.setMinimumWidth(520)
        self.cfg = cfg
        self.w = {}
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        outs = [""] + engine.outputs()
        tabs.addTab(self._page([
            ("Output", self._combo("output", outs, labels={"": "every screen"})),
            ("Span across", self._line("span", ",".join(cfg.get("span") or []),
                                       "DP-1,HDMI-A-1 — one wallpaper stretched over both")),
            ("Layer", self._combo("layer", ["bottom", "background", "top"])),
            ("Frame rate", self._spin("fps", 1, 240)),
            ("Scaling", self._combo("scaling", ["", "default", "stretch", "fit", "fill"],
                                    labels={"": "the wallpaper's own"})),
            ("Clamp", self._combo("clamp", ["", "clamp", "border", "repeat"],
                                  labels={"": "the wallpaper's own"})),
            ("GPU / EGL", self._combo("gpu", ["auto", "nvidia", "mesa"])),
            ("Assets dir", self._line("assets_dir", cfg.get("assets_dir", ""),
                                      "only if your Wallpaper Engine assets live elsewhere")),
        ], "Where the wallpaper is drawn and how it is scaled."), "Render")

        tabs.addTab(self._page([
            ("Mute", self._check("silent")),
            ("Volume", self._spin("volume", 0, 100)),
            ("Keep playing when other apps make sound", self._check("no_automute")),
            ("Audio reactivity", self._combo(
                "audio_processing", ["auto", "always", "never"],
                labels={"auto": "auto — only wallpapers that ask for it",
                        "always": "always — every wallpaper",
                        "never": "never"})),
        ], "Audio *processing* is not the same as audio output: muting still leaves a\n"
           "capture stream open on your sink monitor, and those accumulate. 'auto'\n"
           "turns it on only for wallpapers whose own manifest says they react to sound."),
            "Audio")

        tabs.addTab(self._page([
            ("Pause behind fullscreen apps", self._check("fullscreen_pause")),
            ("…only when that window is focused", self._check("pause_only_active")),
            ("Never pause for these app ids", self._line(
                "pause_ignore_appids", ",".join(cfg.get("pause_ignore_appids") or []),
                "comma separated, e.g. mpv,vlc")),
            ("Disable particles", self._check("no_particles")),
            ("Disable parallax", self._check("no_parallax")),
            ("Disable mouse interaction", self._check("no_mouse")),
        ], "A wallpaper still renders behind a fullscreen game unless you stop it."),
            "Performance")

        tabs.addTab(self._page([
            ("Theme", self._combo("ui_theme", list(theme.THEMES))),
            ("Follow the wallpaper's colours", self._check("ui_follow_wallpaper")),
            ("Accent override", self._line("ui_accent", cfg.get("ui_accent", ""),
                                           "#rrggbb, or blank for the theme's own")),
            ("Card width", self._spin("ui_card_width", 140, 420)),
            ("Font", self._line("ui_font", cfg.get("ui_font", ""),
                                "a family name, or blank for the system font")),
        ], "Flat by design — no gradients anywhere."), "Appearance")

        tabs.addTab(self._page([
            ("Wallhaven API key", self._line("wallhaven_key", cfg.get("wallhaven_key", ""),
                                             "only needed for non-SFW results")),
            ("Wallhaven purity", self._combo("wallhaven_purity", ["sfw", "sketchy", "both"])),
            ("Steam login", self._line("steam_login", cfg.get("steam_login", ""),
                                       "the account that owns Wallpaper Engine (steamcmd)")),
            ("Palette generator", self._combo("theme_backend", engine.theme_backends())),
            ("Re-theme on every apply", self._check("theme_on_apply")),
        ], "Credentials stay in your own config file and go only to the site they belong to."),
            "Sources")

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    # -- builders --------------------------------------------------------- #
    def _page(self, rows, note=""):
        page = QWidget()
        lay = QVBoxLayout(page)
        group = QGroupBox()
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)
        for label, widget in rows:
            form.addRow(label, widget)
        lay.addWidget(group)
        if note:
            lay.addWidget(_muted(note))
        lay.addStretch(1)
        return page

    def _combo(self, key, items, labels=None):
        c = QComboBox()
        for it in items:
            c.addItem((labels or {}).get(it, it or "—"), it)
        i = c.findData(self.cfg.get(key, ""))
        c.setCurrentIndex(i if i >= 0 else 0)
        self.w[key] = ("data", c)
        return c

    def _spin(self, key, lo, hi):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(self.cfg.get(key, lo)))
        self.w[key] = ("int", s)
        return s

    def _check(self, key):
        c = QCheckBox()
        c.setChecked(bool(self.cfg.get(key, False)))
        self.w[key] = ("bool", c)
        return c

    def _line(self, key, value, placeholder=""):
        e = QLineEdit(str(value))
        e.setPlaceholderText(placeholder)
        self.w[key] = ("list" if isinstance(config.DEFAULTS.get(key), list) else "text", e)
        return e

    def values(self):
        out = {}
        for key, (kind, widget) in self.w.items():
            if kind == "data":
                out[key] = widget.currentData()
            elif kind == "int":
                out[key] = widget.value()
            elif kind == "bool":
                out[key] = widget.isChecked()
            elif kind == "list":
                out[key] = [p.strip() for p in widget.text().split(",") if p.strip()]
            else:
                out[key] = widget.text().strip()
        return out


class DocDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("fossypaper — privacy and terms")
        self.resize(720, 620)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        for name, fname in (("Privacy", "PRIVACY.md"), ("Terms", "TERMS.md")):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            path = DOCS / fname
            view.setPlainText(path.read_text() if path.is_file() else f"missing: {path}")
            tabs.addTab(view, name)
        lay.addWidget(tabs)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        lay.addWidget(close)


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fossypaper")
        self.resize(1120, 760)
        self.cfg = config.load()
        self.jobs = Jobs()
        self.lib = engine.scan_library()
        self.cards = {}
        self.selected = self.cfg.get("current", "")

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        for name, fn, tip in (
            ("Apply", self._apply, "apply the selected wallpaper"),
            ("Off", self._off, "stop the wallpaper"),
            ("Theme", self._theme, "read a palette out of the wallpaper's pixels"),
            ("Browse", self._toggle_browse, "Wallhaven and the Steam Workshop"),
            ("Refresh", self._refresh, "rescan the library"),
            ("Settings", self._settings, ""),
            ("Privacy", self._docs, "what leaves this machine, and where it goes"),
        ):
            act = QAction(name, self)
            act.setToolTip(tip)
            act.triggered.connect(fn)
            tb.addAction(act)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter the library")
        self.search.setMaximumWidth(240)
        self.search.textChanged.connect(self._build_grid)
        tb.addWidget(self.search)
        self.hidevid = QCheckBox("hide video")
        self.hidevid.stateChanged.connect(self._build_grid)
        tb.addWidget(self.hidevid)

        self.stack = QStackedWidget()
        self.grid = CardGrid(int(self.cfg.get("ui_card_width", 224)))
        self.browser = Browser(self.jobs, self.cfg, int(self.cfg.get("ui_card_width", 224)))
        self.browser.status.connect(self._status)
        self.browser.imported.connect(self._refresh)
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.browser)
        self.setCentralWidget(self.stack)

        self.dock = PropertyDock(self._apply_props, self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [330], Qt.Horizontal)
        self.setStatusBar(QStatusBar())

        self._restyle()
        self._build_grid()
        if self.selected:
            self.dock.load(self.selected, self.cfg.get("properties", {}).get(self.selected, {}))

    # -- appearance ------------------------------------------------------- #
    def _restyle(self):
        pal = theme.palette(self.cfg.get("ui_theme", theme.DEFAULT),
                            self.cfg.get("ui_accent", ""),
                            engine.palette() if self.cfg.get("ui_follow_wallpaper") else None)
        self.setStyleSheet(theme.stylesheet(pal, self.cfg.get("ui_font", ""),
                                            int(self.cfg.get("ui_card_width", 224))))

    # -- library ---------------------------------------------------------- #
    def _visible(self):
        q = self.search.text().strip().lower()
        out = []
        for w in self.lib:
            if self.hidevid.isChecked() and w.video:
                continue
            if q and q not in w.title.lower() and q not in w.id.lower():
                continue
            out.append(w)
        return out

    def _build_grid(self):
        width = int(self.cfg.get("ui_card_width", 224))
        self.grid.card_width = width
        self.cards = {}
        widgets = []
        current = self.cfg.get("current", "")
        for wp in self._visible():
            c = Card(wp, width)
            c.picked.connect(self._pick)
            c.opened.connect(lambda w=None: self._apply())
            c.mark(selected=wp.id == self.selected, current=wp.id == current)
            self.cards[wp.id] = c
            widgets.append(c)
        self.grid.set_cards(widgets)
        broken = sum(1 for w in self.lib if not engine.backend_for(w)[1])
        self._status(f"{len(widgets)} shown · {len(self.lib)} in library"
                     + (f" · {broken} can't render here (see `fossypaper doctor`)" if broken else ""))

    def _pick(self, wp):
        self.selected = wp.id
        current = self.cfg.get("current", "")
        for wid, card in self.cards.items():
            card.mark(selected=wid == wp.id, current=wid == current)
        self.dock.load(wp.id, self.cfg.get("properties", {}).get(wp.id, {}))
        self._status(f"{wp.title} · {wp.type}"
                     + (" · video-backed" if wp.video and wp.type != "video" else "")
                     + (" · audio-reactive" if wp.audio else ""))

    # -- actions ---------------------------------------------------------- #
    def _apply(self):
        if not self.selected:
            self._status("pick a wallpaper first")
            return
        o = config.opts(self.cfg)
        o["properties"] = self.cfg.get("properties", {}).get(self.selected, {})
        ok, msg = engine.start(self.selected, o)
        if ok:
            self.cfg["current"] = self.selected
            config.save(self.cfg)
            for wid, card in self.cards.items():
                card.mark(current=wid == self.selected)
            if self.cfg.get("theme_on_apply"):
                self._theme()
        self._status(msg)

    def _off(self):
        engine.stop()
        self._status("wallpaper off")

    def _theme(self):
        if not self.selected:
            return
        self._status("rendering a frame and reading its colours…")
        QApplication.processEvents()
        ok, msg = engine.sync_theme(self.selected, config.opts(self.cfg),
                                    self.cfg.get("theme_backend", "builtin"))
        if ok and self.cfg.get("ui_follow_wallpaper"):
            self._restyle()
        self._status(msg)

    def _apply_props(self, wid, props):
        store = self.cfg.setdefault("properties", {})
        if props:
            store[wid] = props
        else:
            store.pop(wid, None)
        config.save(self.cfg)
        self.selected = wid
        self._apply()

    def _toggle_browse(self):
        on_browser = self.stack.currentWidget() is self.browser
        self.stack.setCurrentWidget(self.grid if on_browser else self.browser)
        self.dock.setVisible(on_browser)
        if not on_browser and not self.browser.rows:
            self.browser.search(reset=True)

    def _refresh(self):
        self.lib = engine.scan_library()
        self._build_grid()

    def _settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            self.cfg.update(d.values())
            config.save(self.cfg)
            self.browser.cfg = self.cfg
            self._restyle()
            self._build_grid()

    def _docs(self):
        DocDialog(self).exec()

    def _status(self, m):
        self.statusBar().showMessage(str(m))

    def closeEvent(self, e):
        self.jobs.wait()
        super().closeEvent(e)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("fossypaper")
    app.setDesktopFileName("fossypaper")
    if not engine.library_roots():
        QMessageBox.information(
            None, "fossypaper",
            f"No wallpaper library yet.\n\nLooked in:\n{engine.WE_DIR}\n\n"
            "Subscribe to wallpapers in Steam, set FOSSYPAPER_LIBRARY, or use\n"
            "Browse to pull one from Wallhaven.")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
