---
name: real-export-conventions
description: What Jason's real prime-broker export (data/template PnL tool.csv, 2026-09-28, git-ignored) taught the parser -- columns it lacks, PBRoot coding, broker price scale, LastModified dedupe, cancelled ids, empty book.yaml
metadata:
  type: project
---

Jason's real export arrived 2026-09-28 at `data/template PnL tool.csv` (git-ignored, never commit;
read freely). 89 FUTURE fills 14-24 Sep 2026, Trader 'JS', Status all 'Completed'. It parses to
89 trades (75 FUTURE, 14 LME_FWD), 0 rejects, 0 warnings, day-first.

**Why:** it is the first real commodity export; the synthetic sample and every guess before it
were built blind (see [[commodity-futures-ingest]]).

**How to apply:**
- Columns it has NOT got: Fund, Desk, Version, Settle Date, Currency, Execution Venue,
  NetInvoice, Total Fees, Product (blank). So: the exchange comes only from the symbol suffix
  (`-USAA` / `-UKAA` / `-CHAA` / `-SPAA`, contract-master's `_PB_SUFFIX_EXCHANGES`); no
  NetInvoice cross-check ever fires; the row kind is `Fin Type` alone.
- Dates d/m/yyyy ('16/9/2026'), `detect_day_first` picks day-first; Price has thousands commas
  ('1,280.00'); `_ungroup` handles it.
- `PBRoot` = '<letters><digits>[.<d>]_<STRATEGY>' ('JSHY10.3_COPAR3'): `common.parse_pb_root`
  -> (strategy, trade_type); .3 CROSS_EXCHANGE, .4 CROSS_PRODUCT, .5 TERM_STRUCTURE (the user's
  own coding, 2026-09-28). Raw cell on `trades.pb_root`; `_common` fills all three on every kind.
  Seen strategies: COPAR3, SCO1, ZNA1 (.3), CATTLE, SILARB1, STEEL, ZNA1 (no decimal).
- Broker price scale: the broker books cents-quoted contracts in whole currency per unit (FC 3.32,
  LC 2.19, HG 6.36). `_future_fill(broker_scale=root.broker_price_scale)` multiplies the Price
  cell (exact via Decimal), so `trades.price` and the NOTIONAL leg are Bloomberg's units. A price
  rebuilt from NetInvoice is NOT scaled (already Bloomberg units). CMDTY_OPTION premiums take the
  same scale (a guess, no real option row seen); the strike is left as the symbol states it.
  `res.price_scaled` root_id -> (name, scale, fills) feeds the one report sentence.
- Repeated Trade Id with no Version column: latest `LastModified` wins, then last in file
  (`_dedupe_versions` returns `(df, n, how)`; day-first is detected BEFORE the dedupe now).
  The export had 0 repeats.
- `ParseResult.cancelled_trade_ids`: Trade Ids of rows whose Status is cancel / void / delet /
  reject / fail (`REMOVED_STATUS_WORDS`), never pending / draft / error; upload deletes them.
- `config/book.yaml`: every list empty since 2026-09-28 (the export is Jason's own pull).
- LME rows come as `LPZ26-UKAA` (two-digit year) / `LXV6-UKAA`: resolve_future lands on LME:CA /
  LME:ZS, `_lme_match` routes them to LME_FWD at the month's third Wednesday (14 info notes).
- SGX:XUC (`XUCV6-SPAA`, USD/CNH FX future) is a FUTURE in CNH: instrument 'UCV26 Curncy',
  multiplier 100000, ticker 'UCV6 Curncy'.
- Price scale hazard: a Bloomberg-units fill on a scale-100 root (205.40 on RB) is now 100x too
  big after the scale and the NetInvoice check warns "another unit" (when a NetInvoice exists).
