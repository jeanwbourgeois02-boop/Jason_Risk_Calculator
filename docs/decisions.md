# Decisions log

Where the provenance of a rule lives: the date, who decided, and the words used. CLAUDE.md
states the rule; this file says why and when. New entries go at the top. The infra agent
moves a dated chat citation out of a code comment into a line here and leaves the plain
rationale in the code.

Format: `- YYYY-MM-DD  <rule in one line>  ("<the words that decided it>", <where it now lives>)`

- 2026-09-22  An infra agent owns code robustness and cleanliness: health audit, golden book,
  ruff hook; it never touches P&L arithmetic or CLAUDE.md. (user: "I want to turn this into
  a infra agent, and its responsible to for code robustness and cleanliness"; `.claude/agents/infra.md`)
- 2026-09-22  One line ending in the repository, LF, CRLF only for `*.cmd` / `*.bat` / `*.ps1`.
  (`.gitattributes`, one normalisation commit)
