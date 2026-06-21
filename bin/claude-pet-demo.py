#!/usr/bin/env python3
"""
claude-pet-demo — animation + life-stage test bench for terminal pets.

Run in a REAL terminal:
    python3 ~/claude-pet-demo.py

Controls:
    TAB / n   switch critter (Buddy · Cat · Slime)
    [  /  ]   younger / older   (egg → baby → kid → teen → adult)
    g         gallery: show ALL five ages at once
    SPACE     happy bounce        s  sleep/wake        k  sick        q  quit

Design notes (from research): emotion lives in the EYES; breathing idle +
randomized blink (with a half-closed mid-frame) + occasional yawn; non-uniform
timing (long holds, fast snaps); squash & stretch on Buddy's bounce. Frames are
block-centered and every line in a sprite is equal width, so nothing drifts.
"""
import math
import os
import random
import re
import select
import sys
import termios
import time
import tty

_ANSI = re.compile(r"\033\[[0-9;]*m")


def vlen(s):
    """Visible width: ignore ANSI color codes so centering/padding stays right."""
    return len(_ANSI.sub("", s))


def vpad(s, w):
    return s + " " * max(0, w - vlen(s))

TC = "\033[38;5;173m"      # terracotta (Buddy/Claude)
CYAN = "\033[38;5;51m"     # Cat
PINK = "\033[38;5;207m"    # Slime
HEART = "\033[38;5;207m"
DIM = "\033[38;5;245m"
TITLE = "\033[1;38;5;215m"
SLEEPC = "\033[38;5;111m"
R = "\033[0m"

BSU, ESU = "\033[?2026h", "\033[?2026l"
HIDE, SHOW = "\033[?25l", "\033[?25h"
HOME, CLR = "\033[H", "\033[2J\033[H"

STAGES = ["egg", "baby", "kid", "teen", "adult"]
EGG = [" ___ ", "/   \\", "\\___/"]

MOOD_EYES = {"content": ("●", "●"), "happy": ("^", "^"), "sleepy": ("-", "-"),
             "surprised": ("O", "O"), "sick": ("x", "x")}
MOOD_MOUTH = {"content": "‿", "happy": "‿", "sleepy": "~", "surprised": "o", "sick": "~"}

# Eyes are colored independently from the body (proves per-glyph color works).
EYE_COLORS = {
    "Buddy": "\033[1;38;5;231m",   # bright white on terracotta
    "Cat": "\033[1;38;5;190m",     # yellow-green cat eyes on cyan
    "Slime": "\033[1;38;5;87m",    # glowing cyan on magenta
}

# Per-stage templates. Every line within a stage is the SAME width, so the
# block centers cleanly (this is what fixes the cat's ears drifting).
TEMPLATES = {
    "Buddy": {
        "baby":  ["╭───╮", "│{l} {r}│", "╰─{m}─╯"],
        "kid":   ["╭────╮", "│{l}  {r}│", "│ {m}  │", "╰────╯"],
        "teen":  ["╭─────╮", "│ {l} {r} │", "│  {m}  │", "╰─────╯"],
        "adult": ["╭───────╮", "│  {l} {r}  │", "│   {m}   │", "╰───────╯"],
    },
    "Cat": {
        "baby":  [" /\\_/\\ ", "( {l} {r} )"],
        "kid":   [" /\\_/\\ ", "( {l} {r} )", "  >{m}<  "],
        "teen":  [" /\\_/\\ ", "( {l} {r} )", " > {m} < "],
        "adult": ["  /\\_/\\  ", "(  {l} {r}  )", " >  {m}  < "],
    },
    "Slime": {
        "baby":  [" ~~~ ", "({l} {r})", " ╰~╯ "],
        "kid":   [" ~~~~~ ", "( {l} {r} )", " ╰~~~╯ "],
        "teen":  [" ~~~~~ ", "( {l} {r} )", "(     )", " ╰~~~╯ "],
        "adult": [" ~~~~~~~ ", "( {l}   {r} )", "(       )", " ╰~~~~~╯ "],
    },
}
BUDDY_SQUASH = ["╭─────────╮", "│  {l}   {r}  │", "╰────{m}────╯"]
BUDDY_STRETCH = ["╭─────╮", "│ {l} {r} │", "│     │", "│  {m}  │", "╰─────╯"]

CRITTERS = [("Buddy", TC), ("Cat", CYAN), ("Slime", PINK)]


