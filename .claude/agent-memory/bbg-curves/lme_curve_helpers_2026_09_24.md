---
name: lme-curve-helpers-2026-09-24
description: LME forward curve helpers in fwd_curve.py (Phase 5): source rules for cash / 3M / monthly pillars, unverified prompt-date field, who calls them
metadata:
  type: project
---

Phase 5 (2026-09-24): `fwd_curve.py` gained `request_lme_pillars`, `lme_curve_marks`, `lme_history_marks`
(signatures fixed by the housekeeper; bbg-live and bbg-backfill call them, bbg-ticker-check reads the field
constant). Pillars come from lme-forwards' `engine.lme.lme_curve_tickers`, trimmed by bbg-library's
`lme_curve_pillars`.

Source rules chosen (CLAUDE.md "Official marks" FWD_OUTRIGHT row applied to LME):
- CASH -> SPOT keyed settle_date = as_of, BBG_BFXFORWARD live and on a past close (Bloomberg's own quote,
  the official SPOT source; a SPOT is never interpolated). On the curve it sits at `engine.lme.cash_date`,
  matching `engine/lme/curve.py::day_curve`, whatever date Bloomberg sends.
- 3M / MONTHLY with Bloomberg's delivery date -> BBG_BFXFORWARD at Bloomberg's date (a reason when it differs
  from our computed one); with no date (always so in history) -> BBG_INTERP at our computed date.
- Open prompt: linear in calendar days between available pillars (a missing pillar is just skipped, the
  neighbours bracket); before cash date = cash price as BBG_INTERP; beyond last pillar = no mark + reason.

**Why:** "Bloomberg's own quote at Bloomberg's own date" is the FX rule for BBG_BFXFORWARD (backfill own_dates).
**How to apply:** `LME_PROMPT_DATE_FIELD = "FUT_DLV_DT_LAST"` and every LME ticker are unverified; if the
terminal shows the field means something else, only that constant changes (a Changed interface for
bbg-live, bbg-backfill, bbg-ticker-check). Considered but not built: a tolerance guard rejecting a Bloomberg
date far from ours (a wrong field would otherwise move a pillar to a wrong date).
