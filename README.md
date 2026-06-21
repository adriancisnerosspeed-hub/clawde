# 🥚 Clawde — a context Tamagotchi for Claude Code

Clawde is a tiny pet that lives in your Claude Code **status line** and feeds on a
clear context window. Keep your context low and Clawde thrives — it eats, heals,
and grows up. Let the context fill toward full and it gets stressed, then sleepy,
then sick, and nags you to `/compact`. It's a gentle nudge toward good context
hygiene, disguised as something to keep alive.

```
┌──────────────────────────────────┐
│      Clawde  ·  adult  ·  gen 1    │
│              ✦                     │
│           ╭───────╮                │
│           │  ● ●  │                │
│           │   ‿   │                │
│           ╰───────╯                │
│     ahh, clear head — thanks!      │
│   health  ██████████ 100           │
│   happy   █████████░  92           │
│   clarity ████████░░  84           │
│   streak  9 days   ·   best 12     │
│         age 10d 0h  ·  best 0s     │
└──────────────────────────────────┘
```

In the status line it's a one-liner that animates as you work:
`Clawde (●‿●) ♥98 🔥7`

## What it does

- **Feeds on context.** Clarity = `100 − context%`. Low context heals and delights it; high context drains it. A big context drop (a `/compact`) is a feast.
- **Grows in real time:** 🥚 egg → baby → kid → teen → adult.
- **Can actually die.** ~4 days of total neglect is fatal (a weekend just makes it sick). Death keeps a **generation** counter and your best age, and `--reset` hatches a fresh egg.
- **Learns your hours.** It builds a histogram of when you actually use Claude and sleeps during your quietest stretch — so being offline while *you* sleep doesn't count as neglect.
- **Daily feeding streak** 🔥 with bronze/silver/gold tiers, worn as a sparkle ✦ (7 days) or crown ♔ (30 days).
- **Three switchable skins:** `buddy` (terracotta), `cat` (cyan), `slime` (magenta) — emotion lives in the eyes, which are colored independently of the body.
- **Animated `--watch` mode** and a **catch-the-crumbs mini-game** (`--play`) that boosts happiness.

## Install

```bash
# add this repo as a plugin marketplace, then install
claude plugin marketplace add adriancisnerosspeed-hub/clawde
claude plugin install clawde
```

Then wire it into your status line (one time):

```
/clawde:setup
```

That copies the engine to a stable path, installs a `claude-pet` terminal command, and (with your OK) adds the status-line block to your `~/.claude/settings.json`.

### Manual setup (if you'd rather not use the command)

Add to `~/.claude/settings.json` (merge — don't clobber existing keys):

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/clawde/claude_pet.py --statusline",
    "refreshInterval": 1
  }
}
```

…where `~/.claude/clawde/claude_pet.py` is a copy of this plugin's `bin/claude_pet.py`. Drop `refreshInterval` if you only want it to update per message.

## Commands

| In Claude Code | What it does |
|---|---|
| `/clawde:setup` | Wire Clawde into your status line (one-time) |
| `/clawde:pet` | Show the full Tamagotchi box |
| `/clawde:skin <buddy\|cat\|slime>` | Switch the critter |

In a **real terminal** (these take over the screen, so run them in Terminal/iTerm, not Claude Code's `!` shell):

```bash
claude-pet                 # the full box (read-only)
claude-pet --watch         # live animation: breathe, blink, yawn, bounce, sleep
claude-pet --play          # catch-the-crumbs mini-game (boosts happiness)
claude-pet --skin slime    # switch skin
claude-pet --hatch         # play the egg → baby hatch animation
claude-pet --reset         # hatch a fresh egg (new generation)
```

## How it stays out of your way

- If the engine ever errors, the status line just shows nothing extra — it never breaks your bar.
- State lives in `~/.claude/clawde/pet.json` (or wherever you point the command). Writes are atomic and safe across multiple sessions + the once-a-second status line.

## Requirements

- `python3` on your PATH (standard library only — no pip installs).
- A terminal that renders 256-color ANSI (basically all modern ones). `--watch`/`--play` use a real TTY.

## Development

```bash
python3 bin/claude_pet.py --selftest   # 13 tests: rules, sleep, streak, render alignment, mini-game
```

The rules and rendering are pure functions; the self-test sweeps ~29k rendered lines across every skin/stage/mood/animation frame to guarantee nothing misaligns.

## Notes

Community project — a fun thing, not affiliated with or endorsed by Anthropic.

## License

MIT © 2026 Adrian Cisneros
