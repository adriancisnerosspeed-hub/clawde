#!/usr/bin/env python3
"""
Render a README demo GIF of Clawde (all three skins, breathing + blinking) by
drawing the engine's deterministic animation frames directly to images. No live
terminal / screen-recording needed.

    python3 scripts/make_demo_gif.py

Requires Pillow + a monospace font (Menlo on macOS). Outputs assets/demo.gif.
"""
import importlib.util
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# load the engine
spec = importlib.util.spec_from_file_location("cp", os.path.join(REPO, "bin", "claude_pet.py"))
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

# ---- look ----
BG = (17, 17, 27)            # dark terminal bg
DEFAULT_FG = (205, 214, 244)  # light gray for uncolored text
FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
FALLBACK_PATH = "/Library/Fonts/Arial Unicode.ttf"   # full coverage for glyphs Menlo lacks (‿ ⌄ ᶻ ✦ ♔ …)
SIZE = 26
PAD = 24
ANSI = re.compile(r"\033\[([0-9;]*)m")


def menlo_cmap():
    """Codepoints Menlo can actually draw; anything else uses the fallback font."""
    try:
        from fontTools.ttLib import TTCollection
        coll = TTCollection(FONT_PATH)
        return set(coll.fonts[0].getBestCmap().keys())
    except Exception:
        return None


def xterm_rgb(n):
    if n < 16:
        base = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16),
                (36, 114, 200), (188, 63, 188), (17, 168, 205), (229, 229, 229),
                (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
                (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
        return base[n]
    if n >= 232:
        v = 8 + 10 * (n - 232)
        return (v, v, v)
    n -= 16
    r, g, b = n // 36, (n % 36) // 6, n % 6
    conv = lambda c: 0 if c == 0 else 55 + 40 * c
    return (conv(r), conv(g), conv(b))


def color_from_code(code, cur):
    if code in ("", "0"):
        return DEFAULT_FG
    parts = code.split(";")
    if "38" in parts:
        i = parts.index("38")
        if i + 2 < len(parts) and parts[i + 1] == "5":
            return xterm_rgb(int(parts[i + 2]))
    return cur


def parse(line):
    """-> list of (char, rgb)"""
    out, i, cur = [], 0, DEFAULT_FG
    while i < len(line):
        if line[i] == "\033":
            m = ANSI.match(line, i)
            if m:
                cur = color_from_code(m.group(1), cur)
                i = m.end()
                continue
        out.append((line[i], cur))
        i += 1
    return out


_FALLBACK_SET = set("‿⌄ᶻ✦♔◕◔⁀‗▣")   # used only when fontTools isn't available


def render_frame(text, font, fallback, cmap, cw, ch):
    def font_for(c):
        if cmap is not None:
            return font if ord(c) in cmap else fallback
        return fallback if c in _FALLBACK_SET else font

    lines = text.split("\n")
    width = PAD * 2 + max(len(ANSI.sub("", l)) for l in lines) * cw
    height = PAD * 2 + len(lines) * ch
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    for row, line in enumerate(lines):
        y = PAD + row * ch
        for col, (chx, rgb) in enumerate(parse(line)):
            if chx != " ":
                f = font_for(chx)
                # center fallback (proportional) glyphs within the monospace cell
                dx = 0 if f is font else max(0, (cw - int(round(f.getlength(chx)))) // 2)
                d.text((PAD + col * cw + dx, y), chx, font=f, fill=rgb)
    return img


def main():
    font = ImageFont.truetype(FONT_PATH, SIZE, index=0)
    fallback = ImageFont.truetype(FALLBACK_PATH, SIZE)
    cmap = menlo_cmap()
    cw = int(round(font.getlength("M")))
    ch = SIZE + 8

    DAY = 86400

    def state(skin):
        s = cp.default_state(0)
        s["born"], s["last_seen"] = 0, 12 * DAY      # adult
        s["skin"] = skin
        s["hp"], s["happiness"], s["clarity"] = 100, 92, 85
        s["streak_days"], s["best_streak"] = 5, 9     # <7 so no ✦ accessory glyph
        s["last_fed_date"] = cp.date.fromtimestamp(0).isoformat()
        return s

    frames = []
    for skin in ("buddy", "slime", "cat"):
        st = state(skin)
        for f in range(10, 34):                       # 24 frames: blink lands mid-clip
            frames.append(render_frame(cp.full_render(st, now=0, frame=f), font, fallback, cmap, cw, ch))

    # normalize all frames to the same canvas size (max), centered
    W = max(im.width for im in frames)
    H = max(im.height for im in frames)
    norm = []
    for im in frames:
        canvas = Image.new("RGB", (W, H), BG)
        canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        norm.append(canvas)

    os.makedirs(os.path.join(REPO, "assets"), exist_ok=True)
    out = os.path.join(REPO, "assets", "demo.gif")
    norm[0].save(out, save_all=True, append_images=norm[1:], duration=90, loop=0, optimize=True)
    # also drop a single PNG for quick glyph inspection
    norm[12].save(os.path.join(REPO, "assets", "_frame.png"))
    print(f"wrote {out} ({len(norm)} frames, {W}x{H})")


if __name__ == "__main__":
    main()
