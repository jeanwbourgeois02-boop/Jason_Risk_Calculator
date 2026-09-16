---
name: diagnostics-tool-already-built
description: tools/bbg_diagnostics.py + data/bloomberg/bbg_diagnostics.py already implement run_bloomberg_diagnostics() and are wired live into ui/tabs/header.py's "Check Bloomberg connection" button
metadata:
  type: project
---

As of 2026-09-16, a previous bbg-diagnostics session (see the fuller note
`bbg-diagnostics-2026-09-16.md`) already built `tools/bbg_diagnostics.py::run_bloomberg_diagnostics(db_path=None, as_of=None, host=None, port=None) -> list[{"name","status","message"}]`
covering: session connectivity, official-source mapping vs CLAUDE.md, marks_official never
resolving to BNP_BVAL/BBG_INTERP, FX spot/forward coverage via `mark_inventory`, futures
coverage, IRS/OIS curve coverage, `snapped_at` offset-awareness, last live-feed pull status.
`data/bloomberg/bbg_diagnostics.py` now re-exports it (the handoff item from that note is
done — confirmed present and importable 2026-09-16), and `ui/tabs/header.py` picks it up for
the "Check Bloomberg connection" button. Confirmed working live: `py -3 tools/bbg_diagnostics.py`
runs clean against the real dev DB with no terminal present, correctly reports fail/pass/warning
per check with no raw tracebacks.

**How to apply**: don't rebuild this tool. If asked to design a diagnostics-panel result shape,
this is already the established one — reuse `run_bloomberg_diagnostics` output shape
(`name`/`status`/`message`, status in `pass|fail|warning`) rather than inventing a new one.
Extend `tools/bbg_diagnostics.py` (bbg-diagnostics-owned per that note, though it lives under
`tools/` not `data/bloomberg/`) rather than duplicating checks elsewhere.