def build(name, l, r, m, shape="normal", stage="adult", colors=None):
    """colors=(eye_color, body_color) tints the eyes independently; the eye
    glyph switches color then returns to the body color, so only the eyes pop."""
    if stage == "egg":
        return list(EGG)
    if name == "Buddy" and shape == "squash":
        tmpl = BUDDY_SQUASH
    elif name == "Buddy" and shape == "stretch":
        tmpl = BUDDY_STRETCH
    else:
        tmpl = TEMPLATES[name][stage]
    if colors:
        eye, body = colors
        l = f"{eye}{l}{body}"
        r = f"{eye}{r}{body}"
    return [s.format(l=l, r=r, m=m) for s in tmpl]


# ---- animation sequences: list of (lines, seconds, y_offset) ----------------
def blink_seq(name, stage, colors=None):
    e = MOOD_EYES["content"]
    m = MOOD_MOUTH["content"]
    return [(build(name, e[0], e[1], m, stage=stage, colors=colors), 0.05, 0),
            (build(name, "⁀", "⁀", m, stage=stage, colors=colors), 0.05, 0),
            (build(name, "-", "-", m, stage=stage, colors=colors), 0.10, 0),
            (build(name, "⁀", "⁀", m, stage=stage, colors=colors), 0.05, 0)]


def yawn_seq(name, stage, colors=None):
    return [(build(name, "-", "-", "o", stage=stage, colors=colors), 0.18, 0),
            (build(name, "-", "-", "O", stage=stage, colors=colors), 0.45, 0),
            (build(name, "-", "-", "o", stage=stage, colors=colors), 0.18, 0),
            (build(name, "●", "●", "‿", stage=stage, colors=colors), 0.10, 0)]


def bounce_seq(name, stage, colors=None):
    if name == "Buddy" and stage == "adult":   # full squash & stretch
        return [(build(name, "^", "^", "‿", "squash", stage, colors), 0.10, 0),
                (build(name, "^", "^", "‿", "stretch", stage, colors), 0.08, -1),
                (build(name, "^", "^", "‿", "stretch", stage, colors), 0.12, -2),
                (build(name, "^", "^", "‿", "normal", stage, colors), 0.08, -1),
                (build(name, "^", "^", "‿", "squash", stage, colors), 0.12, 0),
                (build(name, "^", "^", "‿", "normal", stage, colors), 0.16, 0)]
    n = build(name, "^", "^", "‿", stage=stage, colors=colors)   # generic eased hop
    return [(n, 0.08, -1), (n, 0.10, -3), (n, 0.10, -4),
            (n, 0.08, -3), (n, 0.10, -1), (n, 0.14, 0)]


