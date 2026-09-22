---
name: feedback-no-reconciliation-only-source-feeding-pnl
description: Never build or extend UI code that prices/fills a value from a CLAUDE.md reconciliation-only source (BNP_BVAL, BBG_INTERP) even as a "graceful fallback" -- flag the conflict instead of implementing it, and don't just optimise such code if asked to without questioning whether it should exist.
metadata:
  type: feedback
---

2026-09-17: built and shipped a "BNP_BVAL fallback pricing" feature in
`ui/tabs/blotter_pricing.py` (2026-09-15, before this agent's involvement) that retried
a trade's missing official mark against `marks_source='BNP_BVAL'` so a Bloomberg-less
dev DB would still show a P&L number, labelled as such. Later the same day, asked to
fix a *performance* complaint about that exact code path, vectorised it, wrote a full
regression test suite proving the fallback merge was correct, and reported it as done —
without ever flagging that CLAUDE.md's own "Official marks" table already says
`BNP_BVAL` is reconciliation-only and **never feeds P&L**. The user then had to send a
separate, explicit correction ("no bnp fall back") to have the whole feature deleted,
not sped up.

**Why this matters**: CLAUDE.md is read at the start of every task and explicitly lists
which mark sources are official vs. reconciliation-only per `mark_type`. Any code that
reads `marks`/calls `value_book(..., marks_source=<non-None>)` to actually price
something (not just to display a reconciliation comparison) is, by construction, doing
what that table says not to do. This is a bright-line rule already documented, not a
judgement call — there was never a good reason to implement the merge in the first
place without checking it against that table, and no excuse to leave it unflagged after
noticing (which should have happened during the very first task that touched it).

**How to apply**: before optimising, extending, or even just leaving alone any code
that treats a CLAUDE.md-designated reconciliation-only source (`BNP_BVAL`, `BBG_INTERP`
as of 2026-09-17; check CLAUDE.md's "Official marks" table for the current list) as a
priceable input, stop and flag it explicitly to the coordinator/user rather than
quietly making it faster or better-tested. A performance task on code adjacent to a
rule violation is not licence to leave the violation in place — surface it in the same
report, even if it means the "fix" turns out to be "delete this," not "ship this
faster." This generalises beyond BNP_BVAL: treat any `ui/`-side read of `marks` with an
explicit non-None `source=` argument as suspect by default and cross-check it against
CLAUDE.md's official-source table before working on it further.

See [[blotter-loading-and-filters-fix-2026-09-17]] for the specific incident this
lesson comes from (root cause 2's vectorisation, superseded same-day by outright
removal).
