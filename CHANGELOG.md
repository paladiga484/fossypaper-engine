# Changelog

## 0.3.0 — 2026-09-26

The "be the wallpaper, not a window over it" release.

### Every desktop
- **KDE Plasma**: a registered Plasma wallpaper type (`org.fossypaper.wallpaper`),
  set over plasmashell's own D-Bus call. Videos and stills play natively; scenes
  run live through the native `SceneViewer` when `wallpaper-engine-kde-plugin-git`
  is installed, and fall back to a still frame otherwise. Pauses behind
  fullscreen windows. `fossypaper off` puts your previous wallpaper back.
- **GNOME**: stills through gsettings, videos through the Hanabi extension when
  it's installed; the previous background is restored on `off`.
- **X11 window managers**: the root window (xwallpaper/feh, xwinwrap + mpv).
- **Layer-shell** (Hyprland, niri, sway…): `layer: auto` uses the real background
  layer, or steps over a shell's own backdrop (Noctalia 5, iNiR/Quickshell,
  swaybg, hyprpaper) instead of fighting it.

### Fixes
- Scenes with effect chains no longer smear into "infinite layers" on Plasma: the
  renderer's static-pass cache fed each frame's effects the previous frame.
- Scenes that would deadlock the Plasma renderer are caught in an off-screen
  preflight first, instead of freezing the desktop.
- A scene's video layer plays on Plasma and GNOME, and video-to-video switches no
  longer go black.
- lazypaper no longer forks six `pgrep`s per redraw, lines up rows with emoji in
  their titles, and opens from any launcher in its own window (app-id
  `lazypaper`).
- Nothing pokes plasmashell from a non-Plasma session.

### Project
- The engine is a package of small modules instead of one 1,700-line file.
- Arch PKGBUILD in `packaging/arch`, CI on Python 3.10/3.12/3.14, issue templates,
  reproducible screenshots from a generated demo library.
