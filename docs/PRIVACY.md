# fossypaper — privacy

Last updated: 2026-09-08 · applies to fossypaper-engine 0.2.0 and its Noctalia plugin.

fossypaper is a local program. It has no account, no telemetry, no analytics, no
crash reporter, and no update check. Nobody operates a fossypaper service,
because there isn't one.

This document is specific on purpose. "We respect your privacy" is not a fact
about a program; the list below is.

---

## Nothing listens

fossypaper **never opens a listening socket**. It does not bind a port, does not
run a helper daemon, does not start a local web server, and does not expose an
HTTP, WebSocket or D-Bus service. There is no `localhost` endpoint to find, so
nothing on your machine — and nothing on your network — can reach fossypaper
over a socket.

Everything that wants to drive fossypaper does it by running the `fossypaper`
command and reading its standard output. That is how the Noctalia plugin works,
and it is the only mechanism offered.

The one consequence worth stating: **web-type Wallpaper Engine wallpapers are
not supported**. Rendering one would mean serving its HTML from a local server.
fossypaper refuses rather than opening a port, and says so in the interface.

## What leaves the machine, and when

fossypaper makes outbound HTTPS requests **only when you ask it to** — when you
open the browser, run a search, page through results, or download something. It
makes no network request at startup, on a timer, when applying a wallpaper, or
when extracting a palette.

| Host | Reached when | What is sent | What comes back |
| --- | --- | --- | --- |
| `wallhaven.cc` | you search or browse Wallhaven | your search text, page number, sort order, category and purity filters, and your Wallhaven API key **only if you set one** | a JSON list of wallpapers |
| `th.wallhaven.cc` | a Wallhaven result is shown | the thumbnail's URL | a thumbnail image |
| `w.wallhaven.cc` | you add a Wallhaven wallpaper | the image's URL | the full-size image |
| `steamcommunity.com` | you search the Steam Workshop | your search text, page number, sort order, and the Wallpaper Engine app id (431960) | the public browse page, from which item ids are read |
| `api.steampowered.com` | Workshop results are shown | the item ids from that page | public item metadata: title, preview URL, size, tags, subscriber counts |
| `images.steamusercontent.com` | a Workshop result is shown | the preview's URL | a preview image |

That is the complete list. There are no other hosts.

No request carries a login, a cookie, a device identifier, a machine id, a
username, or anything about your library, your hardware or your other
wallpapers. Exactly one header is sent:

```
User-Agent: Mozilla/5.0 (X11; Linux x86_64) fossypaper-engine/0.2
```

It is shaped like a browser's because Wallhaven sits behind Cloudflare, which
answers `502` to anything that isn't — but it names the program, and the version,
and nothing else. There is no identifier in it, and it is the same string for
every user. Non-HTTPS URLs are refused outright.

Wallhaven and Valve will, like any web server, see the IP address your request
comes from. fossypaper cannot change that; a VPN or Tor can.

## Credentials

Two optional fields can hold a secret:

- **Wallhaven API key** — needed only for non-SFW results. It is sent to
  `wallhaven.cc` and to nowhere else.
- **Steam login name** — only a *username*, used as an argument to `steamcmd`
  when you have it installed. fossypaper never asks for, stores, or handles
  your Steam password; steamcmd prompts you directly and manages its own
  session.

Both live in `~/.config/fossypaper/config.json` as plain text, readable by your
user account. If that matters to you, leave them blank — the Workshop browser
works without either, and hands items to Steam to fetch.

## What is written to your disk

| Path | What | Remove with |
| --- | --- | --- |
| `~/.config/fossypaper/config.json` | your settings and per-wallpaper property overrides | delete the file |
| `~/.local/state/fossypaper/` | the extracted palette (`colors.json/.sh/.css`), the frame it came from, any SDDM still | delete the directory |
| `~/.cache/fossypaper/thumbs/` | browser thumbnails, keyed by a hash of their URL | `fossypaper config --reset` does not touch it; delete the directory |
| `~/.cache/fossypaper/library/` | stills baked from your own local previews | delete the directory |
| `~/.local/share/fossypaper/wallpapers/` | wallpapers you downloaded from Wallhaven | delete what you don't want |
| `~/.config/systemd/user/fossypaper.service` | only if you enable login autostart | `fossypaper autostart off` |

Nothing else is written anywhere. fossypaper does not touch `/etc`, `/usr` or
anything outside your home directory — the SDDM command it prints for you to run
is exactly that: printed, for you to run.

## What runs on your machine

fossypaper starts other programs to do the actual rendering:
`linux-wallpaperengine`, `mpvpaper`, `swww`, and — only if you use those
features — `steamcmd`, `ffmpeg`, `matugen`/`wallust`/`wal`, `xdg-open`, and
`systemctl --user`. Those are separate projects with their own behaviour and
their own privacy characteristics.

`fossypaper doctor` lists which of them are present.

## Third parties

Wallhaven and Steam are other people's services with their own terms and their
own privacy policies:

- <https://wallhaven.cc/terms>
- <https://store.steampowered.com/privacy_agreement/>

Wallpapers you download were made by other people and carry whatever terms they
carry. fossypaper takes no position on them and grants you no rights to them.

## Changes

This file ships in the repository. Its history is the changelog. `fossypaper
privacy` prints it; the GUI shows it under **Privacy**.
