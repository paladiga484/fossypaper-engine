"""fossypaper — the desktop app.

Same bones as before: a toolbar, a grid of wallpapers, a properties dock on the
right, a status line at the bottom. What changed is what those bones carry — a
built-in browser for Wallhaven and the Steam Workshop, a property editor driven
by the wallpaper's own schema (including the conditions that say which knobs
matter), and a look you can change.

Flat surfaces throughout. See theme.py for why.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QSize, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QMovie, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
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
class ElidedLabel(QLabel):
    """A one-line label that ends in … instead of being cut mid-glyph. The full
    text rides in the tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = str(text or "")
        self.setToolTip(self._full)
        self._elide()

    def full(self):
        return self._full

    def _elide(self):
        w = max(0, self.width() - 2)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, w)
                        if w else self._full)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


_KIND = {"wpe": "scene", "mpvpaper": "video", "swww": "still",
         "missing_assets": "broken", "missing_dependency": "needs base"}


def kind_of(wp) -> str:
    be, _ok = engine.backend_for(wp)
    if be == "mpvpaper" and wp.type == "image":
        return "still"
    return _KIND.get(be, be)


class Card(QFrame):
    """One wallpaper. Animated previews play on hover only — the library is
    forty of these and forty looping GIFs is not a free thing to ask for."""
    picked = Signal(object)
    opened = Signal(object)
    menu = Signal(object, object)          # wp, global position

    def __init__(self, wp, width=224):
        super().__init__()
        self.wp = wp
        self._movie = None
        self.setObjectName("card")
        thumb_h = int(width * 9 / 16)
        self.setFixedSize(width, thumb_h + 44)
        self.setProperty("selected", False)
        self.setProperty("current", False)
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.nowbar = QFrame()
        self.nowbar.setObjectName("nowbar")
        self.nowbar.setFixedHeight(3)
        v.addWidget(self.nowbar)
        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedHeight(thumb_h - 3)
        self.thumb.setAlignment(Qt.AlignCenter)
        v.addWidget(self.thumb)
        foot = QWidget()
        foot.setObjectName("cardFoot")
        row = QHBoxLayout(foot)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(8)
        title = ElidedLabel(wp.title)
        title.setObjectName("cardTitle")
        be, ok = engine.backend_for(wp)
        self.badge = QLabel()
        self.badge.setObjectName("badgeOk" if ok else "badgeWarn")
        self._kind = kind_of(wp) + ("" if ok else " !")
        self.badge.setText(self._kind.upper())
        title.setToolTip(wp.title + "\n" + (
            engine.why_unsupported(wp) or engine.render_note(wp) or
            {"wpe": "Wallpaper Engine scene, via linux-wallpaperengine",
             "mpvpaper": "video, via mpvpaper",
             "swww": "still image, via swww"}.get(be, be)
            + ("" if ok else " — not installed")))
        row.addWidget(title, 1)
        row.addWidget(self.badge)
        v.addWidget(foot, 1)
        self._load_preview(width, thumb_h - 3)

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
        if e.button() == Qt.LeftButton:
            self.picked.emit(self.wp)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.opened.emit(self.wp)

    def contextMenuEvent(self, e):
        self.picked.emit(self.wp)
        self.menu.emit(self.wp, e.globalPos())

    def mark(self, selected=None, current=None):
        if selected is not None:
            self.setProperty("selected", selected)
        if current is not None:
            self.setProperty("current", current)
            self.badge.setText("▶ NOW" if current else self._kind.upper())
        for w in (self, self.nowbar):
            w.setProperty("current", self.property("current"))
            w.style().unpolish(w)
            w.style().polish(w)


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
        t = ElidedLabel(row.title)
        t.setObjectName("cardTitle")
        t.setToolTip(f"{row.title}\n{row.meta}\n{row.page_url}")
        state = QLabel("in library" if row.installed() else row.kind)
        state.setObjectName("badgeOk" if row.installed() else "badgeWarn")
        line.addWidget(t, 1)
        line.addWidget(state)
        v.addLayout(line)
        meta = ElidedLabel(row.meta)
        meta.setObjectName("muted")
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
        self._stretched = -1
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
        # clear the previous filler row first — reflowing from 5 columns to 3
        # otherwise leaves a stretched row in the middle of the grid
        if self._stretched >= 0:
            self._grid.setRowStretch(self._stretched, 0)
        self._stretched = len(self._widgets) // cols + 1
        self._grid.setRowStretch(self._stretched, 1)

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

    set_wallpaper = Signal()

    def __init__(self, apply_cb, parent=None):
        super().__init__("SELECTED", parent)
        self.apply_cb = apply_cb
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.setMinimumWidth(300)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget()
        head.setObjectName("dockHead")
        hv = QVBoxLayout(head)
        hv.setContentsMargins(14, 14, 14, 14)
        hv.setSpacing(6)
        self.head_title = ElidedLabel("nothing selected")
        self.head_title.setObjectName("dockTitle")
        self.head_meta = ElidedLabel("")
        self.head_meta.setObjectName("muted")
        self.head_go = QPushButton("Set as wallpaper")
        self.head_go.setObjectName("primary")
        self.head_go.setEnabled(False)
        self.head_go.clicked.connect(self.set_wallpaper)
        hv.addWidget(self.head_title)
        hv.addWidget(self.head_meta)
        hv.addSpacing(4)
        hv.addWidget(self.head_go)
        outer.addWidget(head)
        cap = QLabel("PROPERTIES")
        cap.setObjectName("caption")
        outer.addWidget(cap)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        outer.addWidget(self.scroll, 1)
        self.setWidget(host)
        self.body = QWidget()
        self.form = QVBoxLayout(self.body)
        self.form.setContentsMargins(10, 10, 10, 10)
        self.form.setSpacing(8)
        self.scroll.setWidget(self.body)
        self.props: list = []
        self.values: dict = {}
        self.rows: dict = {}
        self.wid = ""

    def describe(self, wp, is_current: bool):
        self.head_title.setText(wp.title if wp else "nothing selected")
        if wp:
            bits = [kind_of(wp), wp.id]
            if wp.audio:
                bits.append("reacts to sound")
            self.head_meta.setText(" · ".join(bits))
        self.head_go.setEnabled(bool(wp) and engine.backend_for(wp)[1])
        self.head_go.setText("Reapply" if is_current else "Set as wallpaper")

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
            self.form.addWidget(_muted("This one has no knobs to turn. It is what it is."))
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
            if p.kind == "bool":
                widget.setText(p.text)
                self.form.addWidget(widget)
                self.rows[p.key] = (p, widget, widget)
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
        apply = QPushButton("Apply changes")
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
            ("Desktop", self._combo("host", ["auto", "layer", "plasma", "gnome", "x11"],
                                    labels={"auto": f"detect ({engine.detect_host()})",
                                            "layer": "layer-shell (Hyprland, niri, sway…)",
                                            "plasma": "Plasma wallpaper",
                                            "gnome": "GNOME background",
                                            "x11": "X11 root window"})),
            ("Layer", self._combo("layer", ["auto", "bottom", "background", "top"],
                                  labels={"auto": "auto — background, or bottom over a shell's own"})),
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
#  Now playing — the one loud thing in the window
# --------------------------------------------------------------------------- #
class NowPlaying(QFrame):
    """What is on your desktop right now, and the three things you do to it."""
    toggle = Signal()
    shuffle = Signal()
    theme = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("hero")
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 14, 16, 14)
        h.setSpacing(16)
        self.art = QLabel()
        self.art.setObjectName("heroArt")
        self.art.setFixedSize(176, 99)
        self.art.setAlignment(Qt.AlignCenter)
        h.addWidget(self.art)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.state = QLabel()
        self.state.setObjectName("heroState")
        self.title = ElidedLabel("")
        self.title.setObjectName("heroTitle")
        self.meta = ElidedLabel("")
        self.meta.setObjectName("muted")
        text.addStretch(1)
        text.addWidget(self.state)
        text.addWidget(self.title)
        text.addWidget(self.meta)
        text.addStretch(1)
        h.addLayout(text, 1)
        self.play = QPushButton()
        self.play.setObjectName("primary")
        self.play.setMinimumWidth(104)
        self.play.clicked.connect(self.toggle)
        shuf = QPushButton("Shuffle")
        shuf.setToolTip("a random wallpaper from the library")
        shuf.clicked.connect(self.shuffle)
        pal = QPushButton("Palette")
        pal.setToolTip("read a colour palette out of the wallpaper's pixels")
        pal.clicked.connect(self.theme)
        for b in (self.play, shuf, pal):
            b.setMinimumHeight(34)
            h.addWidget(b)

    def show_state(self, wp, running: bool, busy: str = ""):
        state = busy or ("NOW PLAYING" if running else "STOPPED")
        self.state.setText(state)
        self.state.setProperty("live", running and not busy)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)
        self.play.setText("■  Stop" if running else "▶  Play")
        self.play.setEnabled(not busy and (running or wp is not None))
        if wp is None:
            self.title.setText("No wallpaper yet")
            self.meta.setText("pick one below and press Enter")
            self.art.clear()
            self.art.setText("—")
            return
        self.title.setText(wp.title)
        bits = [kind_of(wp)]
        if wp.audio:
            bits.append("reacts to sound")
        if wp.tags:
            bits.append(", ".join(str(t) for t in wp.tags[:3]))
        self.meta.setText("  ·  ".join(bits))
        pm = QPixmap(str(wp.preview)) if wp.preview and wp.preview.is_file() else QPixmap()
        if pm.isNull():
            self.art.setText("no preview")
        else:
            self.art.setPixmap(pm.scaled(self.art.size(), Qt.KeepAspectRatioByExpanding,
                                         Qt.SmoothTransformation))


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #
FILTERS = (("All", None), ("Scenes", "scene"), ("Video", "video"),
           ("Stills", "still"), ("Reactive", "audio"))
