---
applyTo:
  - "**/*.md"
  - "**/*.mdx"
  - "**/*.qmd"
---

## Markdown file rules

- No em dashes, ever. Not "sparingly", never. Use a comma, period, colon, or parentheses instead. This is enforced by a hook (`.claude/settings.json`, `PostToolUse` on `Write|Edit`) that blocks the write and reports the offending line.
- Write full sentences. Do not glue short fragments together with commas instead of periods.
- Avoid the "not X, but Y" / "not just X" contrastive-tic construction as a stylistic habit. Say the thing directly instead of setting up a contrast to knock down.
- Do not collapse a sentence into a colon followed by a noun-phrase fragment, for example "X: a direction, not a task, something that...". Write it as a full sentence instead. This construction is a distinctive AI-writing tell and reads as evasive even when the content is fine.
- Do not hard-wrap lines to a fixed column width. Write each sentence as its own line in the source; markdown collapses single newlines within a paragraph, so this only affects diffs, not rendering. Exception: skills and agent-only instruction files may wrap at a character column instead, since that helps a reader estimate token cost at a glance.
- Don't overuse bold, italics, or emojis.
- Check `zensical.toml` before suggesting markdown formatting or structure for anything under `docs/`.

A second `prompt`-type hook alongside the em-dash one checks new prose for the comma-fragment and contrastive-tic rules on every `Write`/`Edit` to a matching file. Both hooks were verified live, not just written: tested against a real violation and confirmed they actually block, not just exist in a config file.
