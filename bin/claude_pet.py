#!/usr/bin/env python3
"""
Clawde — a context Tamagotchi for Claude Code.

The pet feeds on a clear head. Your statusline runs every message and sees the
real context window usage, so that's the heartbeat:
    low context  (clarity high)  -> it eats, heals, grows happy
    high context (clarity low)   -> stressed, health drains, it nags you
    a big context drop (/compact) -> a feast (health + happiness spike)

It ages in real-world time (egg -> baby -> kid -> teen -> adult) and it can
DIE from neglect: ~25 HP per full day you're away (after a 30-min grace), so a
weekend leaves it sick-but-alive and ~4 days of total abandonment kills it.
`--reset` hatches a fresh egg and bumps the generation counter.

Clawde comes in three switchable skins (buddy · cat · slime), each animated with
a breathing idle, randomized blink, yawn, happy bounce, and sleep.

CLI:
    python3 claude_pet.py             # full Tamagotchi screen (read-only peek)
    python3 claude_pet.py --watch     # live animated full-screen pet
    python3 claude_pet.py --play      # 'catch the crumbs' mini-game (boosts happiness)
    python3 claude_pet.py --skin cat  # switch critter skin (buddy | cat | slime)
    python3 claude_pet.py --hatch     # play the egg -> baby hatch animation
    python3 claude_pet.py --update P  # advance one tick with context P% used
    python3 claude_pet.py --reset     # hatch a new egg
    python3 claude_pet.py --selftest  # prove the rules, no files touched
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import date

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _vlen(s):
    """Visible length: ignore ANSI escapes so box padding stays aligned."""
    return len(_ANSI_RE.sub("", s))

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #
STATE_DIR = os.path.expanduser("~/.claude/claude-pet")
STATE_FILE = os.path.join(STATE_DIR, "pet.json")
NAME = "Clawde"

MIN_TICK_INTERVAL = 20          # s between meaningful stat updates (anti-spam)
GRACE = 1800                    # s of absence before neglect decay starts
NEGLECT_HP_PER_DAY = 26.0       # HP lost per full day away (after grace): ~4d is fatal, a weekend wounds

CLARITY_GOOD = 65               # clarity >= this -> thriving (and counts as "fed today")
CLARITY_BAD = 35                # clarity <= this -> stressed
MEAL_DROP = 25                  # context drop this big = a "meal"

IDLE_SLEEP = 900                # s of no activity before a sleeping pet shows zzz
MIN_SLEEP_SAMPLES = 120         # real ticks needed before a sleep window is learned
HIST_CAP = 6000                 # halve the activity histogram past this (gentle forgetting)
SLEEP_QUIET_FRAC = 0.35         # an hour is "quiet" if its activity < this * mean
SLEEP_MIN_HOURS = 3             # learned sleep window must be at least this long
SLEEP_MAX_HOURS = 11            # ...and at most this long (else treat as no clear pattern)

# stage thresholds in seconds since birth
STAGES = [
    ("egg", 15 * 60),
    ("baby", 24 * 3600),
    ("kid", 3 * 24 * 3600),
    ("teen", 7 * 24 * 3600),
    ("adult", float("inf")),
]

# --------------------------------------------------------------------------- #
# ANSI
# --------------------------------------------------------------------------- #
R = "\033[0m"
TC = "\033[38;5;173m"      # terracotta
TCB = "\033[1;38;5;215m"
DIM = "\033[38;5;245m"
BORDER = "\033[38;5;240m"
PINK = "\033[38;5;207m"
CYAN = "\033[38;5;51m"
GREEN = "\033[38;5;78m"
AMBER = "\033[38;5;179m"
RED = "\033[38;5;203m"
GOLD = "\033[1;38;5;220m"
SLEEPC = "\033[38;5;111m"

HIDE_CUR = "\033[?25l"
SHOW_CUR = "\033[?25h"
HOME = "\033[H"
CLEAR = "\033[2J\033[H"
CLR_EOL = "\033[K"


def clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def default_state(now):
    return {
        "name": NAME,
        "born": now,
        "last_seen": now,
        "last_tick": -1e12,     # so the first real tick always counts
        "clarity": 80.0,        # current (display) clarity
        "tick_clarity": 80.0,   # baseline for meal detection
        "hp": 100.0,
        "happiness": 80.0,
        "alive": True,
        "generation": 1,
        "best_age": 0.0,
        "streak_days": 0,           # consecutive calendar days fed
        "best_streak": 0,
        "last_fed_date": "",        # local YYYY-MM-DD of last good feeding
        "activity_hist": [0] * 24,  # learned activity by local hour (for sleep)
        "skin": "buddy",            # critter skin: buddy | cat | slime
        "best_play": 0,             # best mini-game score
    }


def load_state(now=None):
    now = time.time() if now is None else now
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        for k, v in default_state(now).items():
            st.setdefault(k, v)
        return st
    except Exception:
        return default_state(now)


def save_state(st):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        # Unique temp per process: the statusline ticks every second and several
        # Claude sessions may write at once; a shared temp name would let two
        # writers clobber it mid-write and corrupt pet.json. os.replace is atomic.
        tmp = f"{STATE_FILE}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Sleep (learned from activity) & streak helpers
# --------------------------------------------------------------------------- #
def local_hour(ts):
    return time.localtime(ts).tm_hour


def local_date(ts):
    return date.fromtimestamp(ts)


def sleep_window(hist):
    """Learn the pet's sleep hours = the longest contiguous block of 'quiet'
    local hours in the activity histogram. Returns a frozenset of hours, or
    None while still learning / when there's no clear pattern."""
    if not hist or len(hist) != 24:
        return None
    total = sum(hist)
    if total < MIN_SLEEP_SAMPLES:
        return None                      # not enough data yet -> never sleeps
    thr = (total / 24.0) * SLEEP_QUIET_FRAC
    quiet = [c < thr for c in hist]
    if not any(quiet) or all(quiet):
        return None
    best_start, best_len = 0, 0
    cur_start, cur_len = 0, 0
    for i in range(48):                   # scan twice to catch wrap-around runs
        h = i % 24
        if quiet[h]:
            if cur_len == 0:
                cur_start = h
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0
    best_len = min(best_len, 24)
    if best_len < SLEEP_MIN_HOURS or best_len > SLEEP_MAX_HOURS:
        return None
    return frozenset((best_start + k) % 24 for k in range(best_len))


