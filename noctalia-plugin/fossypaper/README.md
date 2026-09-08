# fossypaper for Noctalia

Browse and apply your Wallpaper Engine library from Noctalia — a bar widget, a
library panel, a control-center tile, and a launcher provider.

| Field | Value |
| --- | --- |
| ID | `friend/fossypaper` |
| Entries | widget `status` · panel `library` · shortcut `toggle` · launcher `find` |
| Launcher prefix | `/wp` |
| Requires | the `fossypaper` CLI on `PATH` (fossypaper-engine) |

## Install

```sh
./install.sh          # from the fossypaper-engine checkout; links the plugin in
noctalia msg plugins enable friend/fossypaper
```

Or by hand: copy this directory to `~/.local/share/noctalia/plugins/fossypaper/`.

## Use

- Add the **fossypaper** widget from the Add-widget picker. Left click opens the
  library, right click turns the wallpaper off and on.
- `noctalia msg panel-toggle friend/fossypaper:library`
- Type `/wp` in the launcher and start typing a name.
- Add the **Wallpaper** tile under Settings → Control Center shortcuts.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `binary` | `fossypaper` | name or path of the CLI |
| `sync_theme` | off | read a palette from the wallpaper's pixels after applying |
| `release_noctalia_wallpaper` | on | switch off Noctalia's own wallpaper surface so the two don't stack |
| `show_thumbnails` | on | bake a still of each preview and show it |

## How it talks to fossypaper

`fossypaper --json <command>` on argv, run off-thread, answered on stdout.
There is no socket, no port and no helper daemon — see `docs/PRIVACY.md` in the
fossypaper-engine repo.
