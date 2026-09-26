#!/usr/bin/env python3
"""Render a `tmux capture-pane -e` dump to a PNG, like a terminal would.

Enough of SGR for what lazypaper draws: the 16 colours, 256-colour
foregrounds, reverse video and resets. Colours are a plain dark palette,
since lazypaper itself uses whatever the terminal defines.

    python3 scripts/ansi_to_png.py pane.ansi out.png
"""
import re
import subprocess
import sys
import unicodedata

from PIL import Image, ImageDraw, ImageFont

BG, FG = (16, 14, 20), (214, 208, 222)
BASE16 = [(22, 20, 28), (214, 84, 96), (126, 196, 120), (224, 184, 96),
          (104, 136, 214), (180, 120, 214), (96, 196, 204), (200, 196, 208),
          (80, 76, 92), (236, 110, 120), (150, 220, 140), (240, 204, 120),
          (130, 160, 236), (200, 146, 232), (120, 216, 224), (240, 236, 246)]


def xterm256(n: int):
    if n < 16:
        return BASE16[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return steps[n // 36], steps[(n // 6) % 6], steps[n % 6]
    v = 8 + (n - 232) * 10
    return v, v, v


def font(size: int):
    for name in ("JetBrainsMono Nerd Font Mono", "DejaVu Sans Mono", "monospace"):
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", name],
                                  capture_output=True, text=True).stdout
            if path:
                return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main(src: str, dst: str, size: int = 17) -> None:
    f = font(size)
    cw = int(f.getlength("M"))
    ch = int(size * 1.35)
    rows = open(src, encoding="utf-8", errors="replace").read().rstrip("\n").split("\n")
    cols = 0
    parsed = []
    sgr = re.compile(r"\x1b\[([0-9;]*)m")
    for line in rows:
        fg, bg, rev = FG, None, False
        cells, pos = [], 0
        for m in list(sgr.finditer(line)) + [None]:
            end = m.start() if m else len(line)
            for chr_ in line[pos:end]:
                wide = 2 if unicodedata.east_asian_width(chr_) in ("W", "F") else 1
                a, b = (bg or BG, fg) if rev else (fg, bg)
                cells.append((chr_, a, b, wide))
            if m is None:
                break
            pos = m.end()
            codes = [int(c) if c else 0 for c in m.group(1).split(";")]
            i = 0
            while i < len(codes):
                c = codes[i]
                if c == 0:
                    fg, bg, rev = FG, None, False
                elif c == 7:
                    rev = True
                elif c == 27:
                    rev = False
                elif 30 <= c <= 37:
                    fg = BASE16[c - 30]
                elif 90 <= c <= 97:
                    fg = BASE16[c - 90 + 8]
                elif c == 39:
                    fg = FG
                elif 40 <= c <= 47:
                    bg = BASE16[c - 40]
                elif c == 49:
                    bg = None
                elif c in (38, 48) and i + 2 < len(codes) and codes[i + 1] == 5:
                    col = xterm256(codes[i + 2])
                    if c == 38:
                        fg = col
                    else:
                        bg = col
                    i += 2
                i += 1
        parsed.append(cells)
        cols = max(cols, sum(w for *_, w in cells))
    pad = 18
    img = Image.new("RGB", (cols * cw + pad * 2, len(parsed) * ch + pad * 2), BG)
    d = ImageDraw.Draw(img)
    for y, cells in enumerate(parsed):
        x = 0
        for chr_, fg, bg, wide in cells:
            px, py = pad + x * cw, pad + y * ch
            if bg:
                d.rectangle((px, py, px + cw * wide, py + ch), fill=bg)
            if chr_ != " ":
                d.text((px, py + (ch - size) // 2), chr_, font=f, fill=fg)
            x += wide
    img.save(dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
