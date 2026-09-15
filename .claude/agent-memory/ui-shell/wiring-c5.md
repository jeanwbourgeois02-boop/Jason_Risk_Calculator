---
name: wiring-c5
description: ui/app.py assembly pattern for the four-tab layout (Task C5, 2026-09-15) and a stale test file outside ui-shell scope that breaks full-suite runs
metadata:
  type: project
---

`ui/app.py` assembles `ui/tabs/{header,cash_ladder,blotter,market_data,reconciliation}.py`,
each exposing `build_layout(default_date)` / `register_callbacks(app, get_db_path)`
(header also has a no-arg `layout()`). `VISIBLE_TABS = ["Ladder", "Blotter",
"Market data", "Reconciliation"]`. `reconciliation.register_callbacks` registers
`ui/workbook_rates` internally now — do not also call `workbook_rates.layout`/`register`
at the top level in `ui/app.py`, or Dash raises on duplicate ids.

The header's as-of store (`header.AS_OF_STORE_ID`) is mirrored from the Ladder tab's
own date picker (`cash_ladder.DATE_PICKER_ID`) via a small `app.callback(...)` added
directly in `create_app()` — there is no dedicated cross-tab store module.

**Why:** [[app-structure]] (old memory) describes the retired six-tab / summary()
placeholder layout; that structure no longer exists after `docs/BUILD_PLAN.md`
2026-09-15 replaced it with one valuation + four tabs + header. Trust BUILD_PLAN
section 5/6 over the old CLAUDE.md "Six tabs as views" table when they conflict —
BUILD_PLAN explicitly supersedes it.

**How to apply:** when re-wiring `ui/app.py`, check each tab module's actual
`build_layout`/`register_callbacks` signature first (they can drift, e.g. `trades`
gained a `theme` column making old 13-value INSERT fixtures fail with "14 columns but
13 values supplied" — check `data/ingest/schema.py` column count before trusting an
old test fixture literal).

**Known stray breakage (not ui-shell's to fix):** `tests/test_cash_ladder_parity.py`
(not `tests/test_ui.py`, so outside this agent's owned test file) imports
`ui.tabs.cash_ladder.valuation_table`, which no longer exists after the C1 ladder
rewrite (workbook mark-to-market panel removed from that tab). This blocks
`py -3 -m pytest tests/ -q` (collection error) until whichever agent owns that test
file either deletes it or ports it to the new reconciliation-tab location. Reported to
housekeeper/C1, not edited (outside ui-shell's directory/file allowlist).

**Concurrent-edit hazard (2026-09-15):** another agent was actively mid-editing
`ui/tabs/blotter.py` (a file this agent does not own) *during* this session --
`build_layout` had already dropped the toolbar dropdown filter IDs (moved to native
DataTable header filters) while `register_callbacks` still referenced the old IDs,
so `py -3 -m pytest tests/` failed with `NameError: STATUS_FILTER_ID` partway through
that agent's own edit. A `git stash` taken mid-edit will scoop up *their* uncommitted
file too and can produce a confusing 3-way merge on `git stash pop`. **How to apply:**
if you need to stash to compare against HEAD, `git stash push -- <only your files>`
(or check `git status` first and stash foreign dirty files separately) rather than a
bare `git stash`; and if a full-suite run shows a `NameError`/`AttributeError` inside a
file you don't own, re-run a few minutes later before reporting it as broken -- it may
just be another agent still mid-edit. `tests/test_ui_blotter.py` failures on
2026-09-15 (`test_detail_table_*`, `test_subtotal_line_*`) were this in-progress state,
not something introduced by this agent's exposure.py/cash_ladder.py changes.
