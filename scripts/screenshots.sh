#!/usr/bin/env bash
# Regenerate docs/screenshots/ from the generated demo library, off-screen:
# Qt's offscreen platform for the GUI, a detached tmux pane for lazypaper.
# Nothing here touches your desktop, your library or your config.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$HERE/docs/screenshots"
DEMO="$(mktemp -d)"
trap 'tmux kill-session -t fp-shot 2>/dev/null || true; rm -rf "$DEMO"' EXIT
mkdir -p "$OUT"
python3 "$HERE/scripts/demo_library.py" "$DEMO" >/dev/null

ENV=(env -i HOME="$DEMO/home" PATH=/usr/bin:/bin LANG=en_US.UTF-8 TERM=xterm-256color
     FOSSYPAPER_LIBRARY="$DEMO/lib" FOSSYPAPER_HOST=layer XDG_CURRENT_DESKTOP=Hyprland
     HYPRLAND_INSTANCE_SIGNATURE=demo PYTHONPATH="$HERE")

"${ENV[@]}" QT_QPA_PLATFORM=offscreen python3 - "$OUT/fossypaper.png" <<'PY'
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv)
from fossypaper import app as fp
w = fp.MainWindow(); w.resize(1480, 900); w.show()
QTimer.singleShot(2500, lambda: (w.grab().save(sys.argv[1]), app.quit()))
app.exec()
PY

tmux new-session -d -s fp-shot -x 140 -y 38 "${ENV[*]} python3 -m fossypaper tui"
sleep 3
tmux send-keys -t fp-shot j j
sleep 1
tmux capture-pane -e -p -t fp-shot > "$DEMO/tui.ansi"
python3 "$HERE/scripts/ansi_to_png.py" "$DEMO/tui.ansi" "$OUT/lazypaper.png"
echo "wrote $OUT/fossypaper.png $OUT/lazypaper.png"
