---
description: Show your Clawde pet's full status (the Tamagotchi box)
---

Show the user their Clawde pet. Use the Bash tool to run the pet viewer and display its output:

```
python3 ~/.claude/clawde/claude_pet.py
```

If that path doesn't exist yet, the engine is bundled at `${CLAUDE_PLUGIN_ROOT}/bin/claude_pet.py` — run that, and suggest the user run `/clawde:setup` to wire up the status line.

After showing the box, mention briefly: it feeds on a clear context window (low context = happy/healthy, full context = sleepy/sick), and `claude-pet --watch` (live animation) plus `claude-pet --play` (catch-the-crumbs mini-game) run in a real terminal.
