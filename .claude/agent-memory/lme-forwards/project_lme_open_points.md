---
name: lme-open-points
description: LME forwards open points as of 2026-09-24: unverified prompt rules, ticker guesses, freeze-date question, calendar coverage
metadata:
  type: project
---

State when engine/lme/ was first built (2026-09-24, Phase 5). P&L rule approved by the user that day (CLAUDE.md "P&L conventions -> LME forwards"); pnl-valuation wires it, not this lane.

- Prompt rules were written from memory: 3M month-end clamp, holiday roll of weekly / third-Wednesday prompts, 6M end-of-weekly-zone roll, and whether US holidays are LME non-prompt days are all `# unverified`. Check against the LME's published prompt-date guide before trusting edge cases.
- Bloomberg tickers are guesses: cash 'LM<code>DY Comdty', 3M 'LM<code>DS03 Comdty', monthly dated 'LPV6 Comdty' on contract-master's bbg_root. Generics (LP1) deliberately avoided: their roll rule is unknown.
- Raised to the user: the approved freeze "last cash price on or before the prompt date" reads a cash price that is for prompt+2; the alternative is the cash price of the day the prompt was cash (prompt - 2 LME bd). Awaiting answer.
- config/calendars/LME.txt covers only 2026-2027, but the 27-month pillar list reaches 2028-12; requested an extension from exchange-calendars.

**Why:** these are the places a wrong LME date or ticker would silently mis-mark a ticket.
**How to apply:** before changing prompt logic or tickers, check whether the Bloomberg check or the user has since settled any of these; update this note when they do.
