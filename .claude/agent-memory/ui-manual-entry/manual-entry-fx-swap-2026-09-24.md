---
name: manual-entry-fx-swap-2026-09-24
description: Manual entry sub-tab's FX swap choice (commodity conversion Phase 3) - field layout, toggle callbacks, list package column, both-dates delete wording, test recipe
metadata:
  type: project
---

On 2026-09-24 the user took the recommendation to book FX swaps (hedge rolls) on the Manual entry screen; ingest-booking added `manual.book_fx_swap` (two MANUAL FX_SWAP trades, one package) the same day. Macro products (IRS, NDF, EQ_OPTION) are gone from the app; the form offers FX option, FX forward / spot, FX swap only.

Layout choices in `ui/tabs/manual_entry.py`:
- FORWARD_FIELDS_ID now holds the shared Amount field plus two inner groups: OUTRIGHT_FIELDS_ID (rate, value date) and SWAP_FIELDS_ID (near date, near rate, far date, far rate). Inner groups use `display: contents` when shown so their fields flow in the parent `.toolbar` flex row.
- Two callbacks on the product dropdown: `_toggle_fields` (unchanged outputs, FX_SWAP shows the amount group) and `_toggle_swap_fields` (outright vs swap inner group). Kept separate so the old toggle's return shape stayed identical.
- `_book` takes the four swap States last, with defaults None, so the old positional test calls still work.
- Blank swap fields are caught in the UI ("Not booked: the near rate and far date are blank.") because `_number(None)` in ingest reads "must be a number, got None". Every other check (far after near, rates > 0) is ingest's message, shown after "Not booked: ".
- The package id for the status line is read back from the DB, never rebuilt (ingest's `'SWAP-' + min(id)` is a string min: MANUAL-9 / MANUAL-10 gives SWAP-MANUAL-10).
- List: a Package column ('' for a trade that is its own package), Terms "Near date" / "Far date" by the earlier settle date within the package; delete dropdown labels a swap trade "(swap SWAP-..: both dates go)"; delete status names every id `delete_manual_trade` returned.

**Why:** CLAUDE.md "Commodity conversion plan" Phase 3; housekeeper brief 2026-09-24.
**How to apply:** a new product on the form = a new inner group + toggle branch; keep the old toggle tuple stable, append new States with defaults. Big test blocks: use the Edit tool, not bash heredocs (quotes break them).
