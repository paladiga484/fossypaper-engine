#!/usr/bin/env bash
# Install fossypaper-engine: launchers on PATH + a .desktop entry + icon.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$BIN" "$APPS" "$ICONS"

for f in fossypaper lazypaper; do
  cat > "$BIN/$f" <<LAUNCH
#!/usr/bin/env bash
export PYTHONPATH="$HERE\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m fossypaper $([ "$f" = lazypaper ] && echo tui) "\$@"
LAUNCH
  chmod +x "$BIN/$f"
done
install -m644 "$HERE/data/fossypaper-engine.desktop" "$APPS/"
install -m644 "$HERE/data/fossypaper-engine.svg" "$ICONS/"
echo "✓ installed: $BIN/{fossypaper,lazypaper}  ·  $APPS/fossypaper-engine.desktop"
echo "  run:  fossypaper (GUI) · lazypaper (TUI) · fossypaper --help (CLI)"
echo "  deps: PySide6 + Pillow (pacman -S pyside6 python-pillow)  — you already have them."
