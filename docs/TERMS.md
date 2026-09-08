# fossypaper — terms of use

Last updated: 2026-09-08 · applies to fossypaper-engine 0.2.0 and its Noctalia plugin.

## The short version

fossypaper is free software you run yourself. There is no service, no account
and no company behind it. You may use it, read it, change it and pass it on.
It comes with no warranty. What you do with other people's wallpapers is
between you and them.

## Licence

fossypaper-engine is released under the MIT licence; the full text is in
`LICENSE` at the root of the repository. That licence is the operative legal
document. Everything below is the plain-language part, and where the two
disagree, the licence wins.

## No warranty

fossypaper is provided "as is". It drives a GPU renderer, spawns processes, and
writes files under your home directory. It has been tested, but it is not
certified for anything and no one is on call for it. If it misbehaves — a
wallpaper that won't render, a palette that turns your shell an unfortunate
colour, a login service you'd rather not have enabled — the remedy is to turn
it off, and the tools to do that are `fossypaper off`, `fossypaper autostart
off`, and `fossypaper config --reset`.

## What fossypaper is not

- **It is not Wallpaper Engine.** Wallpaper Engine is a commercial product by
  Kristjan Skutta, sold on Steam. fossypaper is an unaffiliated, independent
  program that renders some of the same content on Linux through
  `linux-wallpaperengine`. It is not endorsed by, connected to, or supported by
  Wallpaper Engine or Valve.
- **It is not a wallpaper repository.** fossypaper indexes what is already on
  your disk and helps you search two public catalogues. It hosts nothing.

## Content you download

Wallpapers on the Steam Workshop and on Wallhaven belong to the people who made
them and are covered by those platforms' terms:

- <https://steamcommunity.com/workshop/workshoplegalagreement/>
- <https://wallhaven.cc/terms>

fossypaper's browser is a client for those catalogues. It does not grant you a
licence to anything you find, and it does not check whether you have one.
Redistributing, reselling or otherwise using someone's wallpaper beyond what
they permit is your responsibility, not the program's.

Steam Workshop items for Wallpaper Engine are generally available only to
accounts that own Wallpaper Engine. fossypaper does not work around that: it
either uses `steamcmd` with a login you supply, or it hands the item to Steam
so you can subscribe there. Circumventing Steam's access controls is not a
feature and will not be added.

## Content ratings

Wallpaper Engine wallpapers carry a content rating, and Wallhaven has a purity
filter which fossypaper defaults to SFW. Neither filter is perfect and neither
is a substitute for your own judgement. Non-SFW Wallhaven results require an
API key you supply yourself.

## Your data

fossypaper collects nothing. See `docs/PRIVACY.md` — which names every host it
ever contacts and states plainly that it opens no listening socket.

## Contributions

Patches are welcome under the same MIT licence. Two rules are not up for
negotiation, because they are the point of the project:

1. **Nothing listens.** No local server, no port, no socket, no daemon.
2. **No telemetry.** No analytics, no phone-home, no update ping.
