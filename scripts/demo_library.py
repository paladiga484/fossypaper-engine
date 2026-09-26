#!/usr/bin/env python3
"""Build a throwaway wallpaper library for screenshots and demos.

Everything in it is generated here — nebulae, rings, one eye — so the
README never shows someone else's Workshop art or anyone's real library.

    python3 scripts/demo_library.py /tmp/fp-demo
    FOSSYPAPER_LIBRARY=/tmp/fp-demo/lib HOME=/tmp/fp-demo/home fossypaper
"""
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def nebula(w, h, c1, c2, stars=400, eye=False, rings=0):
    img = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y, r = random.randint(0, w), random.randint(0, h), random.randint(w // 12, w // 3)
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * random.random()) for i in range(3))
        d.ellipse((x - r, y - r, x + r, y + r), fill=col)
    img = img.filter(ImageFilter.GaussianBlur(w // 18))
    d = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    for i in range(rings):
        r = h // 4 + i * h // 14
        d.ellipse((cx - r * 1.6, cy - r * 0.5, cx + r * 1.6, cy + r * 0.5),
                  outline=tuple(min(255, v + 60) for v in c2), width=2)
    if eye:
        r = h // 5
        d.ellipse((cx - r * 1.8, cy - r, cx + r * 1.8, cy + r), fill=(12, 10, 14))
        d.ellipse((cx - r * 0.7, cy - r * 0.7, cx + r * 0.7, cy + r * 0.7), fill=c2)
        d.ellipse((cx - r * 0.15, cy - r * 0.6, cx + r * 0.15, cy + r * 0.6), fill=(0, 0, 0))
    for _ in range(stars):
        b = random.randint(150, 255)
        d.point((random.randint(0, w - 1), random.randint(0, h - 1)), fill=(b, b, b))
    return img


ITEMS = [
    ("3000000001", "The Drowned Observatory", "scene", ((8, 12, 30), (40, 90, 120)), {"rings": 4}, ["Space", "Sci-Fi"]),
    ("3000000002", "Carcosa Twin Suns", "scene", ((40, 18, 8), (200, 120, 40)), {}, ["Landscape", "Fantasy"]),
    ("3000000003", "It Watches From The Nebula", "scene", ((10, 6, 20), (120, 40, 140)), {"eye": True}, ["Space", "Horror"]),
    ("3000000004", "Abyssal Trench Lights", "video", ((2, 10, 18), (20, 80, 90)), {"stars": 120}, ["Nature"]),
    ("3000000005", "Static Between Stations", "video", ((14, 14, 16), (70, 70, 80)), {"stars": 900}, ["Abstract"]),
    ("3000000006", "Bloodmoon Over Dead Pines", "image", ((20, 4, 6), (150, 20, 20)), {}, ["Landscape"]),
    ("3000000007", "Quiet Void, Four Rings", "image", ((4, 4, 8), (60, 60, 110)), {"rings": 3}, ["Space", "Minimal"]),
    ("3000000008", "Lighthouse at the Edge of Time", "scene", ((10, 14, 22), (180, 160, 90)), {"stars": 250}, ["Landscape", "Space"]),
]


def main(out: Path) -> None:
    random.seed(7)
    lib = out / "lib"
    for wid, title, kind, (c1, c2), kw, tags in ITEMS:
        d = lib / wid
        d.mkdir(parents=True, exist_ok=True)
        img = nebula(1920, 1080, c1, c2, **kw)
        img.resize((512, 288)).save(d / "preview.jpg", quality=88)
        if kind == "image":
            img.save(d / "wallpaper.png")
            entry = "wallpaper.png"
        elif kind == "video":
            (d / "loop.mp4").write_bytes(b"\0" * 64)       # a placeholder: never rendered
            entry = "loop.mp4"
        else:
            (d / "scene.pkg").write_bytes(b"\0" * 64)
            entry = "scene.json"
        (d / "project.json").write_text(json.dumps({
            "title": title, "type": kind, "file": entry, "preview": "preview.jpg", "tags": tags,
            "general": {"supportsaudioprocessing": wid.endswith("3"), "properties": {
                "speed": {"type": "slider", "text": "Drift speed", "value": 1.0,
                          "min": 0, "max": 4, "step": 0.1, "order": 1},
                "stars": {"type": "bool", "text": "Stars", "value": True, "order": 2}}}}))
    cfg = out / "home/.config/fossypaper"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.json").write_text(json.dumps(
        {"ui_theme": "eldritch", "current": "3000000003", "layer": "auto", "output": "DP-1"}))
    print(lib)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "fp-demo"))
