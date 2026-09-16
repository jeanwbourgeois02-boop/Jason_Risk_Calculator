# Bloomberg diagnostics module — 2026-09-16

## What exists already (found before building anything)

`data/bloomberg/` (bbg-data) already has substantial diagnostic-adjacent code:
- `data/bloomberg/live.py::availability(host, port)` — blpapi import + TCP probe, no session.
- `data/bloomberg/live.py::read_status/write_status` — last live-feed pull result JSON
  next to the DB (`<db>.bloomberg_status.json`).
- `data/bloomberg/inventory.py::mark_inventory(conn, as_of)` — OFFICIAL/INTERP/MANUAL/MISSING
  status per (instrument, settle_date, mark_type) the book actually needs today, for FX +
  futures. This is the right building block for coverage checks — reused rather than
  reimplemented.
- `data/bloomberg/diagnose.py` — pretty-prints a `pull_marks.py` `.diag.json`; a post-hoc
  report reader, not a live check, and not in the `run_bloomberg_diagnostics()` shape.
- `tools/bloomberg_diagnostic.py` — a pre-existing standalone (dependency-light, portable to
  the Bloomberg machine) script with its own session/spot/forward checks. Read-only, prints
  a report to `reports/`. Not wired to the UI button and not in the `list[{name,status,
  message}]` shape. Left untouched (not mine to edit — provenance unclear, and it serves a
  different purpose: a portable pre-flight script vs. a UI-triggered structured report).

## ui-shell's expected interface (already wired, waiting on the module)

`ui/tabs/header.py` (`_bbg_diagnostics_entry_point`) already lazily imports
`data.bloomberg.bbg_diagnostics.run_bloomberg_diagnostics` and falls back to a placeholder
if that import fails. Exact contract, already in the UI code as a comment block:

```
data.bloomberg.bbg_diagnostics.run_bloomberg_diagnostics() -> list[dict]
# each dict: {"name": str, "status": "pass"|"fail"|"warning", "message": str}
```

## What I built

`tools/bbg_diagnostics.py` — a full `run_bloomberg_diagnostics(db_path=None, as_of=None,
host=None, port=None) -> list[dict]` matching that shape exactly, plus a CLI
(`py -3 tools/bbg_diagnostics.py [--db ...] [--as-of ...] [--json]`). Read-only throughout
(opens the DB with `mode=ro`, never writes marks/curves).

Checks, in order:
1. Session connectivity (`data.bloomberg.live.availability`).
2. Official-source mapping: `data.ingest.schema.OFFICIAL_MARK_SOURCE` diffed against the
   CLAUDE.md table (SPOT/FWD_OUTRIGHT→BBG_BFXFORWARD, FUTURE_PX→BBG_BDH, PAR_RATE/PV_USD/
   DV01_USD→QL_PRICER, DELTA/PREMIUM→MANUAL), plus a DB query proving `marks_official` never
   serves BNP_BVAL or BBG_INTERP as if official (structurally shouldn't happen given the
   view's CASE expression, but checked directly rather than assumed).
3/4. FX and futures coverage via `mark_inventory` — MISSING marks reported as fail with the
   exact instrument/settle_date list; FWD_OUTRIGHT-only-via-BBG_INTERP reported as a warning
   (fallback used, not officially sourced).
5. IRS/OIS curve coverage: for every currency with a live IRS trade, checks `curve_quotes`
   has today's rows for the Phase-1 OIS index (USD/EUR/GBP/JPY/CHF/CAD/AUD only — flags
   anything else as a scope warning), and that PAR_RATE/PV_USD/DV01_USD resolve to QL_PRICER
   in `marks_official`, never BBG_BDH.
6. `snapped_at` offset: every distinct `snapped_at` for official marks on the as-of date must
   parse as offset-aware (CLAUDE.md's 17:00 America/New_York resolved-offset requirement);
   naive timestamps fail this check by name/example.
7. Last live-feed pull status (`data.bloomberg.live.read_status`).

Every check function catches its own exceptions so one broken check never blocks the rest;
`run_bloomberg_diagnostics` itself never raises.

Verified by running `py -3 tools/bbg_diagnostics.py` against the repo's real dev DB with no
Bloomberg terminal present: correctly reports session=fail (no blpapi), official-source
mapping=pass, FX/futures marks=fail with a full missing-instrument list, IRS=pass (no IRS
trades), snapped_at=warning (nothing to check yet), last pull=fail (no terminal). No
tracebacks surfaced.

## Handoff needed (I did not make this edit — data/bloomberg/ is bbg-data's directory)

To make `ui/tabs/header.py`'s existing lazy import succeed, bbg-data needs to add exactly
one new file, `data/bloomberg/bbg_diagnostics.py`, containing:

```python
from tools.bbg_diagnostics import run_bloomberg_diagnostics  # noqa: F401
```

(or copy the implementation in if the team prefers `data/bloomberg/` to have no dependency
on `tools/` — either way, `tools/bbg_diagnostics.py` is the source of truth for the checks
and should not be duplicated by hand). No other change is required; the UI side is already
complete and will pick up the real module automatically once the import succeeds.

## Open items / things not verified live

- Never run against a real, connected Bloomberg terminal — the missing/fail results above
  are expected on this dev machine, not evidence of a bug in the checks themselves. Re-run
  once on the Bloomberg machine to confirm the "pass" paths actually fire.
- `tools/bloomberg_diagnostic.py`'s FWD_CURVE-table parsing (`points_from_rows`,
  `outright_for_date`) is a good candidate to compare against `data/bloomberg/fwd_curve.py`
  for drift, but that's bbg-data's ownership, not mine to touch.
