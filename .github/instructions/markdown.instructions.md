---
applyTo:
  - "**/*.md"
  - "**/*.mdx"
  - "**/*.qmd"
---

## Markdown file rules

- Never use em dashes. Use a comma, period, colon or parentheses instead.
- Write full sentences. Do not join fragments with commas.
- Do not use "not X, but Y" or "not just X" contrasts. State the point directly.
- Do not end a sentence with a colon and a noun-phrase fragment, such as "X: a direction, not a task". Write a full sentence.
- Put each sentence on its own line. Do not wrap lines at a fixed column.
- Skills and agent-only instruction files may wrap at a column, because that shows their token cost.
- Use bold, italics and emojis sparingly.
- Check `zensical.toml` before you suggest formatting or structure under `docs/`.

Two hooks in `.claude/settings.json` (`PostToolUse` on `Write|Edit`) enforce these rules.
The first hook blocks any em dash in the file.
The second hook checks new prose for comma fragments, contrasts and colon fragments.
Both hooks were tested against real violations and block them.

## Writing style

Write in the ASD-STE100 (Simplified Technical English) style.
The document type sets how strict the style is.

Procedures follow ASD-STE100 strictly.
Procedures are guides, runbooks, troubleshooting steps, quickstarts and instruction files for agents.
Instruction files include this file, `AGENTS.md` and every file in `.github/instructions/`.

- Write at most 20 words in an instruction and at most 25 words in a description.
- Write one instruction in each sentence.
- Use active voice. Use the imperative for instructions.
- Use one word for one meaning. Use the same term for the same thing every time.
- Write noun clusters of at most three words.
- Remove filler and buzzwords.

Explanations are ADRs, architecture pages and design pages.
They follow the same rules, except the word limits.
Use a longer sentence when it carries a reason or a trade-off.

## Documentation content rules

- Do not write install commands for external tools, such as `brew install mkcert`. Name the tool and link to its install instructions.
- State a tool version only when SRDP depends on it.
- Put a setup step that SRDP needs after an install in a `just` recipe, such as `mkcert -install`. Let the docs point to that recipe.

Install commands go out of date, and each OS variant adds maintenance.