# ---- rendering --------------------------------------------------------------
def frame_single(lines, color, rows, cols, baseline, header, footer, extra=None):
    grid = [""] * rows
    bw = max((vlen(l) for l in lines), default=0)
    left = max(0, (cols - bw) // 2)
    top = baseline - len(lines) + 1
    for i, l in enumerate(lines):
        ry = top + i
        if 0 <= ry < rows:
            grid[ry] = " " * left + color + l + R
    for (ry, cx, txt, c) in (extra or []):
        if 0 <= ry < rows and not grid[ry]:
            grid[ry] = " " * max(0, cx) + c + txt + R
    grid[0] = header
    grid[rows - 1] = footer
    return BSU + HOME + "\n".join(g + "\033[K" for g in grid) + "\033[K" + ESU


def frame_gallery(name, color, eye_color, rows, cols, header, footer):
    colors = (eye_color, color)
    sprites = []
    for st in STAGES:
        if st == "egg":
            lines = list(EGG)
        else:
            lines = build(name, "●", "●", "‿", stage=st, colors=colors)
        sprites.append((st, lines))
    h = max(len(s) for _, s in sprites)
    widths = [max(vlen(l) for l in s) for _, s in sprites]
    gutter = 3
    total = sum(widths) + gutter * (len(sprites) - 1)
    left = max(0, (cols - total) // 2)
    # bottom-align each sprite into an h-row column
    cols_lines = []
    for (st, lines), w in zip(sprites, widths):
        padded = [""] * (h - len(lines)) + lines
        cols_lines.append([vpad(p, w) for p in padded])
    grid = [""] * rows
    top = max(2, (rows - h) // 2)
    for r in range(h):
        row = (" " * gutter).join(c[r] for c in cols_lines)
        grid[top + r] = " " * left + color + row + R
    # labels under each
    label = (" " * gutter).join(STAGES[i].center(widths[i]) for i in range(len(STAGES)))
    if top + h < rows - 1:
        grid[top + h] = " " * left + DIM + label + R
    grid[0] = header
    grid[rows - 1] = footer
    return BSU + HOME + "\n".join(g + "\033[K" for g in grid) + "\033[K" + ESU


def main():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("\n  This demo needs a real terminal. Run:  python3 ~/claude-pet-demo.py\n")
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write(HIDE + CLR)

    ci, si = 0, 4               # critter idx, stage idx (start adult)
    mood = "content"
    gallery = False
    sleeping = False
    t0 = time.time()
    next_blink = t0 + random.uniform(2.5, 5)
    next_yawn = t0 + random.uniform(8, 16)
    overlay = None
    try:
        while True:
            sz = os.get_terminal_size()
            cols, rows = sz.columns, max(14, min(22, sz.lines))
            baseline = rows // 2 + 3
            now = time.time()
            name, color = CRITTERS[ci]
            eye_c = EYE_COLORS[name]
            cpair = (eye_c, color)
            stage = STAGES[si]

            if select.select([sys.stdin], [], [], 0)[0]:
                k = sys.stdin.read(1)
                if k in ("q", "\x1b"):
                    break
                elif k in ("\t", "n"):
                    ci = (ci + 1) % len(CRITTERS); overlay = None
                elif k == "]":
                    si = min(4, si + 1); overlay = None
                elif k == "[":
                    si = max(0, si - 1); overlay = None
                elif k == "g":
                    gallery = not gallery; overlay = None
                elif k == " " and not sleeping and stage != "egg":
                    overlay = (bounce_seq(name, stage, cpair), now); mood = "happy"
                elif k == "s":
                    sleeping = not sleeping
                    mood = "sleepy" if sleeping else "content"; overlay = None
                elif k == "k":
                    mood = "sick"; sleeping = False; overlay = None

            header = f"{TITLE}  Clawde animation test{R}   {DIM}{name} · {stage} ({si+1}/5){'  · gallery' if gallery else ''}{R}"
            footer = f"{DIM}  TAB critter · [ ] age · g gallery · SPACE bounce · s sleep · q quit{R}"

            if gallery:
                sys.stdout.write(frame_gallery(name, color, eye_c, rows, cols, header, footer))
                sys.stdout.flush()
                time.sleep(1 / 20.0)
                continue

            # egg: gentle static wobble, no face animation
            if stage == "egg":
                bob = 1 if math.sin((now - t0) * 2 * math.pi / 2.2) < 0 else 0
                lines = list(EGG)
                sys.stdout.write(frame_single(lines, color, rows, cols, baseline - bob, header, footer))
                sys.stdout.flush()
                time.sleep(1 / 24.0)
                continue

            # schedule idle blink / yawn when calm
            if overlay is None and not sleeping and mood in ("content", "happy"):
                if now >= next_yawn:
                    overlay = (yawn_seq(name, stage, cpair), now)
                    next_yawn = now + random.uniform(9, 18)
                    next_blink = now + random.uniform(2.5, 5)
                elif now >= next_blink:
                    overlay = (blink_seq(name, stage, cpair), now)
                    next_blink = now + (0.22 if random.random() < 0.25 else random.uniform(2.5, 5))

            extra, yoff = None, 0
            if overlay is not None:
                frames, start = overlay
                elapsed = now - start
                total = sum(d for _, d, _ in frames)
                if elapsed >= total:
                    overlay = None
                    if mood == "happy":
                        mood = "content"
                else:
                    acc = 0.0
                    lines, yoff = frames[0][0], frames[0][2]
                    for fr, d, yo in frames:
                        acc += d
                        if elapsed < acc:
                            lines, yoff = fr, yo
                            break
                    if mood == "happy":
                        prog = elapsed / total
                        hy = baseline - len(lines) - 1 - int(prog * 4)
                        extra = [(hy, cols // 2 - 6, "♡", HEART), (hy + 1, cols // 2 + 4, "♥", HEART)]
            if overlay is None:
                el, er = MOOD_EYES[mood]
                m = MOOD_MOUTH[mood]
                breath = math.sin((now - t0) * 2 * math.pi / 3.6)
                yoff = -1 if breath > 0.3 else 0
                color_use = SLEEPC if sleeping else color
                lines = build(name, el, er, m, stage=stage, colors=(eye_c, color_use))
                if sleeping:
                    zc = int((now - t0) * 2) % 3
                    extra = [(baseline - len(lines) - 1 - zc, cols // 2 + 3, "ᶻ", SLEEPC)]
            else:
                color_use = color

            sys.stdout.write(frame_single(lines, color_use, rows, cols, baseline + yoff, header, footer, extra))
            sys.stdout.flush()
            time.sleep(1 / 24.0)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW + ESU + R + "\n")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
