---
name: hung-terminal-one-ledger-2026-10-01
description: 2026-10-01 user-approved follow-ups of 1c7d30b - contract dates and LME counted in _NetLog and skipped once Bloomberg stopped answering; the pull's ledger call left to the auto-backfill (LEDGER_AT_END) with LiveFeed's fallback
metadata:
  type: project
---

User yes 2026-10-01 to the two items proposed in [[pull-speed-2026-10-01]].

**Hung Terminal.** `contract_dates_step(net=)` and `_lme_step(net=)` count each request in the
press's `_NetLog` (ok / failed / `timed_out`) and send nothing once `net.gave_up`: block
"skipped" = `stopped_answering()`, tickers / pillars listed with "not asked: ..." (never put in
the contract-dates empty state). `request_lme_pillars` never raises on a timeout: every ticker
comes back error "TIMEOUT" (`_lme_timed_out`). The old post-hoc net.ok() for contract dates and
LME in pull_once is gone (it reset the counter after an LME timeout). Contract dates run FIRST,
so their own check can only trip on a later field-set request; in practice what changes is that
their timeouts now count, so a hung Terminal costs contract dates + one spot request, and
futures + LME are not sent (harness "hung": 4 requests -> 2).

**One ledger call per press.** `pull_once(ledger_by_backfill=False)`; LiveFeed passes
`backfill.LEDGER_AT_END`. True + a session -> step "skipped" LEDGER_BY_BACKFILL, status["ledger"]
= `deferred_ledger_block` (ledger_block keys empty + skipped + deferred). LiveFeed._backfill
captures start_auto_backfill's return in both branches; None or a raise -> `_ledger_now` makes
the call and REPLACES status["ledger"] (not patch_status: it merges, the deferred keys would
stay) and rewrites the ledger step's outcome. No-session press, CLI, tests: the pull's own call.
backfill's `_closing_step_needed` treats "skipped" in the block as "run the closing step".

**How to apply:** harness = scratchpad harness2.py pattern (modes ok / timeout / boom / hung /
lmeto, arg `lbb` for ledger_by_backfill). Tests that assert a ledger step "ok" through LiveFeed,
or LME / contract-dates outcomes under timeouts, need re-pinning when the suite runs.
