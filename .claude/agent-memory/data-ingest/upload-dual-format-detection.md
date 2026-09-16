---
name: upload-dual-format-detection
description: data/ingest/upload.py now accepts BNP snapshot or blotter CSV via column-signature detect_format(); import_blotter has no idempotent re-upload mode yet
metadata:
  type: project
---

`data/ingest/upload.py` gained `detect_format(payload, filename, sheet=None) -> 'BNP'|'BLOTTER'`
and `import_blotter(payload, filename, db_path, sheet=None)` alongside the existing
`import_report` (BNP-only, signature unchanged, still the only entry point `2_launcher.py`
and `tests/test_upload.py` import by that exact name). Detection is column-signature only,
never filename: BNP requires the full `BNP_REQUIRED` set (renamed from `REQUIRED`, which is
kept as a back-compat alias since nothing else imports the old name); BLOTTER requires
`{'Status','Fund','Fin Type','Trade Id','Symbol'}` — `Fin Type`/`Trade Id` never appear in
the BNP file (which has `Financial Type` and no `Trade Id`), so the two signatures cannot
both match. Frame parsing itself was factored out into `_parse_frame` so `detect_format`
doesn't duplicate `read_report`'s CSV/Excel logic.

`import_blotter` has no `as_of` param (blotter rows carry their own dates) and produces no
`positions` rows (CURRENCY rows in the blotter are settlement-level cash movements, not an
EOD snapshot — see [[blotter-source-quirks]]). It reuses `import_report`'s stage-then-publish
safety pattern (in-memory copy backed from a read-only live snapshot, `schema.TABLES` generic
copy loop, `swaps.package_swaps(live)` at the end since blotter FORWARD rows are inserted as
`product='FX_FWD'`/`package_id=trade_id` exactly like BNP's).

`blotter.load()` still has no idempotent skip-on-duplicate mode (plain `INSERT`, documented in
its own docstring) — a re-upload of the same file raises `sqlite3.IntegrityError` deep inside
`blotter.load`, not a `ValueError`. `import_blotter` catches that specifically and re-raises as
`ValueError('Nothing imported. Duplicate key on re-upload: ...')` so the UI never sees a raw
traceback. If a proper idempotent (skip-vs-reject-vs-conflict) mode is ever wanted for the
blotter, mirror `bnp.load`'s `on_duplicate='skip'` design (see [[idempotent-loader]]) rather
than reinventing it.

`data/ingest/xlsx_futures.py` (the old workbook futures-fill loader) was deleted 2026-09-16 —
confirmed orphaned (only caller was the since-deleted `ui/tabs/reconciliation.py`). Futures
fills now come from the blotter's FUTURE rows instead.
