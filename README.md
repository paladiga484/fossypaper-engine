# fossypaper-engine

A FOSS **Wallpaper Engine** manager for Linux/Wayland — three frontends on one
engine, and more control than the original.

- **`fossypaper`** — Qt GUI: thumbnail library, one-click apply, a live
  per-wallpaper **property editor** (sliders / colour pickers from the
  wallpaper's own schema), settings, and Sync-Theme.
- **`lazypaper`** — a LazyVim-style **TUI**.
- **`fossypaper <cmd>`** — a scriptable **CLI** (`list · apply · off · status ·
  props · theme · download · config`).

## Features
- Renders WE **scene** wallpapers via `linux-wallpaperengine` (not the Windows app).
- Flags **video-backed scenes** that don't composite on NVIDIA+wlroots.
- Bakes in the NVIDIA **red-brick + vsync/tear** fix (`--gpu auto`).
- Every render knob: **fps · scaling · layer · particles/parallax/mouse · audio**.
- **Theme sync** — extracts a palette from the wallpaper's own **pixels**
  (built-in, no matugen needed) → `~/.local/state/fossypaper/colors.{json,sh,css}`.
- **Not tied to Steam** — set `FOSSYPAPER_LIBRARY`, or pull from the Workshop web
  via `fossypaper download <id>` (steamcmd).

## Install
```
./install.sh          # launchers + .desktop + icon
# or:  pipx install .
```
Deps: `PySide6`, `Pillow`, and `linux-wallpaperengine`.

## Usage
```
fossypaper                 # GUI
lazypaper                  # TUI
fossypaper list --gl-only
fossypaper apply 2500458873
fossypaper theme           # palette from the current wallpaper's pixels
fossypaper config --set fps=60 gpu=auto
```
