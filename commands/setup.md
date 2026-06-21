---
description: Wire Clawde into your Claude Code status line (one-time setup)
---

Set up the **Clawde** pet so it appears in the user's status line. Do this carefully with your tools (Bash / Read / Edit):

1. **Find the bundled engine.** It ships at `${CLAUDE_PLUGIN_ROOT}/bin/claude_pet.py`. If that variable isn't resolved in your context, locate it:
   `find ~/.claude/plugins -name claude_pet.py 2>/dev/null | head -1`

2. **Copy it to a stable path** so the status line keeps working across plugin updates:
   ```
   mkdir -p ~/.claude/clawde
   cp <engine-path> ~/.claude/clawde/claude_pet.py
   ```

3. **Install the `claude-pet` terminal command** (for `--watch`, `--play`, `--skin`). Copy `${CLAUDE_PLUGIN_ROOT}/bin/claude-pet` to a directory on PATH, or create a tiny wrapper, then make it executable:
   ```
   printf '#!/bin/sh\nexec python3 "$HOME/.claude/clawde/claude_pet.py" "$@"\n' > ~/.local/bin/claude-pet
   chmod +x ~/.local/bin/claude-pet
   ```
   (Use `/usr/local/bin` if `~/.local/bin` isn't on their PATH; mention adding it to PATH if needed.)

4. **Add the status line** to `~/.claude/settings.json`. ⚠️ Read the file first and MERGE — do not clobber other keys. If a `statusLine` already exists, show it to the user and ask before replacing it. The block to add:
   ```json
   "statusLine": {
     "type": "command",
     "command": "python3 ~/.claude/clawde/claude_pet.py --statusline",
     "refreshInterval": 1
   }
   ```
   (`refreshInterval: 1` gives the gentle blink/animation; it re-runs the status line every second. The user can drop that line if they'd rather it only update per message.)

5. **Confirm** by running `python3 ~/.claude/clawde/claude_pet.py` and showing the pet box. Tell the user: the status line pet appears on the next message; `claude-pet --watch` (live animation) and `claude-pet --play` (mini-game) run in a real terminal; switch looks with `claude-pet --skin buddy|cat|slime`.
