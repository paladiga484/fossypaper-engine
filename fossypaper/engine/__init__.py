"""fossypaper.engine — the backend. No Qt, no curses, so it stays testable alone.

What it knows how to do:

  * **Find wallpapers** across every library root you have — Steam's Workshop
    dir, fossypaper's own downloads, and anything you point
    `FOSSYPAPER_LIBRARY` at — and read each one's `project.json` for what it
    actually is, instead of guessing from filenames.
  * **Route each wallpaper to a renderer that can actually draw it**: WE scenes
    to `linux-wallpaperengine`, video to `mpvpaper`, stills to `swww` when you
    have it and `mpvpaper` when you don't.
  * **Be the wallpaper, not a window over it**, on whatever desktop you're on:
    a layer-shell surface on wlroots/Hyprland/niri, a registered wallpaper
    type on Plasma, gsettings (and Hanabi, if present) on GNOME, the root
    window on a bare X11 window manager. See `host()`.
  * **Drive the renderer with every knob it exposes** — per-output backgrounds,
    spanning, scaling/clamp, layer, fps, GPU/EGL selection, audio, effects, and
    the fullscreen-pause rules that keep a wallpaper from eating a game's frames.
  * **Sync a colour theme** from the wallpaper's own pixels.

The engine is a package, and this module re-exports its whole surface so
`engine.start(...)` and friends keep working. Each name lives in exactly one
submodule, and submodules call each other as `module.name` -- so a test
patches the submodule that defines a name, and every caller sees it.
"""
from __future__ import annotations

from . import tools, library, session, plasma, gnome, x11, render, media, colours, service, selftest

from .tools import (
    STATE, _WHICH, which, BIN, have_renderer,
)
from .library import (
    STEAM_WORKSHOP, FLATPAK_WORKSHOP, OWN_LIBRARY, library_roots, _DirProxy, WE_DIR,
    Property, Wallpaper, _PREVIEW_GLOB, _preview, _VIDEO_EXT, _IMAGE_EXT, _entry_file,
    _read_project, _dependency_folder, _preset_overrides, read_wallpaper, _infer_type,
    scan_library, find, list_properties, kind_of, SCENE_VIDEOS, _pkg_entries,
    scene_has_effects, scene_video, _NOTICE_OFF, _NOTICE_ON, quiet_overrides,
    effective_properties,
)
from .session import (
    gpu_env, _dgpu, outputs, _outputs_hypr, _outputs_niri, _outputs_kde, compositor,
    FULLSCREEN_BLIND, fullscreen_blind, HOSTS, _HOST, host, detect_host,
    _BACKDROP_SHELLS, _BACKDROP_TTL, _backdrop_cache, _running_comms, backdrop_shell,
    resolved_layer, assets_dir, _outputs_wlr, _outputs_drm, target_outputs, missing_output,
)
from .plasma import (
    PLASMA_PLUGIN, _PLASMA_PREV, _SCENE_MODULE, plasma_plugin_installed,
    plasma_scene_module, _PREFLIGHT, _PREFLIGHT_QML, plasma_preflight, _qdbus,
    plasma_eval, plasma_config, _typed, plasma_script, _start_plasma, _bus_has,
    _plasma_restore,
)
from .gnome import (
    _GNOME_PREV, _GNOME_BG, _HANABI, _gsettings, _gv, hanabi_available,
    _gnome_save_previous, _gnome_set_picture, _start_gnome, _gnome_restore,
)
from .x11 import (
    _X11_PID, _x11_still_tool, _x11_can, _x11_needs, _start_x11_still, _start_x11_video,
    _x11_video_alive, _x11_video_stop,
)
from .render import (
    _MANAGED, _UNSUPPORTED, backend_of, backend_for, _layer_backend_for, wants_audio,
    why_unsupported, render_note, backends_status, is_running, stop, build_argv,
    _per_output, start, RENDER_LOG, _spawned, _spawn, _died_early, render_log_tail,
    _start_wpe, _start_mpvpaper, _start_swww,
)
from .media import (
    _still_for, screenshot, _render_one_frame, THUMBS, thumbnail, clear_thumbnails,
    _still_from_image,
)
from .colours import (
    theme_backends, sync_theme, extract_palette, _lum, _sat, derive_roles,
    write_palette_files, palette,
)
from .service import (
    SERVICE, ensure_wayland_env, apply_current, install_service, remove_service,
    _SDDM_DEST, sddm_prepare,
)


