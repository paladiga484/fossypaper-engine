# fossypaper-engine

A wallpaper manager for Linux/Wayland that renders **Wallpaper Engine** scenes,
video and stills — with a browser, per-wallpaper controls, and no Windows in
sight.

- **`fossypaper`** — the desktop app: thumbnail library, a live property editor
  built from each wallpaper's own schema, a built-in Wallhaven + Steam Workshop
  browser, flat themes you can change.
- **`lazypaper`** — the same thing in the terminal, in your terminal's colours.
- **`fossypaper <cmd>`** — the CLI everything else is built on. `--json` on
  every listing.
- **Noctalia plugin** — bar widget, library panel, control-centre tile and a
  `/wp` launcher provider. See `noctalia-plugin/fossypaper/README.md`.

```
./install.sh
fossypaper doctor
```

Needs `PySide6`, `Pillow`, `linux-wallpaperengine`, and `mpvpaper` for video.
`swww`, `steamcmd` and `ffmpeg` are optional; `doctor` says what each buys you.

---

## What it renders

| Wallpaper Engine type | Rendered by | Notes |
| --- | --- | --- |
| `scene` (`scene.pkg`, `gifscene.pkg`) | `linux-wallpaperengine` | including scenes with video layers |
| `video` | `mpvpaper` | hardware decode, paused behind fullscreen apps |
| `image` | `swww`, or `mpvpaper` if you don't have it | also what Wallhaven downloads become |
| `web` | nothing, on purpose | it would need a local web server; see below |
| `application` | nothing | it's a Windows executable |

The type comes from each wallpaper's `project.json`, along with the file the
renderer should actually open — so `gifscene.pkg`, video files with spaces in
their names, and libraries that aren't Steam's all work without special cases.

Libraries are searched in this order, and the first one to define an id wins:
Steam's workshop dir, the Flatpak Steam equivalent, and fossypaper's own
downloads. Set `FOSSYPAPER_LIBRARY` (colon-separated, like `PATH`) to replace
that list entirely.

## Per-wallpaper properties

Wallpaper Engine wallpapers ship their own settings schema — a 4K terminal
wallpaper in this library has 77 of them. fossypaper reads that schema straight
out of `project.json`: types, ranges, combo labels, defaults, and the
`condition` expressions that decide when a knob is even relevant. Knobs whose
condition isn't met are hidden, the way the original hides them.

```
fossypaper props 3634342477            # what this one exposes
fossypaper props 3634342477 --all      # including the hidden and the decorative
fossypaper set-prop 3634342477 theme=4 efeitovhs=true
```

The GUI dock and the TUI's `p` view are the same data with widgets on it.

## Audio

`--silent` mutes a wallpaper's output. It does **not** stop audio *processing* —
the renderer keeps a capture stream open on your sink monitor, and those
accumulate across reapplies until something latency-sensitive starts to
struggle.

So `audio_processing` defaults to `auto`: capture only for wallpapers whose own
manifest declares `supportsaudioprocessing`. In a library of forty, that was
eighteen. `always` restores the original behaviour, `never` disables it outright.

## Games

A wallpaper carries on rendering behind a fullscreen game unless something stops
it. `fullscreen_pause` is on by default for both backends. Loosen it per app:

```
fossypaper config --set fullscreen_pause=true pause_only_active=true
fossypaper config --set pause_ignore_appids=mpv,vlc
```

## The browser

`Browse` in the app, `b` in the TUI, or:

```
fossypaper browse wallhaven "eldritch"
fossypaper browse workshop "cosmic horror"
fossypaper get 2500458873          # a Workshop id
fossypaper get wqokqq              # a Wallhaven id
```

Wallhaven stills are downloaded straight into your library. Workshop items are
Steam's to serve: with `steamcmd` installed fossypaper fetches them, and without
it hands the item to Steam so you can subscribe there. Wallhaven matches tags,
so one or two words beat a sentence.

## Colours

`fossypaper theme` renders a frame of the current wallpaper, clusters its
pixels, and writes the palette to `~/.local/state/fossypaper/colors.{json,sh,css}`
in a pywal-compatible shape. `matugen`, `wallust` and `wal` are used instead
when you have them and ask for them. The GUI can follow the same palette
(Settings → Appearance).

## Multiple screens

`output` blank means every connected screen. `span` stretches one wallpaper
across several. Output names come from the compositor — `hyprctl`, `niri`, then
`wlr-randr`, then DRM — because a MUX switch can rename the panel and
`--screen-root` only accepts the name the compositor is using.

## Privacy

fossypaper opens **no listening socket**: no port, no local server, no helper
daemon. The Noctalia plugin talks to it by running `fossypaper --json <cmd>` and
reading stdout, and that is the only mechanism offered. That is also why `web`
wallpapers are unsupported rather than faked.

Outbound requests happen only when you use the browser, and only to the five
hosts named in [`docs/PRIVACY.md`](docs/PRIVACY.md) — which lists exactly what
is sent to each. `fossypaper privacy` prints it; `--terms` prints
[`docs/TERMS.md`](docs/TERMS.md).

## Commands

```
fossypaper list [--json] [--usable-only] [--type T] [--search Q] [--thumbs]
fossypaper apply <id> · off · toggle · status
fossypaper props <id> [--all] · set-prop <id> KEY=VALUE...
fossypaper browse wallhaven|workshop [query] [--page N] [--sort S]
fossypaper get <id>
fossypaper theme [id] [--backend builtin|matugen|wallust|wal]
fossypaper config [--set KEY=VAL...] [--reset]
fossypaper autostart on|off · daemon · sddm [id]
fossypaper doctor · privacy [--terms]
fossypaper gui · tui
```

## Tests

```
python3 -m unittest discover -s tests
```

They run headless, and the last few run the scanner and the argv builder over
whatever library the machine actually has.

## Not affiliated with Wallpaper Engine

Wallpaper Engine is a commercial product by Kristjan Skutta. fossypaper is an
independent program that renders some of the same content through
`linux-wallpaperengine`. MIT licensed; see `LICENSE`.
