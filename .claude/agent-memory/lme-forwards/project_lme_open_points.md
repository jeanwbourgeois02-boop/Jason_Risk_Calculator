---
name: lme-open-points
description: LME forwards open points: unverified prompt rules, ticker guesses, calendar coverage; the freeze-date rule settled 2026-09-28 (C14)
metadata:
  type: project
---

State of engine/lme/ (first built 2026-09-24, Phase 5). P&L rule approved by the user that day (CLAUDE.md "P&L conventions -> LME forwards"); pnl-valuation wires it, not this lane.

- Prompt rules were written from memory: 3M month-end clamp, holiday roll of weekly / third-Wednesday prompts, 6M end-of-weekly-zone roll, and whether US holidays are LME non-prompt days are all `# unverified`. Check against the LME's published prompt-date guide before trusting edge cases.
- Bloomberg tickers are guesses: cash 'LM<code>DY Comdty', 3M 'LM<code>DS03 Comdty', monthly dated 'LPV6 Comdty' on contract-master's bbg_root. Generics (LP1) deliberately avoided: their roll rule is unknown.
- **Freeze date settled 2026-09-28 (user yes, C14):** a ticket freezes at the cash price of the day its prompt became cash (prompt - 2 LME business days, `prompts.freeze_date`), last on or before that day, never one quoted after it. `curve.settlement_price` applies this internally and still takes the prompt date from its callers (ledger `_lme_freeze`, valuation `_frozen_row`), returning (value, price date, source): the price date is now the freeze date, so the ledger's "(last before settlement)" note text always fires; pnl-ledger may want to reword it.
- config/calendars/LME.txt covers only 2026-2027, but the 27-month pillar list reaches 2028-12; requested an extension from exchange-calendars.
- The golden book (as-of 2026-08-14) holds no settled LME ticket (sample prompts Sep / Dec 2026), so a freeze-rule change does not move it.

**Why:** these are the places a wrong LME date or ticker would silently mis-mark a ticket.
**How to apply:** before changing prompt logic or tickers, check whether the Bloomberg check or the user has since settled any of these; update this note when they do.
