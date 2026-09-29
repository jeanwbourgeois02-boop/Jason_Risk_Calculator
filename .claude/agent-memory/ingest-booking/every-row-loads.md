---
name: every-row-loads
description: Since 2026-09-29 (hard rule 6) no blotter row is rejected: UNRECOGNISED trades are written by the upload, their reason kept in upload_issues by trade_id and symbol across uploads, and reresolve_unrecognised rewrites them once the contract list knows them
metadata:
  type: project
---

User 2026-09-29: "all rows need to load thats non negotiable ... so we see what we need to fix".
Only a cancelled / void / deleted / rejected / failed row removes a trade; pending stays out.

What upload.py does (built 2026-09-29, verified on the committed parser + a stub, since the parser
was mid-edit):
- `_write_unrecognised` (inside the staged load) makes sure each UNRECOGNISED trade has its
  'UNRECOGNISED:<SYMBOL>' instrument (INSERT OR IGNORE: the parser's row wins), its trade row and
  no leg. `_file_trades(result)` = result.trades + any trade listed only on result.unrecognised.
- `upload_issues` gained `trade_id` (ALTERed in: schema's `_migrate_columns` does not reach this
  lane's tables). Kind UNRECOGNISED (`NEED_FIX_KIND`) carries the symbol AS WRITTEN (the trade's
  broker_symbol): pnl-valuation and bbg-library match the reason on it, case-insensitively.
  Row-level parser warnings now go in too, kind WARNING with the trade id.
- An earlier upload's UNRECOGNISED row whose trade is still UNRECOGNISED and not in the new file
  is CARRIED across uploads (pnl-valuation asked: otherwise it falls back to a generic reason).
- `upload_report.need_fix` / `upload_history.need_fix` = the file's rows still UNRECOGNISED after
  the upload's own re-resolution. merge_sentence adds ", N of them need a fix".
- `reresolve_unrecognised(conn)` runs after the publish, before contract dates and the library
  sync; hands `blotter.resolve_stored` the trades row as a dict + `currency` (the unrecognised
  instrument's quote_ccy = the file's Currency cell) + `symbol`; accepts (parsed, reason).
  Never raises (start-up safe): errors come back under `error`.
- Trade upserts filter `vars(Trade)` to the columns `trades` has (`_upsert_trade`): the parser's
  `Trade.fin_type` exists before its column may.

**Why:** screens must show every row and what blocks it; a fixed contracts.csv must price the
trade with no re-upload.
**How to apply:** never reintroduce a reject path in the upload; keep the issue symbol as written.
Related: [[upload-merge-rule]], [[upload-history]].
