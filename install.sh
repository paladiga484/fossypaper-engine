#!/usr/bin/env bash
# Install fossypaper-engine for the current user:
#   launchers on PATH · desktop entries + icons · the Noctalia plugin ·
#   the Plasma wallpaper type.
# Everything lands under $HOME. Nothing here needs root, and nothing is
# installed system-wide.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
NOCT="${XDG_DATA_HOME:-$HOME/.local/share}/noctalia/plugins"
PLASMA="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/wallpapers"

mkdir -p "$BIN" "$APPS" "$ICONS"

# ---- launchers ------------------------------------------------------------ #
cat > "$BIN/fossypaper" <<LAUNCH
#!/usr/bin/env bash
export PYTHONPATH="$HERE\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m fossypaper "\$@"
LAUNCH
cat > "$BIN/lazypaper" <<LAUNCH
#!/usr/bin/env bash
export PYTHONPATH="$HERE\${PYTHONPATH:+:\$PYTHONPATH}"
exec python3 -m fossypaper tui "\$@"
LAUNCH
chmod +x "$BIN/fossypaper" "$BIN/lazypaper"

# ---- launcher entries ----------------------------------------------------- #
install -m644 "$HERE/data/fossypaper.desktop" "$APPS/fossypaper.desktop"
install -m644 "$HERE/data/lazypaper.desktop"  "$APPS/lazypaper.desktop"
install -m644 "$HERE/data/fossypaper.svg"     "$ICONS/fossypaper.svg"
install -m644 "$HERE/data/lazypaper.svg"      "$ICONS/lazypaper.svg"
# the pre-0.2 entry, if it's still sitting there
rm -f "$APPS/fossypaper-engine.desktop" "$ICONS/fossypaper-engine.svg"

command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
command -v gtk-update-icon-cache >/dev/null &&
  gtk-update-icon-cache -qtf "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

# ---- Noctalia plugin ------------------------------------------------------ #
if command -v noctalia >/dev/null; then
  mkdir -p "$NOCT"
  rm -rf "$NOCT/fossypaper"
  cp -r "$HERE/noctalia-plugin/fossypaper" "$NOCT/fossypaper"
  noctalia msg plugins enable friend/fossypaper >/dev/null 2>&1 || true
  echo "  noctalia   plugin installed -> $NOCT/fossypaper  (friend/fossypaper)"
fi

# ---- Plasma wallpaper type -------------------------------------------------- #
# Plain files: plasmashell finds wallpaper packages here on its own. Installed
# whether or not you're in Plasma right now, so it's there when you log in.
mkdir -p "$PLASMA"
rm -rf "$PLASMA/org.fossypaper.wallpaper"
cp -r "$HERE/plasma-wallpaper/org.fossypaper.wallpaper" "$PLASMA/org.fossypaper.wallpaper"
echo "  plasma     wallpaper type installed -> $PLASMA/org.fossypaper.wallpaper"
echo "             (updating it? plasmashell caches QML: systemctl --user restart plasma-plasmashell)"

echo "installed:"
echo "  commands   $BIN/fossypaper · $BIN/lazypaper"
echo "  launcher   $APPS/{fossypaper,lazypaper}.desktop"
echo
echo "next:  fossypaper doctor      # what's present and what it costs you"
echo "       fossypaper             # the app"
echo "       lazypaper              # the terminal side"
