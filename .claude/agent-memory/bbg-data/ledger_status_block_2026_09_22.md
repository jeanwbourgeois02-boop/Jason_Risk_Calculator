---
name: ledger-status-block-2026-09-22
description: The pull status's ledger block (live.ledger_block): refrozen/kept/refrozen_count/refrozen_summary carried from realise_settled; backfill's two-call ordering (after_last_day then closing) and the merge rule in _record_ledger; lane constraints when backfill.py is shared
metadata:
  type: project
---

`live.ledger_block(led, as_of)` is the one normaliser for every `realise_settled` result
(live pull `status["ledger"]`, each backfill day's flat keys, the backfill's published
`backfill.ledger` block, `snapshot.import_snapshot()["ledger"]`). Bare ids under
`refrozen` become `{"trade_id": id}`; a missing `kept` is `[]`; a non-dict result still
gives the shape. Sentence: `refrozen_summary(n)` = "N settled trade(s) re-frozen at the close".

**Why:** user yes 2026-09-22, the pull status records what the ledger's re-freeze did;
pnl-engine changed `refrozen` to dicts and added `kept` in parallel, so the reader had to
tolerate both shapes on the same day.

**How to apply / traps:**
- In `auto_backfill` mode the ledger is NOT run per day (`order is not None`): the
  re-freeze at a past close lands in `backfill()`'s single call after the last worked day
  (`span_end`), and `_realise_after_backfill` (today) then finds nothing left. So the
  published block must merge both: `_record_ledger(db, "after_last_day" | "closing", block)`.
  after_last_day starts the block afresh; closing merges unless a closing is already in
  `steps` (a run with no due days makes no after_last_day call). Getting this backwards
  drops the re-freeze count from the status.
- `patch_status` merges one level under the key, so patching `{"ledger": ...}` under
  "backfill" never clobbers the other backfill keys; `_published[key]["ledger"]` is set too
  so the `_run` finally-publish keeps it.
- NO_CLOSES / ERROR / SKIPPED day results still lack the refrozen/kept keys (outside the
  hunks I was allowed on 2026-09-22): readers must `.get`.
- When another session's agents hold backfill.py, the coordinator names exact hunks: edit
  by Edit only, re-read before each edit, append tests at the end of shared test files.
