---
description: Switch your Clawde pet's skin — buddy, cat, or slime
---

The user wants to change their Clawde pet's appearance to: **$ARGUMENTS**

Valid skins are `buddy` (terracotta, white eyes), `cat` (cyan, yellow-green eyes), and `slime` (magenta, glowing cyan eyes).

- If `$ARGUMENTS` is one of those, run via Bash: `python3 ~/.claude/clawde/claude_pet.py --skin $ARGUMENTS` and show the resulting pet box.
- If `$ARGUMENTS` is empty or not a valid skin, list the three options and ask the user which they'd like.

(If `~/.claude/clawde/claude_pet.py` doesn't exist, suggest running `/clawde:setup` first.)