def awake_seconds(last_seen, now, win):
    """Seconds in [last_seen, now] that fall in the pet's WAKING hours.
    Sleeping hours don't count as neglect."""
    span = now - last_seen
    if span <= 0:
        return 0.0
    if not win:
        return span
    if span > 14 * 86400:                 # too long to walk hour-by-hour
        return span * (24 - len(win)) / 24.0
    total = 0.0
    t = float(last_seen)
    while t < now:
        nxt = (math.floor(t / 3600.0) + 1) * 3600.0
        seg_end = min(nxt, now)
        if local_hour(t) not in win:
            total += seg_end - t
        t = seg_end
    return total


def is_sleeping(st, now):
    if not st.get("alive", True):
        return False
    win = sleep_window(st.get("activity_hist"))
    if not win or local_hour(now) not in win:
        return False
    return (now - st.get("last_seen", now)) > IDLE_SLEEP


def current_streak(st, now):
    """Effective streak today: alive if fed today or yesterday, else broken."""
    last = st.get("last_fed_date", "")
    if not last:
        return 0
    try:
        diff = (local_date(now) - date.fromisoformat(last)).days
    except Exception:
        return 0
    return int(st.get("streak_days", 0)) if diff <= 1 else 0


def _register_feed(st, now):
    today = local_date(now).isoformat()
    last = st.get("last_fed_date", "")
    if today == last:
        return                            # already fed today
    diff = 99
    if last:
        try:
            diff = (date.fromisoformat(today) - date.fromisoformat(last)).days
        except Exception:
            diff = 99
    st["streak_days"] = int(st.get("streak_days", 0)) + 1 if diff == 1 else 1
    st["last_fed_date"] = today
    st["best_streak"] = max(int(st.get("best_streak", 0)), st["streak_days"])


