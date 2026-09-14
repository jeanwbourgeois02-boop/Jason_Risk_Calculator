---
name: idempotent-loader
description: data/load.py CLI design — on_duplicate='skip', skip vs reject accounting, why XXXUSD SPOT warnings are not rejects
metadata:
  type: project
---

`data/load.py` (`py -3 -m data.load <csv> [--db path] [--marks path] [--allow-rejects] [--as-of ISO]`)
loads BNP trades/legs/positions via `data.ingest.bnp.load(..., on_duplicate='skip')` and
BNP_BVAL marks via `data.bloomberg.bnp_marks.extract_bnp_marks` + a pre-filter against
existing `marks` keys (not `load_bnp_marks` directly, so duplicate-key skips can be
counted separately from genuine parse/validation rejects).

Key design points, in case the loader needs to change:
- `bnp.load` gained `on_duplicate: str = 'error'` (default unchanged — plain INSERT,
  raises `sqlite3.IntegrityError` on a re-run). `on_duplicate='skip'` pre-filters trades
  (by `trade_id`), legs (by `trade_id, leg_no`) and positions (by the full PK) against
  what's already in the DB before inserting, and records counts on
  `ParseResult.skipped = {'trades': n, 'legs': n, 'positions': n}`.
- The CLI's "rejects" count is genuine data-quality failures only: BNP row-level rejects
  (bad symbol/description regex etc.), BNP recon failures (tolerance breaches), and
  marks-level rejects (unknown instrument, bad mark_type, non-finite value). Rows skipped
  purely because their primary key already exists are never counted as rejects — this
  matters because re-running the loader on the same file/day must exit 0.
- On the reference file, `extract_bnp_marks` always logs ~32 warnings
  ("cannot derive SPOT for <PAIR> (XXXUSD pair...)") for every EURUSD/GBPUSD/AUDUSD/
  XAUUSD row — this is `result.warnings`, not `result.rejects`, and is expected/benign
  per that module's own docstring (an XXXUSD row's `Fx` column is always 1.0 and gives no
  base-ccy spot). Don't mistake this log noise for a loader bug.
- A regex-failing FORWARD row is rejected *twice* in the CLI's reject count: once by
  `bnp.load` (trade parser) and once by `extract_bnp_marks` (mark extractor), since both
  independently parse the same FORWARD rows. This is correct/expected, not
  double-counting a bug — a test asserting `rejects=1` for one bad row is wrong; it's `2`.
- `--marks <canonical_csv>` loads via a temp-file pre-filter around
  `data.bloomberg.marks_csv.load_marks_csv` (same skip/reject split as the BNP_BVAL path).

## Conflicts (key exists, value differs) — added after reviewer C-1

Key-only skip was a CRITICAL bug: a same-PK row with an *amended* value (BNP re-issues a
corrected Local Cost/Price for an existing `trade_id`, or a mark value changes for the
same `(as_of, instrument, settle, type, source)`) was silently dropped with no signal.
Fixed by content-comparing on every key match, not just checking key membership:
- `bnp.load(..., on_duplicate='skip')` now fetches the existing DB row for every
  key-matched trade/leg/position and diffs it field-by-field against the freshly parsed
  row (`data/ingest/bnp.py::_row_diff` / `_values_match`, abs/rel float tolerance
  `1e-6`). Same values -> `res.skipped[...]` (unchanged). Different values -> a
  **conflict**: counted in `res.conflicts = {'trades': n, 'legs': n, 'positions': n}`,
  described in `res.conflict_details` (key + differing column names), and the existing
  DB row is left untouched — the amended row is never silently applied by `skip`.
- `data/load.py`'s two marks idempotent helpers (`_load_bnp_marks_idempotent`,
  `_load_marks_csv_idempotent`) do the same for `marks.value` (own small tolerance
  `_values_close`, same 1e-6 abs/rel shape).
- CLI summary line gained `conflicts=N`. **N mirrors the existing `skipped=N` asymmetry
  on purpose**: it is `bnp_res.conflicts['trades'] + total_marks_conflicts` only — leg/
  position conflicts for the same amended trade are still detailed on stderr
  (`conflict: ...` lines) but are not double-counted in the headline total, exactly like
  `n_skipped` already only sums trades+marks and ignores `res.skipped['legs'/'positions']`.
  If that skipped asymmetry is ever fixed, fix conflicts the same way at the same time.
- New `--allow-conflicts` flag, deliberately separate from `--allow-rejects` (a conflict
  is a different signal — stale/amended source data — from a parse failure; conflicts
  and rejects are counted and gated independently, both must be zero, or explicitly
  allowed, for exit 0).
- Gotcha when writing synthetic conflict-CSV test fixtures off `_fwd_row()`: changing the
  fill rate (the `@ NN.NNNNNNNN` in `Symbol Description`) or the `Price` column also
  moves `local_cost`/`mv_local`/`mv_base`/`dtd_total_pnl`/`mtd_total_pnl` recon checks out
  of tolerance unless you update `Local Cost`, `Market Value Local`, `Market Value Base`,
  `DTD Total P&L`, `DTD Trading P&L`, `MTD Total P&L` in the same row to stay consistent
  — otherwise the row becomes a recon-failure reject instead of a clean conflict, and
  `rejects=0` assertions fail. Also: the two synthetic files must share the same
  `as_of_date` (pass `--as-of` explicitly) for a *marks* conflict test, since `marks`'
  PK includes `as_of_date` and two differently-named `HA_PNL_*.csv` files default to two
  different `as_of_date`s (file date minus one weekday) — a `trades` conflict test
  doesn't need this since `trades`' PK is just `trade_id`.
