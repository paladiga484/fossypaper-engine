"""fossypaper.sources — the built-in wallpaper browsers.

Two catalogues, one shape:

  * **Wallhaven** — its public JSON API. Stills, downloadable directly.
  * **Steam Workshop** — the Wallpaper Engine catalogue. Listing is keyless
    (the public browse page for ids, Valve's keyless detail endpoint for the
    metadata). Fetching the *content* is Steam's job, so we hand off to
    steamcmd or to Steam itself rather than pretending otherwise.

Network rules this module keeps, deliberately:
  * stdlib `urllib` only — no new dependency, no vendored HTTP stack;
  * outbound HTTPS only, to the hosts named in docs/PRIVACY.md;
  * **nothing listens.** fossypaper never opens a socket, never binds a port,
    never runs a helper server. Thumbnails are cached to disk and read as files.
  * no account, no cookie, no identifier — a query string is all that leaves.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import config

# Wallhaven sits behind Cloudflare, which answers 502 to a User-Agent that
# doesn't look like a browser — so this is shaped like one while still naming
# the program honestly. It carries no identifier of any kind. docs/PRIVACY.md
# quotes it verbatim; keep the two in step.
UA = "Mozilla/5.0 (X11; Linux x86_64) fossypaper-engine/0.2"
TIMEOUT = 20

CACHE = Path.home() / ".cache/fossypaper/thumbs"
DOWNLOADS = Path.home() / ".local/share/fossypaper/wallpapers"

WALLHAVEN_API = "https://wallhaven.cc/api/v1/search"
WALLHAVEN_ONE = "https://wallhaven.cc/api/v1/w/"
WORKSHOP_BROWSE = "https://steamcommunity.com/workshop/browse/"
WORKSHOP_DETAILS = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
WE_APPID = 431960


@dataclass
class Listing:
    """One row in a browser. `local_id` is set once it's in your library."""
    source: str                 # "wallhaven" | "workshop"
    id: str
    title: str
    thumb_url: str
    full_url: str               # image URL, or the Workshop page
    meta: str = ""              # resolution / kind / size, for the detail line
    kind: str = "image"         # image | scene | video
    page_url: str = ""

    @property
    def local_id(self) -> str:
        return self.id if self.source == "workshop" else f"wh-{self.id}"

    def installed(self) -> bool:
        from . import engine
        return (engine.WE_DIR / self.local_id).is_dir() or (DOWNLOADS / self.local_id).is_dir()


class SourceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
#  Transport
# --------------------------------------------------------------------------- #
def _get(url: str, data: bytes | None = None, timeout: int = TIMEOUT,
         retries: int = 1) -> bytes:
    """One HTTPS GET (or POST, with `data`).

    Wallhaven's edge answers 502 now and then under no particular provocation,
    so a 5xx is retried once after a short pause. A 4xx is not — that one means
    what it says.
    """
    if not url.startswith("https://"):
        raise SourceError("refusing a non-HTTPS request")
    host = urllib.parse.urlsplit(url).netloc
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise SourceError(f"{host} is rate-limiting us — wait a minute") from e
            if e.code < 500 or attempt == retries:
                raise SourceError(f"{host} said {e.code}") from e
            last = f"{host} said {e.code}"
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt == retries:
                raise SourceError(f"network error: {getattr(e, 'reason', e)}") from e
            last = f"network error: {getattr(e, 'reason', e)}"
        time.sleep(1.5)
    raise SourceError(last or "request failed")


def thumbnail(url: str) -> Path | None:
    """Fetch a thumbnail once, then serve it from disk forever after."""
    if not url:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlsplit(url).path).suffix or ".jpg"
    dest = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + ext)
    if dest.is_file() and dest.stat().st_size:
        return dest
    # via a temp file: an interrupted fetch must not leave a truncated image
    # cached under a name we will then trust forever
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        part.write_bytes(_get(url, timeout=15))
        os.replace(part, dest)
    except (SourceError, OSError):
        part.unlink(missing_ok=True)
        return None
    return dest


def clear_cache() -> int:
    """Wipe every cached thumbnail. Returns how many files went."""
    n = 0
    for f in CACHE.glob("*"):
        if f.is_file():
            f.unlink(); n += 1
    return n