# --------------------------------------------------------------------------- #
# Core rules (pure)
# --------------------------------------------------------------------------- #
def update(st, used_pct, now):
    if not st.get("alive", True):
        return st  # dead stays dead until reset

    used_pct = clamp(used_pct)
    clarity_now = clamp(100.0 - used_pct)

    last_seen = st.get("last_seen", now)
    win = sleep_window(st.get("activity_hist"))
    awake_away = awake_seconds(last_seen, now, win)   # sleep hours don't count
    if awake_away > GRACE:
        lost = (awake_away - GRACE) / 86400.0 * NEGLECT_HP_PER_DAY
        st["hp"] = clamp(st["hp"] - lost)
        st["happiness"] = clamp(st["happiness"] - lost * 0.5)

    last_tick = st.get("last_tick", -1e12)
    if now - last_tick >= MIN_TICK_INTERVAL:
        # learn when this user is active (for the sleep window)
        hist = st.get("activity_hist")
        if not hist or len(hist) != 24:
            hist = [0] * 24
        hist[local_hour(now)] += 1
        if sum(hist) > HIST_CAP:
            hist = [c // 2 for c in hist]   # gentle forgetting -> stays adaptive
        st["activity_hist"] = hist

        delta = clarity_now - st.get("tick_clarity", clarity_now)
        if delta >= MEAL_DROP:                       # a feast (e.g. /compact)
            st["hp"] = clamp(st["hp"] + 8)
            st["happiness"] = clamp(st["happiness"] + 10)
            st["last_meal"] = now
        if clarity_now >= CLARITY_GOOD:              # clear head
            st["happiness"] = clamp(st["happiness"] + 4)
            st["hp"] = clamp(st["hp"] + 2)
            _register_feed(st, now)                  # counts toward daily streak
        elif clarity_now <= CLARITY_BAD:             # cluttered
            st["happiness"] = clamp(st["happiness"] - 5)
            st["hp"] = clamp(st["hp"] - 3)
        else:
            st["happiness"] = clamp(st["happiness"] + 1)
        st["tick_clarity"] = clarity_now
        st["last_tick"] = now

    st["clarity"] = clarity_now
    st["last_seen"] = now

    if st["hp"] <= 0:
        st["alive"] = False
        st["died_at"] = now
        age = now - st.get("born", now)
        st["age_at_death"] = age
        st["best_age"] = max(st.get("best_age", 0.0), age)
    return st


def stage_for_age(age_seconds):
    for name, limit in STAGES:
        if age_seconds < limit:
            return name
    return "adult"


def mood_for(st):
    if not st.get("alive", True):
        return "dead"
    hp, hap = st["hp"], st["happiness"]
    if hp < 25 or hap < 20:
        return "sick"
    if hap >= 80:
        return "ecstatic"
    if hap >= 60:
        return "happy"
    if hap >= 40:
        return "ok"
    return "sad"


# --------------------------------------------------------------------------- #
# Public tick (loads, updates, saves) — used by the statusline
# --------------------------------------------------------------------------- #
def tick(used_pct, now=None):
    now = time.time() if now is None else now
    st = load_state(now)
    update(st, used_pct if used_pct is not None else 0, now)
    save_state(st)
    return st


def reset(now=None):
    now = time.time() if now is None else now
    old = load_state(now)
    fresh = default_state(now)
    fresh["generation"] = int(old.get("generation", 1)) + (0 if old.get("alive", True) and old.get("born") == now else 1)
    fresh["best_age"] = old.get("best_age", 0.0)
    # your habits and schedule persist across generations
    fresh["best_streak"] = int(old.get("best_streak", 0))
    fresh["streak_days"] = int(old.get("streak_days", 0))
    fresh["last_fed_date"] = old.get("last_fed_date", "")
    fresh["best_play"] = int(old.get("best_play", 0))
    fresh["skin"] = old.get("skin", "buddy")     # keep your chosen critter
    hist = old.get("activity_hist")
    fresh["activity_hist"] = hist if (hist and len(hist) == 24) else [0] * 24
    save_state(fresh)
    return fresh


# --------------------------------------------------------------------------- #
# Rendering — skinnable critter (buddy · cat · slime), emotion in the eyes.
# Every line within a stage is equal width; eyes are colored independently.
# --------------------------------------------------------------------------- #
EGG = [" ___ ", "/   \\", "\\___/"]
GRAVE = ["  ____  ", " /    \\ ", "│ R.I.P│", "│      │", "┴──────┴"]

SKINS = {
    "buddy": {
        "baby":  ["╭───╮", "│{l} {r}│", "╰─{m}─╯"],
        "kid":   ["╭────╮", "│{l}  {r}│", "│ {m}  │", "╰────╯"],
        "teen":  ["╭─────╮", "│ {l} {r} │", "│  {m}  │", "╰─────╯"],
        "adult": ["╭───────╮", "│  {l} {r}  │", "│   {m}   │", "╰───────╯"],
    },
    "cat": {
        "baby":  [" /\\_/\\ ", "( {l} {r} )"],
        "kid":   [" /\\_/\\ ", "( {l} {r} )", "  >{m}<  "],
        "teen":  [" /\\_/\\ ", "( {l} {r} )", " > {m} < "],
        "adult": ["  /\\_/\\  ", "(  {l} {r}  )", " >  {m}  < "],
    },
    "slime": {
        "baby":  [" ~~~ ", "({l} {r})", " ╰~╯ "],
        "kid":   [" ~~~~~ ", "( {l} {r} )", " ╰~~~╯ "],
        "teen":  [" ~~~~~ ", "( {l} {r} )", "(     )", " ╰~~~╯ "],
        "adult": [" ~~~~~~~ ", "( {l}   {r} )", "(       )", " ╰~~~~~╯ "],
    },
}
BUDDY_SQUASH = ["╭─────────╮", "│  {l}   {r}  │", "╰────{m}────╯"]
BUDDY_STRETCH = ["╭─────╮", "│ {l} {r} │", "│     │", "│  {m}  │", "╰─────╯"]

SKIN_NAMES = ["buddy", "cat", "slime"]
EYE_COLORS = {"buddy": "\033[1;38;5;231m", "cat": "\033[1;38;5;190m", "slime": "\033[1;38;5;87m"}
BODY_COLORS = {"buddy": TC, "cat": CYAN, "slime": PINK}
WRAP = {"buddy": ("(", ")"), "cat": ("=", "="), "slime": ("{", "}")}

# pet mood -> (left eye, right eye, mouth)
PET_FACE = {
    "ecstatic": ("^", "^", "‿"), "happy": ("●", "●", "‿"), "ok": ("●", "●", "-"),
    "sad": ("●", "●", "⌄"), "sick": ("x", "x", "~"), "sleep": ("-", "-", "~"),
}


def build_critter(skin, el, er, m, stage="adult", shape="normal", colors=None):
    """Return the critter's sprite lines. colors=(eye, body) tints just the eyes."""
    if stage == "egg":
        return list(EGG)
    if skin == "buddy" and shape == "squash":
        tmpl = BUDDY_SQUASH
    elif skin == "buddy" and shape == "stretch":
        tmpl = BUDDY_STRETCH
    else:
        tmpl = SKINS.get(skin, SKINS["buddy"])[stage]
    if colors:
        eye, body = colors
        el, er = f"{eye}{el}{body}", f"{eye}{er}{body}"
    return [s.format(l=el, r=er, m=m) for s in tmpl]


def compact_face(skin, mood, blink):
    el, er, m = PET_FACE.get(mood, PET_FACE["ok"])
    if blink:
        el = er = "-"
    eye = EYE_COLORS.get(skin, EYE_COLORS["buddy"])
    body = BODY_COLORS.get(skin, TC)
    L, Rr = WRAP.get(skin, ("(", ")"))
    return f"{body}{L}{eye}{el}{body}{m}{eye}{er}{body}{Rr}{R}"


def hp_color(hp):
    return GREEN if hp >= 60 else AMBER if hp >= 30 else RED


def streak_color(n):
    if n < 3:
        return DIM
    if n < 7:
        return AMBER          # bronze
    if n < 30:
        return CYAN           # silver
    return GOLD               # gold


def streak_badge(n, frame=0):
    if n <= 0:
        return None
    return f"{streak_color(n)}🔥{n}{R}"


def accessory(streak):
    """Gear earned by streak, worn on the pet's crown line."""
    if streak >= 30:
        return ("♔", GOLD)     # crown
    if streak >= 7:
        return ("✦", CYAN)     # sparkle
    return (None, "")


def compact_render(st, now=None):
    """One-liner for the statusline (animates gently off the wall clock)."""
    now = time.time() if now is None else now
    sec = int(now)
    name = st.get("name", NAME)
    skin = st.get("skin", "buddy")
    body = BODY_COLORS.get(skin, TC)
    age = (st.get("last_seen", 0) - st.get("born", 0))
    stage = stage_for_age(age)
    if not st.get("alive", True):
        return f"{DIM}🪦 {name} R.I.P.{R}"
    badge = streak_badge(current_streak(st, now))
    suffix = f" {badge}" if badge else ""
    if is_sleeping(st, now):
        z = "ᶻ" * (1 + sec % 3)                  # z's rise once a second
        return f"{SLEEPC}💤 {name} {z}{R}{suffix}"
    if stage == "egg":
        return f"{body}🥚 {name}{R} {DIM}(egg){R}{suffix}"
    face = compact_face(skin, mood_for(st), blink=(sec % 5 == 0))
    hp = int(round(st["hp"]))
    return f"{body}{name}{R} {face} {hp_color(hp)}♥{hp}{R}{suffix}"


def particle_line(mood, sleeping, recent_meal, frame, width):
    """One animated 'air' row drawn above the pet. Empty string = calm."""
    if frame is None:
        if recent_meal:
            return "      ✦ ♥ ✦"
        if sleeping:
            return "        ᶻ ᶻ"
        if mood in ("ecstatic", "happy"):
            return "        ♡"
        return ""
    if recent_meal:                              # munch sparkles
        return "      " + ("✦ ♥ ✦" if frame % 2 == 0 else " ♥ ✦ ♥")
    if sleeping:                                 # z's twinkle/drift
        cols, z = [6, 9, 12], [" "] * width
        for i, c in enumerate(cols):
            if (frame // 2 + i) % 3 != 0 and c < width:
                z[c] = "ᶻ"
        return "".join(z).rstrip()
    if mood in ("ecstatic", "happy"):            # a heart drifts sideways
        col, row = 6 + (frame // 2) % 10, [" "] * width
        if col < width:
            row[col] = "♡" if (frame // 2) % 2 == 0 else "♥"
        return "".join(row).rstrip()
    if mood == "sick":
        return "       ·" if (frame // 3) % 2 == 0 else ""
    return ""


def fmt_age(sec):
    sec = int(max(0, sec))
    d, h, m = sec // 86400, (sec % 86400) // 3600, (sec % 3600) // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{sec}s"


def _bar(val, color, width=10):
    val = int(round(val))
    fill = max(0, min(width, round(val / 100 * width)))
    return f"{color}{'█' * fill}{BORDER}{'░' * (width - fill)}{R} {color}{val:>3}{R}"


def full_render(st, now=None, frame=None):
    """Render the full Tamagotchi screen. Pass `frame` (an incrementing int) to
    animate — blink, sway, and floating particles. `frame=None` = a calm still."""
    now = time.time() if now is None else now
    INNER = 34
    alive = st.get("alive", True)
    age = (st.get("last_seen", 0) - st.get("born", 0)) if alive else st.get("age_at_death", 0)
    stage = stage_for_age(age)
    sleeping = alive and is_sleeping(st, now)
    mood = "sleep" if sleeping else mood_for(st)
    name = st.get("name", NAME)
    streak = current_streak(st, now)

    def line(content, color=""):
        pad = max(0, INNER - _vlen(content))
        body = content + " " * pad
        if color:
            body = color + body + R
        return f"{BORDER}│{R}{body}{BORDER}│{R}"

    def center(plain, color="", off=0):
        total = max(0, INNER - _vlen(plain))
        left = max(0, total // 2 + off)
        return line(" " * left + plain, color)

    if not alive:
        quip = f"{name} faded at age {fmt_age(age)}."
    elif sleeping:
        quip = "zzz… (sleeping)"
    elif st["hp"] < 25:
        quip = "I'm not feeling great… /compact?"
    elif st["clarity"] <= CLARITY_BAD:
        quip = "brain's getting full… /compact soon?"
    elif st["clarity"] >= CLARITY_GOOD and st["happiness"] >= 70:
        quip = "ahh, clear head — thanks!"
    else:
        quip = "doing alright in here."

    # animation state
    skin = st.get("skin", "buddy")
    body_color = BODY_COLORS.get(skin, TC)
    eye_color = EYE_COLORS.get(skin, EYE_COLORS["buddy"])
    blink = bool(frame is not None and not sleeping and alive and stage != "egg" and frame % 24 < 2)
    sway = 0 if frame is None else (1 if (frame // 7) % 2 else 0)
    recent_meal = bool(alive and not sleeping and (now - st.get("last_meal", -1e12) <= 4))

    status_word = "gone" if not alive else ("sleeping" if sleeping else stage)
    acc_glyph, acc_color = accessory(streak) if alive else (None, "")

    # build the critter sprite for this skin / stage / mood
    if not alive:
        sprite, sprite_color = list(GRAVE), DIM
    elif stage == "egg":
        sprite, sprite_color = list(EGG), body_color
    else:
        el, er, m = PET_FACE.get(mood, PET_FACE["ok"])
        if blink:
            el = er = "-"
        sprite_color = SLEEPC if sleeping else body_color
        sprite = build_critter(skin, el, er, m, stage=stage, colors=(eye_color, sprite_color))

    # one animated 'air' row above the pet
    p = particle_line(mood, sleeping, recent_meal, frame, INNER)
    if "♥" in p or "♡" in p:
        pcol = PINK
    elif "ᶻ" in p:
        pcol = SLEEPC
    elif "✦" in p:
        pcol = (acc_color or GOLD)
    else:
        pcol = DIM

    out = []
    out.append(f"{BORDER}┌{'─' * INNER}┐{R}")
    out.append(center(f"{name}  ·  {status_word}  ·  gen {int(st.get('generation', 1))}", TCB))
    out.append(line(p, pcol) if p else line(""))
    if alive and stage != "egg" and acc_glyph:        # crown / sparkle rides on top
        out.append(center(acc_glyph, acc_color, sway))
    for s in sprite:
        out.append(center(s, sprite_color, sway))
    out.append(line(""))
    out.append(center(quip, SLEEPC if sleeping else CYAN))
    out.append(line(""))
    out.append(line(f"  health  {_bar(st['hp'], hp_color(st['hp']))}"))
    out.append(line(f"  happy   {_bar(st['happiness'], PINK)}"))
    out.append(line(f"  clarity {_bar(st['clarity'], CYAN)}"))
    out.append(line(""))
    best_streak = int(st.get("best_streak", 0))
    scol = streak_color(streak) if streak else DIM
    out.append(line(f"  streak  {scol}{streak} days{R}   ·   best {best_streak}"))
    best = st.get("best_age", 0.0)
    out.append(center(f"age {fmt_age(age)}  ·  best {fmt_age(best)}", DIM))
    if not alive:
        out.append(center("run:  claude-pet --reset", AMBER))
    out.append(f"{BORDER}└{'─' * INNER}┘{R}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Self-test (no disk)
# --------------------------------------------------------------------------- #
def selftest():
    DAY = 86400

    # 1. fresh egg
    s = default_state(0)
    assert s["alive"] and s["hp"] == 100 and stage_for_age(0) == "egg"
    print("PASS  fresh state is a living egg")

    # 2. feeding (clear head) keeps it healthy and happy
    s = default_state(0)
    s["happiness"] = 50
    t = 0
    for _ in range(10):
        t += 30
        update(s, 10, t)  # 10% used -> clarity 90
    assert s["alive"] and s["happiness"] >= 80 and s["hp"] >= 95
    print(f"PASS  feeding raises happiness ({int(s['happiness'])}) & keeps hp high ({int(s['hp'])})")

    # 3. chronic max context starves it to death
    s = default_state(0)
    t = 0
    for _ in range(60):
        t += 30
        update(s, 95, t)  # clarity 5
    assert not s["alive"] and s["best_age"] > 0
    print("PASS  chronic high-context starves the pet to death")

    # 4. ~4 days of total absence kills; a weekend only wounds
    s = default_state(0)
    update(s, 50, 4 * DAY)
    assert not s["alive"], "4 days away should be fatal"
    s2 = default_state(0)
    update(s2, 50, 2 * DAY)
    assert s2["alive"] and 35 <= s2["hp"] <= 65, f"weekend should wound, hp={s2['hp']:.0f}"
    print(f"PASS  4d absence kills · weekend leaves it sick-but-alive (hp {int(s2['hp'])})")

    # 5. a big context drop (/compact) is a feast
    s = default_state(0)
    s["hp"] = 60
    s["happiness"] = 50
    update(s, 80, 30)            # high context first (clarity 20)
    hp_before = s["hp"]
    update(s, 10, 60)            # context drops to 10% -> clarity 90, +70 jump
    assert s["hp"] > hp_before + 5 and s.get("last_meal") == 60
    print("PASS  big context drop triggers a feast (hp + happiness spike)")

    # 6. anti-spam: ticks within 20s don't double-count
    s = default_state(0)
    s["happiness"] = 50
    update(s, 10, 100)
    h1 = s["happiness"]
    update(s, 10, 105)          # only 5s later
    assert s["happiness"] == h1, "ticks <20s apart must not change stats"
    print("PASS  rapid ticks (<20s) are display-only, no stat spam")

    # 7. stages by real age
    assert stage_for_age(10 * 60) == "egg"
    assert stage_for_age(2 * 3600) == "baby"
    assert stage_for_age(2 * DAY) == "kid"
    assert stage_for_age(5 * DAY) == "teen"
    assert stage_for_age(10 * DAY) == "adult"
    print("PASS  age maps to the right life stage")

    # 8. reset hatches a new egg, bumps generation, keeps best_age
    s = default_state(0)
    s["alive"] = False
    s["best_age"] = 12345.0
    s["generation"] = 2
    fresh = default_state(100)
    fresh["generation"] = s["generation"] + 1
    fresh["best_age"] = s["best_age"]
    assert fresh["alive"] and fresh["hp"] == 100 and fresh["generation"] == 3 and fresh["best_age"] == 12345.0
    print("PASS  reset = new egg, gen+1, best_age remembered")

    # 9. daily feeding streak: consecutive days increment, a gap resets
    def ts(y, mo, d, h):
        return time.mktime((y, mo, d, h, 0, 0, 0, 0, -1))
    s = default_state(ts(2026, 6, 1, 12))
    update(s, 10, ts(2026, 6, 1, 12))           # feed day 1
    assert s["streak_days"] == 1
    update(s, 10, ts(2026, 6, 1, 15))           # same day again -> still 1
    assert s["streak_days"] == 1
    update(s, 10, ts(2026, 6, 2, 12))           # next day -> 2
    update(s, 10, ts(2026, 6, 3, 12))           # next day -> 3
    assert s["streak_days"] == 3 and s["best_streak"] == 3
    update(s, 10, ts(2026, 6, 6, 12))           # skipped 6/4 & 6/5 -> reset to 1
    assert s["streak_days"] == 1 and s["best_streak"] == 3
    assert current_streak(s, ts(2026, 6, 6, 18)) == 1      # fed today -> alive
    assert current_streak(s, ts(2026, 6, 9, 12)) == 0      # 3 days later -> broken
    print("PASS  daily streak increments, resets on a missed day, best kept")

    # 10. sleep window learned from the activity histogram
    hist = [0] * 24
    for h in range(8, 24):
        hist[h] = 20                            # active 8:00–23:00, quiet 0:00–7:00
    win = sleep_window(hist)
    assert win == frozenset(range(0, 8)), f"expected sleep 0–7, got {sorted(win) if win else None}"
    assert sleep_window([1] * 24) is None       # flat activity -> no window
    assert sleep_window([0] * 24) is None       # no samples -> still learning
    print("PASS  sleep window = quietest contiguous block (0–7h here)")

    # 11. fair neglect: away during sleep barely decays; away while awake does
    base = default_state(ts(2026, 6, 1, 0))
    base["activity_hist"] = hist                 # sleep window 0–7
    base["hp"] = 100.0
    sl = json.loads(json.dumps(base))
    update(sl, 50, ts(2026, 6, 1, 7))           # away 0:00→7:00, all sleep hours
    aw = json.loads(json.dumps(base))
    aw["last_seen"] = ts(2026, 6, 1, 9)
    update(aw, 50, ts(2026, 6, 1, 16))          # away 9:00→16:00, all waking hours
    assert sl["hp"] >= 99, f"sleep-time absence should not decay (hp {sl['hp']:.1f})"
    assert aw["hp"] <= 95, f"waking absence should decay (hp {aw['hp']:.1f})"
    print(f"PASS  fair neglect: slept-through hp {int(sl['hp'])} vs awake-away hp {int(aw['hp'])}")

    # 12. render stability: every box line is exactly INNER+2 wide across every
    #     skin, stage, mood, streak tier and animation frame (catches misalignment)
    INNER_PLUS = 36
    checked = 0
    for skin in SKIN_NAMES:
        for stg_age in (5 * 60, 2 * 3600, 2 * DAY, 5 * DAY, 10 * DAY):
            for hp, hap in ((100, 90), (50, 50), (15, 10)):
                for strk in (0, 7, 30):
                    for alive in (True, False):
                        s = default_state(0)
                        s["born"], s["last_seen"] = 0, stg_age
                        s["skin"] = skin
                        s["hp"], s["happiness"], s["clarity"] = hp, hap, 80
                        s["streak_days"], s["best_streak"] = strk, strk
                        s["last_fed_date"] = date.fromtimestamp(0).isoformat()
                        s["alive"] = alive
                        if not alive:
                            s["age_at_death"] = stg_age
                        for frame in (None, 0, 1, 7, 24, 25):
                            for ln in full_render(s, now=0, frame=frame).splitlines():
                                assert _vlen(ln) == INNER_PLUS, (
                                    f"misaligned ({_vlen(ln)}!={INNER_PLUS}) skin={skin} "
                                    f"age={stg_age} hp={hp} streak={strk} alive={alive} frame={frame}: {ln!r}")
                                checked += 1
    # compact never throws and stays a single line, for every skin
    for skin in SKIN_NAMES:
        for sec in range(0, 12):
            s = default_state(sec); s["skin"] = skin
            assert "\n" not in compact_render(s, now=sec)
    print(f"PASS  render stays aligned across {checked} lines (all skins/stages/moods/frames)")

    # 13. mini-game: catch detection, clamping, and capped reward
    assert catch_overlap(5, 5) and catch_overlap(5, 11) and not catch_overlap(5, 12)
    assert clamp_x(-3, 7, 40) == 0 and clamp_x(99, 7, 40) == 33
    assert play_reward(0) == {"happiness": 0, "hp": 0}
    assert play_reward(100)["happiness"] == 25 and play_reward(100)["hp"] == 8   # capped
    s = default_state(0); s["happiness"] = 50.0; s["hp"] = 50.0
    apply_play(s, 6, now=10)
    assert s["happiness"] == 62 and s["hp"] == 56 and s["best_play"] == 6
    s["happiness"] = 90.0
    apply_play(s, 100, now=20)            # +25 happiness clamps at 100; best_play kept
    assert s["happiness"] == 100 and s["best_play"] == 100
    print("PASS  mini-game: catch/clamp/reward correct and capped")

    print("\nAll self-tests passed.")


# --------------------------------------------------------------------------- #
# Animated full-screen modes (need a real terminal)
# --------------------------------------------------------------------------- #
def _needs_tty(cmd="--watch"):
    if sys.stdin.isatty() and sys.stdout.isatty():
        return True
    print(
        f"\n  claude-pet {cmd} needs a real terminal window.\n"
        f"  Open Terminal.app (or iTerm) and run:  claude-pet {cmd}\n"
    )
    return False


def _play_evolve(stage):
    """A short celebratory sparkle flash when the pet levels up."""
    msgs = ["✦", "✦ ✧ ✦", "✧ ✦ ✧ ✦ ✧", f"★  leveled up → {stage}!  ★",
            "✧ ✦ ✧ ✦ ✧", "✦ ✧ ✦", "✦"]
    for m in msgs:
        sys.stdout.write(CLEAR + "\n\n\n\n")
        pad = max(0, (40 - len(m)) // 2)
        sys.stdout.write(GOLD + " " * pad + m + R + "\n")
        sys.stdout.flush()
        time.sleep(0.12)


def _animate(get_state, fps=12, evolve=True):
    """Shared render loop: redraw get_state() each frame; q/Ctrl-C exits."""
    try:
        import select
        import termios
        import tty as _tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        _tty.setcbreak(fd)
    except Exception:
        old = fd = None

    sys.stdout.write(HIDE_CUR + CLEAR)
    frame, last_stage = 0, None
    try:
        while True:
            if fd is not None:
                if select.select([sys.stdin], [], [], 0)[0] and sys.stdin.read(1) in ("q", "Q"):
                    break
            st, now = get_state(), time.time()
            alive = st.get("alive", True)
            age = (st.get("last_seen", 0) - st.get("born", 0)) if alive else st.get("age_at_death", 0)
            stage = stage_for_age(age)
            if evolve and last_stage is not None and stage != last_stage and alive:
                _play_evolve(stage)
            last_stage = stage
            body = full_render(st, now=now, frame=frame).replace("\n", CLR_EOL + "\n")
            sys.stdout.write(HOME + body + CLR_EOL)
            sys.stdout.write(f"\n{DIM}   q to quit · Clawde lives off your statusline{R}{CLR_EOL}")
            sys.stdout.flush()
            frame += 1
            time.sleep(1.0 / fps)
    except KeyboardInterrupt:
        pass
    finally:
        if fd is not None and old is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(SHOW_CUR + R + "\n")
        sys.stdout.flush()


def watch():
    if not _needs_tty("--watch"):
        return
    _animate(lambda: load_state())


def hatch_demo():
    """Show the egg → baby hatch animation regardless of the real pet."""
    if not _needs_tty("--hatch"):
        return
    now = time.time()
    egg = default_state(now)                 # age 0 -> egg
    baby = default_state(now)
    baby["born"] = now - 20 * 60             # age 20m -> baby
    sys.stdout.write(HIDE_CUR + CLEAR)
    try:
        for f in range(20):                  # egg wobbles
            sys.stdout.write(HOME + full_render(egg, now=now, frame=f).replace("\n", CLR_EOL + "\n") + CLR_EOL)
            sys.stdout.flush()
            time.sleep(1.0 / 12)
        _play_evolve("baby")
        for f in range(36):                  # baby says hi
            sys.stdout.write(HOME + full_render(baby, now=now, frame=f).replace("\n", CLR_EOL + "\n") + CLR_EOL)
            sys.stdout.flush()
            time.sleep(1.0 / 12)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CUR + R + "\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Skin switch + mini-game ("catch the crumbs")
# --------------------------------------------------------------------------- #
def set_skin(skin):
    st = load_state()
    if skin in SKIN_NAMES:
        st["skin"] = skin
        save_state(st)
    return st


CATCHER_W = 7   # the catcher sprite: \(●‿●)/


def clamp_x(x, w, width):
    return max(0, min(width - w, x))


def catch_overlap(catcher_x, crumb_x, w=CATCHER_W):
    return catcher_x <= crumb_x <= catcher_x + w - 1


def play_reward(score):
    """Score -> stat boost. Capped so the game can't be farmed to infinity."""
    return {"happiness": min(25, score * 2), "hp": min(8, score)}


def apply_play(st, score, now=None):
    now = time.time() if now is None else now
    r = play_reward(score)
    st["happiness"] = clamp(st.get("happiness", 0) + r["happiness"])
    st["hp"] = clamp(st.get("hp", 0) + r["hp"])
    st["best_play"] = max(int(st.get("best_play", 0)), score)
    st["last_played"] = now
    return r


def play_game():
    """Catch falling ✦ crumbs with the critter; the score boosts happiness."""
    if not _needs_tty("--play"):
        return
    import random
    import select
    import termios
    import tty as _tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    _tty.setcbreak(fd)

    st = load_state()
    skin = st.get("skin", "buddy")
    eye = EYE_COLORS.get(skin, EYE_COLORS["buddy"])
    body = BODY_COLORS.get(skin, TC)
    catcher = f"\\({eye}●{body}‿{eye}●{body})/"      # arms-up, colored eyes

    sz = os.get_terminal_size()
    W = max(20, min(sz.columns - 2, 44))
    H = max(14, min(20, sz.lines))
    catch_row = H - 2
    cx = (W - CATCHER_W) // 2
    crumbs, score, frame = [], 0, 0
    ROUND = 30.0
    start = time.time()

    sys.stdout.write(HIDE_CUR + CLEAR)
    try:
        while True:
            now = time.time()
            left = ROUND - (now - start)
            if left <= 0:
                break
            if select.select([sys.stdin], [], [], 0)[0]:
                data = os.read(fd, 6)
                if b"q" in data or b"\x03" in data or data == b"\x1b":
                    break
                if b"\x1b[D" in data or b"a" in data or b"h" in data:
                    cx = clamp_x(cx - 2, CATCHER_W, W)
                if b"\x1b[C" in data or b"d" in data or b"l" in data:
                    cx = clamp_x(cx + 2, CATCHER_W, W)

            spawn_every = max(4, 9 - int((now - start) / 6))   # speeds up
            fall_every = max(2, 3 - int((now - start) / 12))
            if frame % spawn_every == 0:
                crumbs.append([random.randint(0, W - 1), 1])
            if frame % fall_every == 0:
                moved = []
                for c in crumbs:
                    c[1] += 1
                    if c[1] >= catch_row:
                        if catch_overlap(cx, c[0]):
                            score += 1
                        # else: missed, drop it
                    else:
                        moved.append(c)
                crumbs = moved

            grid = [[" "] * W for _ in range(H)]
            for (xx, yy) in crumbs:
                if 0 <= yy < H and 0 <= xx < W:
                    grid[yy][xx] = "✦"
            rows = [GOLD + f" CATCH THE CRUMBS    {int(left):>2}s    caught {score}"[:W] + R]
            for ry in range(1, H):
                if ry == catch_row:
                    rows.append(body + " " * cx + catcher + R)
                else:
                    rows.append(GREEN + "".join(grid[ry]) + R)
            rows.append(DIM + " ←/→ move · q quit" + R)
            buf = "\033[?2026h" + HOME + "\n".join(r + CLR_EOL for r in rows) + CLR_EOL + "\033[?2026l"
            sys.stdout.write(buf)
            sys.stdout.flush()
            frame += 1
            time.sleep(1 / 15.0)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(SHOW_CUR + CLEAR + R)
        sys.stdout.flush()

    r = apply_play(st, score)
    save_state(st)
    print(f"\n  {body}Clawde caught {score} crumbs!{R}  "
          f"{PINK}♥ +{r['happiness']} happiness{R}, {GREEN}+{r['hp']} health{R}"
          f"   {DIM}(best: {int(st.get('best_play', 0))}){R}\n")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--watch", action="store_true", help="live animated full-screen pet")
    ap.add_argument("--hatch", action="store_true", help="play the egg → baby hatch animation")
    ap.add_argument("--play", action="store_true", help="play 'catch the crumbs' (boosts happiness)")
    ap.add_argument("--skin", choices=SKIN_NAMES, help="switch critter skin (buddy · cat · slime)")
    ap.add_argument("--statusline", action="store_true",
                    help="statusLine mode: read the Claude Code JSON on stdin, tick, print the one-line pet")
    ap.add_argument("--update", type=float, metavar="PCT", help="advance one tick with context PCT%% used")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return
    if args.statusline:
        try:
            data = json.load(sys.stdin)
        except Exception:
            data = {}
        used = (data.get("context_window") or {}).get("used_percentage")
        st = tick(used if used is not None else 0)
        sys.stdout.write(compact_render(st))
        return
    if args.skin:
        st = set_skin(args.skin)
        print(full_render(st))
        return
    if args.play:
        play_game()
        return
    if args.watch:
        watch()
        return
    if args.hatch:
        hatch_demo()
        return
    if args.reset:
        st = reset()
        print(full_render(st))
        return
    if args.update is not None:
        st = tick(args.update)
        print(compact_render(st))
        return
    # default: read-only peek at the current pet
    print(full_render(load_state()))


if __name__ == "__main__":
    main()
