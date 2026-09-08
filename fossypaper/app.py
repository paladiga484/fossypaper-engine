"""fossypaper GUI — a PySide6 system app.

Thumbnail library, one-click apply, a settings dialog for every render knob, a
live per-wallpaper property editor (sliders / colour pickers built from the
wallpaper's own schema), and Sync-Theme that pulls a palette from the pixels.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap, QColor, QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QScrollArea, QGridLayout, QVBoxLayout,
    QHBoxLayout, QLabel, QFrame, QToolBar, QDialog, QFormLayout, QSpinBox,
    QComboBox, QCheckBox, QSlider, QLineEdit, QPushButton, QColorDialog,
    QDockWidget, QDoubleSpinBox, QStatusBar, QSizePolicy,
)

from . import engine, config

ACCENT = "#c8a24b"
STYLE = f"""
QMainWindow, QWidget {{ background:#14110d; color:#efe6d0; font-family:'Iowan Old Style',Palatino,serif; }}
QToolBar {{ background:#1b1712; border-bottom:2px solid {ACCENT}; spacing:8px; padding:6px; }}
QPushButton {{ background:#2a2015; color:#efe6d0; border:1px solid {ACCENT}; padding:6px 12px; }}
QPushButton:hover {{ background:#3a2d17; }}
QFrame#card {{ background:#1b1712; border:1px solid #3a2f1e; }}
QFrame#card:hover {{ border:1px solid {ACCENT}; }}
QFrame#card[selected="true"] {{ border:2px solid #e9cf8a; }}
QLabel#badgeGL {{ color:#5fa06b; }}
QLabel#badgeVID {{ color:#d9a441; }}
QDockWidget::title {{ background:#1b1712; padding:6px; }}
"""


class Card(QFrame):
    clicked = Signal(object)

    def __init__(self, wp: engine.Wallpaper):
        super().__init__()
        self.wp = wp
        self.setObjectName("card")
        self.setFixedSize(224, 168)
        self.setProperty("selected", False)
        v = QVBoxLayout(self); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(4)
        thumb = QLabel(); thumb.setFixedHeight(118); thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet("background:#000;")
        if wp.preview and wp.preview.is_file():
            pm = QPixmap(str(wp.preview))
            if not pm.isNull():
                thumb.setPixmap(pm.scaled(210, 118, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        v.addWidget(thumb)
        row = QHBoxLayout()
        t = QLabel(wp.title); t.setWordWrap(False); t.setStyleSheet("font-size:12px;")
        t.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        be, ok = engine.backend_of(wp.id)
        lbl = {"wpe": "WPE", "mpvpaper": "MPV", "swww": "IMG"}[be]
        solid = ok and be in ("mpvpaper", "swww")   # video/image = reliable; scene = best-effort
        badge = QLabel(lbl if ok else lbl + "!")
        badge.setObjectName("badgeGL" if solid else "badgeVID")
        badge.setToolTip({"wpe": "WE scene (linux-wallpaperengine, best-effort)",
                          "mpvpaper": "video via mpvpaper", "swww": "image via swww"}[be]
                         + ("" if ok else " — tool not installed"))
        row.addWidget(t, 1); row.addWidget(badge)
        v.addLayout(row)

    def mousePressEvent(self, e):
        self.clicked.emit(self.wp)

    def set_selected(self, on: bool):
        self.setProperty("selected", on); self.style().unpolish(self); self.style().polish(self)


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent); self.setWindowTitle("fossypaper — settings"); self.cfg = cfg
        f = QFormLayout(self)
        self.output = QComboBox(); self.output.addItems(engine.outputs() or ["eDP-1"])
        self.output.setCurrentText(cfg.get("output", "eDP-1"))
        self.layer = QComboBox(); self.layer.addItems(["bottom", "background", "top"]); self.layer.setCurrentText(cfg.get("layer", "bottom"))
        self.fps = QSpinBox(); self.fps.setRange(1, 240); self.fps.setValue(cfg.get("fps", 30))
        self.gpu = QComboBox(); self.gpu.addItems(["auto", "nvidia", "mesa"]); self.gpu.setCurrentText(cfg.get("gpu", "auto"))
        self.scaling = QComboBox(); self.scaling.addItems(["", "default", "stretch", "fit", "fill"]); self.scaling.setCurrentText(cfg.get("scaling", ""))
        self.silent = QCheckBox(); self.silent.setChecked(cfg.get("silent", True))
        self.nop = QCheckBox(); self.nop.setChecked(cfg.get("no_particles", False))
        self.nopar = QCheckBox(); self.nopar.setChecked(cfg.get("no_parallax", False))
        self.nom = QCheckBox(); self.nom.setChecked(cfg.get("no_mouse", False))
        for lbl, w in [("Output", self.output), ("Layer", self.layer), ("FPS", self.fps),
                       ("GPU / EGL", self.gpu), ("Scaling", self.scaling), ("Silent", self.silent),
                       ("No particles", self.nop), ("No parallax", self.nopar), ("No mouse", self.nom)]:
            f.addRow(lbl, w)
        ok = QPushButton("Save"); ok.clicked.connect(self.accept); f.addRow(ok)

    def values(self):
        return {"output": self.output.currentText(), "layer": self.layer.currentText(),
                "fps": self.fps.value(), "gpu": self.gpu.currentText(),
                "scaling": self.scaling.currentText(), "silent": self.silent.isChecked(),
                "no_particles": self.nop.isChecked(), "no_parallax": self.nopar.isChecked(),
                "no_mouse": self.nom.isChecked()}


class PropertyDock(QDockWidget):
    def __init__(self, apply_cb, parent=None):
        super().__init__("Properties", parent); self.apply_cb = apply_cb
        self.body = QWidget(); self.form = QFormLayout(self.body); self.setWidget(self.body)
        self.widgets = {}; self.wid = None

    def load(self, wid):
        self.wid = wid
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets = {}
        props = engine.list_properties(wid)
        if not props:
            self.form.addRow(QLabel("no customizable properties")); return
        for p in props:
            w = self._widget(p)
            if w:
                self.widgets[p.key] = (p, w); self.form.addRow(p.text or p.key, w)
        ap = QPushButton("Apply properties"); ap.clicked.connect(self._apply); self.form.addRow(ap)

    def _widget(self, p):
        if p.kind == "boolean":
            c = QCheckBox(); c.setChecked(p.value.strip() in ("1", "true")); return c
        if p.kind == "slider":
            s = QDoubleSpinBox(); s.setRange(p.mn, p.mx); s.setSingleStep(p.step or 0.01)
            try: s.setValue(float(p.value))
            except ValueError: pass
            return s
        if p.kind == "color":
            btn = QPushButton("pick"); btn._rgba = p.value
            def pick(_=None, b=btn):
                parts = [float(x) for x in (b._rgba.split(",") + ["0", "0", "0"])[:3]]
                col = QColorDialog.getColor(QColor.fromRgbF(*parts), self)
                if col.isValid():
                    b._rgba = f"{col.redF():.6f} {col.greenF():.6f} {col.blueF():.6f} 1.0"
                    b.setStyleSheet(f"background:{col.name()};")
            btn.clicked.connect(pick)
            try:
                r, g, bl = [float(x) for x in p.value.split(",")[:3]]
                btn.setStyleSheet(f"background:{QColor.fromRgbF(r, g, bl).name()};")
            except (ValueError, IndexError):
                pass
            return btn
        if p.kind in ("textinput",):
            e = QLineEdit(p.value); return e
        if p.kind == "combo":
            c = QComboBox(); c.addItems(p.options or []); c.setCurrentText(p.value); return c
        return None

    def _apply(self):
        props = {}
        for key, (p, w) in self.widgets.items():
            if isinstance(w, QCheckBox): props[key] = "1" if w.isChecked() else "0"
            elif isinstance(w, QDoubleSpinBox): props[key] = str(w.value())
            elif isinstance(w, QLineEdit): props[key] = w.text()
            elif isinstance(w, QComboBox): props[key] = w.currentText()
            elif isinstance(w, QPushButton): props[key] = getattr(w, "_rgba", "")
        self.apply_cb(self.wid, props)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fossypaper-engine")
        self.resize(1000, 720)
        self.cfg = config.load()
        self.setStyleSheet(STYLE)
        self.lib = engine.scan_library()
        self.cards = {}
        self.selected = self.cfg.get("current", "")

        tb = QToolBar(); self.addToolBar(tb)
        for name, fn in [("On", self._apply_current), ("Off", lambda: (engine.stop(), self._status("off"))),
                         ("Sync theme", self._theme), ("Settings", self._settings), ("Refresh", self._refresh)]:
            a = QAction(name, self); a.triggered.connect(fn); tb.addAction(a)
        self.hidevid = QCheckBox("hide video"); self.hidevid.stateChanged.connect(self._refresh); tb.addWidget(self.hidevid)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.setCentralWidget(self.scroll)
        self.dock = PropertyDock(self._apply_props, self); self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.setStatusBar(QStatusBar())
        self._build_grid()
        if self.selected:
            self.dock.load(self.selected)

    def _build_grid(self):
        host = QWidget(); grid = QGridLayout(host); grid.setSpacing(12)
        self.cards = {}
        col_n = max(1, (self.width() - 260) // 236)
        r = c = 0
        for wp in self.lib:
            if self.hidevid.isChecked() and wp.video:
                continue
            card = Card(wp); card.clicked.connect(self._pick)
            card.set_selected(wp.id == self.selected)
            self.cards[wp.id] = card
            grid.addWidget(card, r, c); c += 1
            if c >= col_n: c = 0; r += 1
        grid.setRowStretch(r + 1, 1)
        self.scroll.setWidget(host)
        self._status(f"{len(self.cards)} wallpapers · green=GL ✓  amber=video (breaks on NVIDIA+wlroots)")

    def _pick(self, wp):
        for cid, card in self.cards.items():
            card.set_selected(cid == wp.id)
        self.selected = wp.id
        self.dock.load(wp.id)
        self._apply_current()

    def _apply_current(self):
        if not self.selected:
            return
        wp = next((w for w in self.lib if w.id == self.selected), None)
        be, ok = engine.backend_of(self.selected)
        o = config.opts(self.cfg); o["properties"] = self.cfg.get("properties", {}).get(self.selected, {})
        engine.start(self.selected, o)
        self.cfg["current"] = self.selected; config.save(self.cfg)
        msg = (f"⚠ needs {be} (paru -S {be}) · " if not ok else "applied ✓ ") + (wp.title if wp else "")
        self._status(msg)

    def _apply_props(self, wid, props):
        self.cfg.setdefault("properties", {})[wid] = props; config.save(self.cfg)
        self.selected = wid; self._apply_current(); self._status("properties applied ✓")

    def _theme(self):
        if not self.selected:
            return
        self._status("rendering a frame + extracting colours…"); QApplication.processEvents()
        ok, msg = engine.sync_theme(self.selected, config.opts(self.cfg), self.cfg.get("theme_backend", "builtin"))
        self._status(msg)

    def _settings(self):
        d = SettingsDialog(self.cfg, self)
        if d.exec():
            self.cfg.update(d.values()); config.save(self.cfg)
            if self.selected:
                self._apply_current()

    def _refresh(self):
        self.lib = engine.scan_library(); self._build_grid()

    def _status(self, m):
        self.statusBar().showMessage(m)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("fossypaper-engine")
    if not engine.WE_DIR.is_dir():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "fossypaper", f"No wallpaper library at {engine.WE_DIR}.\n"
                            "Set FOSSYPAPER_LIBRARY or subscribe to WE scenes.")
        return 1
    w = MainWindow(); w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