# --------------------------------------------------------------------------- #
#  Wallhaven
# --------------------------------------------------------------------------- #
_PURITY = {"sfw": "100", "sketchy": "010", "both": "110"}
_SORTS = ("date_added", "relevance", "random", "views", "favorites", "toplist")


def wallhaven(query: str = "", page: int = 1, sorting: str = "",
              categories: str = "111", purity: str = "sfw",
              ratios: str = "", api_key: str = "") -> tuple[list[Listing], int]:
    """Search Wallhaven. Returns (rows, last_page).

    `api_key` is optional and only needed for NSFW results; it is read from your
    config and sent to wallhaven.cc alone.
    """
    query = query.strip()
    sorting = sorting if sorting in _SORTS else ""
    if not sorting or (query and sorting == "toplist"):
        # `toplist` intersects a search with the ranked chart and comes back
        # empty for almost any query — so it is the right default only when
        # there is no query to intersect with.
        sorting = "relevance" if query else "toplist"
    q = {"page": max(1, int(page)), "categories": categories,
         "purity": _PURITY.get(purity, "100"), "sorting": sorting}
    if query:
        q["q"] = query
    if ratios:
        q["ratios"] = ratios
    if api_key:
        q["apikey"] = api_key
    raw = json.loads(_get(WALLHAVEN_API + "?" + urllib.parse.urlencode(q)))
    rows = []
    for w in raw.get("data", []):
        rows.append(Listing(
            source="wallhaven", id=str(w.get("id", "")),
            title=f"{w.get('resolution', '?')} · {w.get('category', '')}",
            thumb_url=(w.get("thumbs") or {}).get("small", ""),
            full_url=w.get("path", ""),
            meta=f"{w.get('resolution', '?')}  {_size(w.get('file_size', 0))}  "
                 f"{w.get('views', 0)} views",
            kind="image", page_url=w.get("url", "")))
    return rows, int((raw.get("meta") or {}).get("last_page", 1))


def wallhaven_one(wid: str) -> Listing | None:
    """One wallpaper by its Wallhaven id. The search endpoint has no id filter,
    so guessing the file extension would be the alternative — this asks."""
    raw = json.loads(_get(WALLHAVEN_ONE + urllib.parse.quote(wid)))
    w = raw.get("data")
    if not isinstance(w, dict) or not w.get("path"):
        return None
    return Listing(
        source="wallhaven", id=str(w.get("id", wid)),
        title=f"{w.get('resolution', '?')} · {w.get('category', '')}",
        thumb_url=(w.get("thumbs") or {}).get("small", ""),
        full_url=w["path"],
        meta=f"{w.get('resolution', '?')}  {_size(w.get('file_size', 0))}",
        kind="image", page_url=w.get("url", ""))


def fetch_wallhaven(row: Listing) -> tuple[bool, str]:
    """Download a Wallhaven still into fossypaper's own library, shaped like a
    Wallpaper Engine folder so the rest of the app treats it identically."""
    folder = DOWNLOADS / row.local_id
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(urllib.parse.urlsplit(row.full_url).path).suffix or ".jpg"
    dest = folder / ("wallpaper" + ext)
    try:
        dest.write_bytes(_get(row.full_url, timeout=120))
    except SourceError as e:
        return False, str(e)
    thumb = thumbnail(row.thumb_url)
    if thumb:
        shutil.copyfile(thumb, folder / ("preview" + thumb.suffix))
    (folder / "project.json").write_text(json.dumps({
        "title": f"wallhaven {row.id}", "type": "image", "file": dest.name,
        "preview": f"preview{thumb.suffix}" if thumb else "",
        "description": row.page_url, "general": {"properties": {}},
    }, indent=2))
    return True, f"saved → {folder}"


def _size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return ""


# --------------------------------------------------------------------------- #
#  Steam Workshop
# --------------------------------------------------------------------------- #
_ID_RE = re.compile(r"filedetails/\?id=(\d+)")
WORKSHOP_SORTS = {"trend": "trend", "recent": "mostrecent", "popular": "totaluniquesubscribers"}


