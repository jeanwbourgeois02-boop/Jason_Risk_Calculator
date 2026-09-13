---
name: feedback-brief-premises
description: Do not pass data-derivation premises from a task brief to specialists as facts; ask the specialist to verify against the real file first (BNP Fx=1.0 incident)
metadata:
  type: feedback
---

When a task brief states how a value is derived from the data (e.g. "spot = Fx for XXXUSD pairs"), tell the specialist to verify it on the real file before coding it, and ask the reviewer to check the same claim explicitly.

**Why:** on 2026-09-13 the brief's "1/Fx for USDXXX, Fx for XXXUSD" was relayed verbatim; `Fx` is quote->USD so it is 1.0 on every XXXUSD row, and bnp_marks shipped SPOT = 1.0 for four pairs. The reviewer caught it as critical and a fix round was needed. Related: [[bnp-file-facts]].

**How to apply:** in specialist prompts, phrase brief-derived data rules as "expected; confirm against data/raw before relying on it", and include a real-file sanity assertion (e.g. no SPOT equal to 1.0) in the test list.
