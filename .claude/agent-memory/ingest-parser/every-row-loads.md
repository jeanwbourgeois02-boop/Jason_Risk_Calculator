---
name: every-row-loads
description: 2026-09-29 hard rule 6 rewrite: no row is rejected or skipped; UNRECOGNISED trades, contradictions load on the primary field, resolve_stored / write_parsed, fin_type, NOID ids
metadata:
  type: project
---

User 2026-09-29: "all rows need to load thats non negotiable", "so we see what we need to fix". The
shared definition was a housekeeper scratch file (every-row-loads.md); CLAUDE.md hard rule 6 carries it.

**Why:** a rejected / skipped row vanished from the book; the user wants every row on file as a red to-do.

**How to apply:**
- Inside a row parser, a row that cannot be booked raises `blotter._Unrecognised(reason)`; `_parse_row`
  catches it and `_book_unrecognised` writes product/asset_class UNRECOGNISED, instrument
  'UNRECOGNISED:' + Symbol (else Underlying, Description, Trade Id) upper-cased, no legs, multiplier 0,
  quote_ccy from Currency, qty signed by Side in file units (0 if none), price as read (0 if none).
  Named on `ParseResult.unrecognised` (common.Unrecognised: row_no, trade_id, symbol = Symbol cell, reason).
  `rejects`, `skipped_other_rows`, `n_skipped_other/retired` stay as fields and are always empty/0.
- Contradictions warn and load on the primary: Symbol over Description / Currency / venue
  (`_resolve_on_symbol` drops the fewest contradicting cells), Buy/Sell columns over Description and
  over the Symbol's pair, strike column over Description, symbol over CN-code type/strike, LME in USD.
- Blank Trade Id: 'NOID-' + sha1(populated cells)[:10], '-2' on repeats, warned (my choice).
- Single-currency CURRENCY rows are UNRECOGNISED too now (strict reading of the rule; was CASH-only).
- Status rows unchanged, but now named on `excluded_status_rows` (row_no, trade_id, symbol, status).
- `Trade.fin_type` = Fin Type cell (else Product): needed so re-resolution never turns e.g. a
  commodity swap on 'CLZ6' into a FUTURE. `write_parsed` writes only columns `trades` has, so it is
  harmless until ingest-schema adds the column; without it `resolve_stored` infers the kind from the
  symbol shape (Forward / Option / Future).
- `resolve_stored(trade, conn) -> (ParseResult | None, reason)` rebuilds the row (broker_price as the
  Price cell so broker_price_scale applies again) and re-parses with an empty book filter;
  `write_parsed(res, conn)` is load's writer, shared with ingest-booking's `reresolve_unrecognised`.
- The contract universe is lru-cached per path (`universe._load`): a contracts.csv edit is seen by a
  running process only after `_load.cache_clear()` (startup re-resolution is fine).
- The golden book pins the sample, which now has 52 trades (2 UNRECOGNISED), not 50.
Related: [[phase2-macro-removal]], [[synthetic-sample-book]], [[commodity-futures-ingest]].