def workshop(query: str = "", page: int = 1, sort: str = "trend") -> tuple[list[Listing], int]:
    """Browse the Wallpaper Engine Workshop without an API key.

    The public browse page yields the ids for a query; Valve's keyless detail
    endpoint turns those into titles, previews, kinds and sizes. Nothing here
    needs a login — but *downloading* does, which is what fetch_workshop covers.
    """
    qs = urllib.parse.urlencode({
        "appid": WE_APPID, "searchtext": query.strip(),
        "browsesort": WORKSHOP_SORTS.get(sort, "trend"),
        "section": "readytouseitems", "p": max(1, int(page))})
    html = _get(WORKSHOP_BROWSE + "?" + qs).decode("utf-8", "replace")
    ids, seen = [], set()
    for m in _ID_RE.finditer(html):
        if m.group(1) not in seen:
            seen.add(m.group(1)); ids.append(m.group(1))
    if not ids:
        return [], page
    rows = [_workshop_row(d) for d in details(ids[:30])]
    rows = [r for r in rows if r]
    # The browse page stops handing back ids once you run off the end.
    return rows, page + (1 if len(ids) >= 12 else 0)


def details(ids: list[str]) -> list[dict]:
    """Valve's keyless metadata endpoint. Batched; ids we can't resolve drop out."""
    if not ids:
        return []
    body = [("itemcount", str(len(ids)))]
    body += [(f"publishedfileids[{i}]", str(w)) for i, w in enumerate(ids)]
    raw = json.loads(_get(WORKSHOP_DETAILS, data=urllib.parse.urlencode(body).encode()))
    return (raw.get("response") or {}).get("publishedfiledetails", [])


def _workshop_row(d: dict) -> Listing | None:
    if str(d.get("consumer_app_id")) not in (str(WE_APPID), "None"):
        return None
    if d.get("banned") or not d.get("title"):
        return None
    tags = [t.get("tag", "") for t in d.get("tags", []) if isinstance(t, dict)]
    kind = next((t.lower() for t in tags if t.lower() in ("scene", "video", "web", "application")), "scene")
    res = next((t for t in tags if re.match(r"^\d+ x \d+$", t)), "")
    wid = str(d.get("publishedfileid"))
    return Listing(
        source="workshop", id=wid, title=str(d.get("title"))[:80],
        thumb_url=d.get("preview_url", ""),
        full_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}",
        meta=f"{kind}  {res}  {_size(d.get('file_size', 0))}  "
             f"{d.get('subscriptions', 0)} subs",
        kind=kind,
        page_url=f"https://steamcommunity.com/sharedfiles/filedetails/?id={wid}")


def fetch_workshop(row: Listing) -> tuple[bool, str]:
    """Get a Workshop item onto disk.

    Workshop content is Steam's to serve, and Wallpaper Engine items are
    owner-gated — so this drives the tools that are allowed to fetch them
    (steamcmd if you have it, Steam itself otherwise) instead of impersonating
    a client.
    """
    from . import engine
    cfg = config.load()
    if engine.which("steamcmd"):
        login = cfg.get("steam_login", "").strip() or "anonymous"
        argv = ["steamcmd", "+login", login, "+workshop_download_item",
                str(WE_APPID), row.id, "+quit"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError) as e:
            return False, f"steamcmd failed: {e}"
        from . import engine
        if (engine.STEAM_WORKSHOP / row.id).is_dir():
            return True, "downloaded via steamcmd"
        hint = "set steam_login to the account that owns Wallpaper Engine" \
            if login == "anonymous" else "steamcmd couldn't fetch it"
        return False, f"{hint} (steamcmd said: {r.stdout.strip().splitlines()[-1:] or '—'})"
    return open_in_steam(row)


def open_in_steam(row: Listing) -> tuple[bool, str]:
    """Open the item where Steam can subscribe to it; Steam then syncs it into
    the library fossypaper already reads."""
    from . import engine
    target = (f"steam://url/CommunityFilePage/{row.id}"
              if engine.which("steam") else row.page_url)
    opener = engine.which("xdg-open") or engine.which("steam")
    if not opener:
        return False, f"no opener found — visit {row.page_url}"
    try:
        subprocess.Popen([opener, target], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return False, f"couldn't open Steam: {e}"
    return True, "opened in Steam — subscribe there, then Refresh"


def fetch(row: Listing) -> tuple[bool, str]:
    return fetch_wallhaven(row) if row.source == "wallhaven" else fetch_workshop(row)


def available() -> dict:
    """Which browsers can do what, given what's installed."""
    from . import engine
    return {"wallhaven": True,
            "workshop": True,
            "workshop_download": bool(engine.which("steamcmd")),
            "steam": bool(engine.which("steam"))}