def forget_tools() -> None:
    """Drop the cached tool lookups and host choice -- after installing
    something, or changing the desktop setting, mid-session."""
    tools._WHICH.clear()
    session._HOST.clear()


__all__ = [
    'selftest', 'tools', 'library', 'session', 'plasma', 'gnome', 'x11', 'render', 'media',
    'colours', 'service', 'STATE', '_WHICH', 'which', 'BIN', 'have_renderer',
    'STEAM_WORKSHOP', 'FLATPAK_WORKSHOP', 'OWN_LIBRARY', 'library_roots', '_DirProxy',
    'WE_DIR', 'Property', 'Wallpaper', '_PREVIEW_GLOB', '_preview', '_VIDEO_EXT',
    '_IMAGE_EXT', '_entry_file', '_read_project', '_dependency_folder',
    '_preset_overrides', 'read_wallpaper', '_infer_type', 'scan_library', 'find',
    'list_properties', 'kind_of', 'SCENE_VIDEOS', '_pkg_entries', 'scene_has_effects',
    'scene_video', '_NOTICE_OFF', '_NOTICE_ON', 'quiet_overrides',
    'effective_properties', 'gpu_env', '_dgpu', 'outputs', '_outputs_hypr',
    '_outputs_niri', '_outputs_kde', 'compositor', 'FULLSCREEN_BLIND',
    'fullscreen_blind', 'HOSTS', '_HOST', 'host', 'detect_host', '_BACKDROP_SHELLS',
    '_BACKDROP_TTL', '_backdrop_cache', '_running_comms', 'backdrop_shell',
    'resolved_layer', 'assets_dir', '_outputs_wlr', '_outputs_drm', 'target_outputs', 'missing_output',
    'PLASMA_PLUGIN', '_PLASMA_PREV', '_SCENE_MODULE', 'plasma_plugin_installed',
    'plasma_scene_module', '_PREFLIGHT', '_PREFLIGHT_QML', 'plasma_preflight', '_qdbus',
    'plasma_eval', 'plasma_config', '_typed', 'plasma_script', '_start_plasma',
    '_bus_has', '_plasma_restore', '_GNOME_PREV', '_GNOME_BG', '_HANABI', '_gsettings',
    '_gv', 'hanabi_available', '_gnome_save_previous', '_gnome_set_picture',
    '_start_gnome', '_gnome_restore', '_X11_PID', '_x11_still_tool', '_x11_can',
    '_x11_needs', '_start_x11_still', '_start_x11_video', '_x11_video_alive',
    '_x11_video_stop', '_MANAGED', '_UNSUPPORTED', 'backend_of', 'backend_for',
    '_layer_backend_for', 'wants_audio', 'why_unsupported', 'render_note',
    'backends_status', 'is_running', 'stop', 'build_argv', '_per_output', 'start',
    'RENDER_LOG', '_spawned', '_spawn', '_died_early', 'render_log_tail', '_start_wpe',
    '_start_mpvpaper', '_start_swww', '_still_for', 'screenshot', '_render_one_frame',
    'THUMBS', 'thumbnail', 'clear_thumbnails', '_still_from_image', 'theme_backends',
    'sync_theme', 'extract_palette', '_lum', '_sat', 'derive_roles',
    'write_palette_files', 'palette', 'SERVICE', 'ensure_wayland_env', 'apply_current',
    'install_service', 'remove_service', '_SDDM_DEST', 'sddm_prepare', 'forget_tools',
]