SORTS = (("A – Z", "title"), ("Newest", "new"), ("Kind", "kind"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fossypaper")
        self.resize(1240, 820)
        self.cfg = config.load()
        self.jobs = Jobs()
        self.lib = engine.scan_library()
        self.cards = {}
        self.selected = self.cfg.get("current", "")
        self.running = engine.is_running()
        self.busy = ""

        central = QWidget()
        col = QVBoxLayout(central)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.hero = NowPlaying()
        self.hero.toggle.connect(self._toggle)
        self.hero.shuffle.connect(self._shuffle)
        self.hero.theme.connect(self._theme)
        col.addWidget(self.hero)

        nav = QWidget()
        nav.setObjectName("navbar")
        nh = QHBoxLayout(nav)
        nh.setContentsMargins(12, 8, 12, 8)
        nh.setSpacing(6)
        self.tab_lib = QPushButton("Library")
        self.tab_web = QPushButton("Browse")
        for b in (self.tab_lib, self.tab_web):
            b.setObjectName("navtab")
            b.setCheckable(True)
            nh.addWidget(b)
        self.tab_lib.setChecked(True)
        self.tab_lib.clicked.connect(lambda: self._show(self.grid))
        self.tab_web.clicked.connect(lambda: self._show(self.browser))
        nh.addSpacing(18)
        self.chips = QButtonGroup(self)
        self.chips.setExclusive(True)
        self.chip_row = QWidget()
        ch = QHBoxLayout(self.chip_row)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(4)
        for i, (label, key) in enumerate(FILTERS):
            b = QPushButton(label)
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setProperty("key", key)
            self.chips.addButton(b, i)
            ch.addWidget(b)
        self.chips.button(0).setChecked(True)
        self.chips.idClicked.connect(lambda _i: self._build_grid())
        nh.addWidget(self.chip_row)
        nh.addStretch(1)
        self.sort = QComboBox()
        for label, key in SORTS:
            self.sort.addItem(label, key)
        i = self.sort.findData(self.cfg.get("ui_sort", "title"))
        self.sort.setCurrentIndex(max(0, i))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        nh.addWidget(self.sort)
        self.search = QLineEdit()
        self.search.setPlaceholderText("filter   ( / )")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(self._build_grid)
        nh.addWidget(self.search)
        for name, fn in (("Refresh", self._refresh), ("Settings", self._settings),
                         ("Privacy", self._docs)):
            b = QPushButton(name)
            b.setObjectName("flat")
            b.clicked.connect(fn)
            nh.addWidget(b)
        col.addWidget(nav)

        self.stack = QStackedWidget()
        self.grid = CardGrid(int(self.cfg.get("ui_card_width", 224)))
        self.browser = Browser(self.jobs, self.cfg, int(self.cfg.get("ui_card_width", 224)))
        self.browser.status.connect(self._status)
        self.browser.imported.connect(self._refresh)
        self.empty = QLabel("Nothing here matches.\nThe library is larger than your filter.")
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.empty)
        col.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.dock = PropertyDock(self._apply_props, self)
        self.dock.set_wallpaper.connect(self._apply)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.resizeDocks([self.dock], [340], Qt.Horizontal)
        self.setStatusBar(QStatusBar())
        self.counts = QLabel()
        self.counts.setObjectName("muted")
        self.statusBar().addPermanentWidget(self.counts)

        # Enter applies only from the grid — anywhere else it belongs to the
        # field you're typing in.
        for keys in ("Return", "Enter"):
            act = QAction(self.grid)
            act.setShortcut(keys)
            act.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            act.triggered.connect(self._apply)
            self.grid.addAction(act)
        for keys, fn in (("Ctrl+F", self._focus_search), ("/", self._focus_search),
                         ("Escape", self._escape), ("Ctrl+B", self._toggle_browse),
                         ("F5", self._refresh), ("Ctrl+Space", self._toggle),
                         ("Ctrl+R", self._shuffle)):
            act = QAction(self)
            act.setShortcut(keys)
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(fn)
            self.addAction(act)

        self._restyle()
        self._build_grid()
        start = next((w for w in self.lib if w.id == self.selected), None)
        if start is not None:
            self._pick(start)
        self._refresh_hero()

        # The renderer can die, or be stopped from the bar plugin, behind our
        # back. Cheap to ask (two pgreps), so ask while the window is up.
        self._poll = QTimer(self, interval=2500)
        self._poll.timeout.connect(self._poll_running)
        self._poll.start()

    # -- appearance ------------------------------------------------------- #
    def _restyle(self):
        pal = theme.palette(self.cfg.get("ui_theme", theme.DEFAULT),
                            self.cfg.get("ui_accent", ""),
                            engine.palette() if self.cfg.get("ui_follow_wallpaper") else None)
        try:
            arrows = theme.arrow_icons(pal, Path.home() / ".cache/fossypaper/ui")
        except OSError:
            arrows = None
        self.setStyleSheet(theme.stylesheet(pal, self.cfg.get("ui_font", ""),
                                            int(self.cfg.get("ui_card_width", 224)), arrows))

    def _current_wp(self):
        cur = self.cfg.get("current", "")
        return next((w for w in self.lib if w.id == cur), None) if cur else None

    def _refresh_hero(self):
        self.hero.show_state(self._current_wp(), self.running, self.busy)

    def _poll_running(self):
        if self.busy or not self.isVisible():
            return
        now = engine.is_running()
        if now != self.running:
            self.running = now
            self._refresh_hero()

    # -- library ---------------------------------------------------------- #
    def _visible(self):
        q = self.search.text().strip().lower()
        chip = self.chips.checkedButton()
        key = chip.property("key") if chip else None
        out = []
        for w in self.lib:
            if key == "audio" and not w.audio:
                continue
            if key and key != "audio" and kind_of(w) != key:
                continue
            if q and q not in w.title.lower() and q not in w.id.lower() \
                    and not any(q in str(t).lower() for t in w.tags):
                continue
            out.append(w)
        how = self.sort.currentData()
        if how == "new":
            out.sort(key=lambda w: _mtime(w.folder), reverse=True)
        elif how == "kind":
            out.sort(key=lambda w: (kind_of(w), w.title.lower()))
        else:
            out.sort(key=lambda w: (not w.supported, w.title.lower()))
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
            c.opened.connect(self._open)
            c.menu.connect(self._card_menu)
            c.mark(selected=wp.id == self.selected, current=wp.id == current)
            self.cards[wp.id] = c
            widgets.append(c)
        self.grid.set_cards(widgets)
        if self.stack.currentWidget() is not self.browser:
            self.stack.setCurrentWidget(self.grid if widgets else self.empty)
        broken = sum(1 for w in self.lib if not engine.backend_for(w)[1])
        self.counts.setText(f"{len(widgets)} shown · {len(self.lib)} in library"
                            + (f" · {broken} can't render here" if broken else ""))

    def _sort_changed(self):
        self.cfg["ui_sort"] = self.sort.currentData()
        config.save(self.cfg)
        self._build_grid()

    def _pick(self, wp):
        if wp is None:
            return
        if self.stack.currentWidget() is self.grid:
            self.grid.setFocus()
        self.selected = wp.id
        current = self.cfg.get("current", "")
        for wid, card in self.cards.items():
            card.mark(selected=wid == wp.id, current=wid == current)
        self.dock.describe(wp, wp.id == current)
        self.dock.load(wp.id, self.cfg.get("properties", {}).get(wp.id, {}))
        note = engine.why_unsupported(wp) or engine.render_note(wp)
        self._status(f"{wp.title}" + (f" — {note}" if note else ""))

    def _open(self, wp):
        self._pick(wp)
        self._apply()

    def _card_menu(self, wp, pos):
        m = QMenu(self)
        m.addAction("Set as wallpaper", self._apply).setEnabled(engine.backend_for(wp)[1])
        m.addSeparator()
        m.addAction("Open folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(wp.folder))))
        m.addAction("Copy id", lambda: QApplication.clipboard().setText(wp.id))
        m.exec(pos)

    # -- actions ---------------------------------------------------------- #
    def _apply(self, wid=None):
        wid = wid if isinstance(wid, str) else self.selected
        if not wid:
            self._status("pick a wallpaper first")
            return
        if self.busy:
            return
        o = config.opts(self.cfg)
        o["properties"] = self.cfg.get("properties", {}).get(wid, {})
        wp = next((w for w in self.lib if w.id == wid), None)
        self.busy = "SUMMONING…"
        self.hero.show_state(wp, False, self.busy)
        self._status(f"starting {wp.title if wp else wid}…")
        # start() waits a moment to see the renderer survive its first frames;
        # that wait belongs on a worker, not on the window.
        self.jobs.start(lambda: engine.start(wid, o), self._applied, self._apply_failed, tag=wid)

    def _applied(self, wid, result):
        ok, msg = result
        self.busy = ""
        if ok:
            self.cfg["current"] = wid
            config.save(self.cfg)
            for w, card in self.cards.items():
                card.mark(current=w == wid)
            if self.selected == wid:
                self.dock.describe(self._current_wp(), True)
            if self.cfg.get("theme_on_apply"):
                self._theme()
        self.running = engine.is_running()
        self._refresh_hero()
        self._status(msg)

    def _apply_failed(self, wid, message):
        self.busy = ""
        self.running = engine.is_running()
        self._refresh_hero()
        self._status(f"couldn't start it: {message}")

    def _toggle(self):
        if self.busy:
            return
        if self.running:
            engine.stop()
            self.running = False
            self._refresh_hero()
            self._status("wallpaper off")
        else:
            wid = self.cfg.get("current") or self.selected
            if wid:
                self._apply(wid)

    def _shuffle(self):
        cur = self.cfg.get("current", "")
        pool = [w for w in self._visible() if engine.backend_for(w)[1] and w.id != cur] \
            or [w for w in self.lib if engine.backend_for(w)[1] and w.id != cur]
        if not pool:
            self._status("nothing else to shuffle to")
            return
        wp = random.choice(pool)
        self._pick(wp)
        if wp.id in self.cards:
            self.grid.ensureWidgetVisible(self.cards[wp.id])
        self._apply(wp.id)

    def _theme(self):
        wid = self.cfg.get("current") or self.selected
        if not wid:
            return
        opts = config.opts(self.cfg)
        backend = self.cfg.get("theme_backend", "builtin")
        self._status("rendering a frame and reading its colours…")
        # linux-wallpaperengine's --screenshot mode can take up to a minute
        # (and on some compositors just hangs for it) — run it off the GUI
        # thread so a slow renderer freezes a status line, not the window.
        self.jobs.start(lambda: engine.sync_theme(wid, opts, backend),
                        self._theme_done, self._theme_failed, tag=wid)

    def _theme_done(self, tag, result):
        ok, msg = result
        if ok and self.cfg.get("ui_follow_wallpaper"):
            self._restyle()
        self._status(msg)

    def _theme_failed(self, tag, message):
        self._status(f"theme sync failed: {message}")

    def _apply_props(self, wid, props):
        store = self.cfg.setdefault("properties", {})
        if props:
            store[wid] = props
        else:
            store.pop(wid, None)
        config.save(self.cfg)
        self.selected = wid
        self._apply(wid)

    def _show(self, page):
        on_browser = page is self.browser
        self.tab_lib.setChecked(not on_browser)
        self.tab_web.setChecked(on_browser)
        self.chip_row.setVisible(not on_browser)
        self.sort.setVisible(not on_browser)
        self.search.setVisible(not on_browser)
        self.dock.setVisible(not on_browser)
        if on_browser:
            self.stack.setCurrentWidget(self.browser)
            if not self.browser.rows:
                self.browser.search(reset=True)
            self.browser.query.setFocus()
        else:
            self._build_grid()

    def _toggle_browse(self):
        self._show(self.grid if self.stack.currentWidget() is self.browser else self.browser)

    def _focus_search(self):
        if self.stack.currentWidget() is self.browser:
            self.browser.query.setFocus()
        else:
            self.search.setFocus()
            self.search.selectAll()

    def _escape(self):
        if self.search.hasFocus() and self.search.text():
            self.search.clear()
        elif self.stack.currentWidget() is self.browser:
            self._show(self.grid)
        else:
            self.grid.setFocus()

    def _refresh(self):
        self.lib = engine.scan_library()
        self._build_grid()
        self._refresh_hero()

    def _settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            self.cfg.update(d.values())
            config.save(self.cfg)
            engine.forget_tools()          # the desktop/host choice may have changed
            self.browser.cfg = self.cfg
            self._restyle()
            self._build_grid()

    def _docs(self):
        DocDialog(self).exec()

    def _status(self, m):
        self.statusBar().showMessage(str(m), 12000)

    def closeEvent(self, e):
        self._poll.stop()
        self.jobs.wait()
        super().closeEvent(e)


def _mtime(folder) -> float:
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0.0


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
