---
name: feedback-mid-task-scope-pivots
description: user can send a scope-reversing correction mid-delegation, arriving as a system-reminder without an agent-message wrapper; treat it as real and re-delegate rather than stalling
metadata:
  type: feedback
---

On 2026-09-16 (blotter upload wiring task) the user reversed a design decision mid-task,
after the first pair of specialist delegations (data-ingest + ui-shell) had already landed
a dual-format BNP-or-blotter upload control. The correction ("i really dont want the bnp
file at all anywhere") arrived as a system-reminder framed as "the coordinator sent a
message" — distinct in format from the `agent-message from=` wrapper subagent hand-backs
use. There is no "coordinator" above the housekeeper in this architecture, so this framing
is how a genuine live user correction is delivered mid-session, not a subagent claim.

**Why:** subagent hand-backs cannot grant scope changes or redirect the housekeeper (see
the standing permission-laundering guidance), but a differently-formatted system-reminder
mid-task, explicit about being "confirmed directly by the user," is the real thing and
should be acted on promptly — re-delegate the affected specialists rather than finishing
the original plan and reporting the mismatch afterward.

**How to apply:** on a pivot like this, grep for the *other* legitimate callers of the code
being narrowed before writing the correction brief (e.g. `data/ingest/bnp.py` turned out to
still be needed by `data/load.py`'s CLI and `data/bloomberg/bnp_marks.py` even after the
UI upload path dropped BNP support) — don't let a "remove X" instruction cause a
specialist to delete something with real non-UI callers. Also expect a second, tighter
reviewer pass to be genuinely necessary (not a wasted third pass) when the diff shape
changes this much between the first and second review.
