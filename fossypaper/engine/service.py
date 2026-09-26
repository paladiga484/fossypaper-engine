"""Login autostart and the SDDM login background."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import media, render, tools


# --------------------------------------------------------------------------- #
#  Login daemon + SDDM background
# --------------------------------------------------------------------------- #
SERVICE = Path.home() / ".config/systemd/user/fossypaper.service"


def ensure_wayland_env() -> None:
    """A login service can start before the compositor exported WAYLAND_DISPLAY."""
    if os.environ.get("WAYLAND_DISPLAY"):
        return
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for s in sorted(Path(rt).glob("wayland-*")):
        if not s.name.endswith(".lock"):
            os.environ["WAYLAND_DISPLAY"] = s.name
            os.environ.setdefault("XDG_RUNTIME_DIR", rt)
            return


def apply_current() -> tuple[bool, str]:
    """Restore the saved wallpaper — the login daemon's job."""
    from .. import config
    ensure_wayland_env()
    cfg = config.load()
    wid = cfg.get("current")
    if not wid:
        return False, "nothing saved to restore"
    o = config.opts(cfg)
    o["properties"] = cfg.get("properties", {}).get(wid, {})
    return render.start(wid, o)


def install_service() -> Path:
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    exe = tools.which("fossypaper") or str(Path.home() / ".local/bin/fossypaper")
    SERVICE.write_text(
        "[Unit]\nDescription=fossypaper — restore the wallpaper at login\n"
        "After=graphical-session.target\nPartOf=graphical-session.target\n\n"
        "[Service]\nType=oneshot\nRemainAfterExit=yes\n"
        f"ExecStart={exe} daemon\n\n"
        "[Install]\nWantedBy=graphical-session.target\n")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "--user", "enable", "fossypaper.service"], capture_output=True)
    return SERVICE


def remove_service() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", "fossypaper.service"],
                   capture_output=True)
    SERVICE.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)


_SDDM_DEST = "/usr/share/sddm/themes/fossypaper-bg.png"


def sddm_prepare(wid: str, opts: dict) -> tuple[bool, str, dict]:
    """Render a still for the SDDM login background and stage a drop-in. SDDM is
    system-level, so hand back the exact root commands rather than touching /usr."""
    tools.STATE.mkdir(parents=True, exist_ok=True)
    still = tools.STATE / "sddm-background.png"
    if not media.screenshot(wid, still, opts):
        return False, "couldn't produce a still image", {}
    conf = tools.STATE / "sddm-fossypaper.conf"
    conf.write_text("[Theme]\n# point your SDDM theme's background at the still below,\n"
                    "# or use a theme that honours [General] background=\n"
                    f"\n[General]\nbackground={_SDDM_DEST}\n")
    return True, str(still), {
        "still": str(still), "conf": str(conf),
        "install": [f"sudo install -Dm644 {still} {_SDDM_DEST}",
                    f"sudo install -Dm644 {conf} /etc/sddm.conf.d/10-fossypaper.conf"]}
