---
name: upload-dual-format-detection
description: data/ingest/upload.py is blotter-only and does a FULL REPLACE on every import_blotter call (2026-09-17) -- this file is now stale on the "dual format" and "no idempotent re-upload" points it used to describe; keep reading it for the still-accurate history.
metadata:
  type: project
---

**Superseded, 2026-09-17.** This file originally described a `detect_format`/`import_report`
(BNP) + `import_blotter` (blotter) dual-format upload path, and said `import_blotter` had no
idempotent re-upload mode. Both are gone:

- `data/ingest/upload.py` has exactly one import entry point, `import_blotter`. There is no
  `import_report`, no `detect_format`, no BNP branch anywhere in this file (BNP upload was
  removed from the app entirely per CLAUDE.md's "User-authorised direction, 2026-09-15" /
  "2026-09-17 — BNP removed entirely" note; `data/ingest/bnp.py` itself is untouched and still
  used as a library by `data/load.py`'s CLI, just unreachable from the app).
- `blotter.load()` (the library function) IS idempotent by `trade_id` now (upsert:
  `INSERT ... ON CONFLICT(trade_id) DO UPDATE`, dissolving any swap package containing a
  replaced trade) — this was added after this file was first written.

**Current behaviour, 2026-09-17 (user instruction: "when a new excel is put in - that's the
only input for the trades - all of the old stuff gets deleted - sample data and previous
excels - so there are no duplicates or fake things"):** `import_blotter` does a FULL REPLACE,
not a merge, and this is deliberately DIFFERENT from `blotter.load`'s own idempotent-by-id
behaviour:

- `_stage_and_publish(db_path, load_fn, full_replace=False)` gained the `full_replace` param.
  When `True`: before `load_fn` runs, `FULL_REPLACE_CHILD_TABLES + ("trades",)` =
  `("trade_legs", "realised_pnl", "swap_review", "trades")` are deleted from BOTH the staged
  in-memory snapshot (so `load_fn`'s own idempotent-merge logic doesn't repopulate the old
  rows from the snapshot) AND, after `load_fn` succeeds, from `live` too (so the generic
  per-table upsert-merge loop becomes a plain insert of exactly the new file's rows). Deleting
  from staged as well as live is the non-obvious part — deleting only from `live` looks
  correct but silently fails: the merge loop copies EVERY row in staged (old + new, since
  `blotter.load` only adds/updates, never deletes) back into `live`, undoing the delete. Caught
  by a test that reproduced it before the two-sided delete was added.
  FK-safe order matters: children (`trade_legs`, `realised_pnl`, `swap_review`, all
  `REFERENCES trades`) before `trades` itself, or the delete raises `IntegrityError` (schema.py
  enables `PRAGMA foreign_keys = ON`).
- `realised_pnl` and `swap_review` are NOT in `schema.TABLES` (the generic per-table merge
  loop never touches them) — deliberately left empty after a full-replace rather than
  re-populated from staged: `swaps.package_swaps(live)` (already called at the end of
  `_stage_and_publish`) rebuilds `swap_review` from scratch against the new book, and
  `realised_pnl` is populated later by the (not-mine) P&L ledger engine, not by upload.
- `import_blotter`'s returned message drops the old "(N new, M updated)" wording (that
  concept no longer applies — everything in a full-replace is "new" relative to the just-wiped
  table) and instead says `"Replaced the previous book: N trade(s) and M leg(s) removed."`
  when there was a previous book (omitted on a first-ever upload). `ui/uploads.py` renders
  this string verbatim, so this is the entire mechanism for the UI to show "replaced N trades".
- The launcher's sample loader (`2_launcher.py::cmd_load_sample`, NOT owned by data-ingest)
  calls this SAME `import_blotter` function, so it now also wipes-and-reloads on every call —
  a real risk if it's ever re-run after a user has uploaded their own file. It needs a guard
  ("only run when the DB has no trades yet") added by whoever owns `2_launcher.py`; reported,
  not fixed here (out of lane).

Only two callers of `import_blotter` exist in the whole repo: `ui/uploads.py` (the app upload
button) and `2_launcher.py::cmd_load_sample` (the sample data loader). `_stage_and_publish` is
private to `upload.py` and has no other caller, so its signature was safe to change freely.
