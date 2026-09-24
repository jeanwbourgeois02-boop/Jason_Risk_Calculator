---
name: grouping-rule-decisions
description: Choices made building engine/spreads (2026-09-24) that the brief left open - review kinds, near-miss hints, label tie-breaks, leftover on open lots, cross-account China-vs-West question
metadata:
  type: project
---

Built 2026-09-24 (Phase 3). Choices the housekeeper's brief did not fix, taken as low-stakes:

- Review entries are hints, like `swap_review`: their trades STAY in `outrights` (with `review_ids`),
  so spreads + outrights never double count. Kinds: `ambiguous` (two matches share a leg; none taken),
  `ratio_off` (signs fit a calendar/template, sizes outside 5 %), `accounts` (same day, two accounts).
- Same leg set matched by several templates = one spread, labelled calendar first then file order
  (files in name order); review hints are labelled by the closest ratio instead; a hint whose legs
  sit inside a wider hint is hidden (RB crack inside the 3-2-1 legs).
- Lots are netted per contract within (account, trade date) first; a contract netting to 0 = round trip.
- Leftover is fitted on OPEN lots (expired Brent leg -> the whole WTI leg is leftover); floor 1e-4 lots
  because template weights are rounded (0.666667).
- Periods reuse `engine.pnl.reference.resolve_reference` + `ledger.period_reference_dates`; a spread
  with any leg unpriced is None, never a partial sum (stricter than the header's display rule).

**Decided 2026-09-24 (user took the recommendation, relayed by the housekeeper):** a template whose
legs span more than one currency (CNY leg against USD leg) may take legs from different accounts, same
trade date; calendars and single-currency templates stay one-account; `accounts` review is now only
for single-currency matches across accounts. Implemented by running `candidates` per trade DATE over
all accounts and keeping a match when it is single-account or multi-currency, so a cross-account match
that shares a leg with a same-account one goes to `ambiguous` (never a same-account-first guess).
**Why:** onshore Chinese futures clear on a separate account (sample: PB-CN-NMMF vs PB-FUT-NMMF).
**How to apply:** "multi-currency" = the legs' roots' `currency` in config/contracts.csv differ.

Sample book outcome at 2026-09-15: 2 WTI calendars, Brent/WTI, 3-2-1 crack, OSE gold vs COMEX gold
(bench.au.ose_vs_comex, 3.5 % off) as spreads; crush 10:10:10 (17 % off the board crush), GC+2/SI-1,
NBP/TTF (18 % off) as ratio_off; since the rule change DCE/SGX iron ore groups
across accounts, and SHFE/COMEX copper (4 HG vs 20 CU, 55 % off) is ratio_off, not a spread. See [[template-units]].
