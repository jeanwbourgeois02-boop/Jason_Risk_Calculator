---
name: trade-book
description: 2026-09-29 Phase G trade_book (engine/spreads/trades.py) - the type rule's choices (roll-aware day calendars, flat roots as a side), what the real export types as, the no-marks hedge fallback, what is hard-coded pending contract-master
metadata:
  type: project
---
Built 2026-09-29 (Phase G: the trade, the PBRoot name, is the screens' unit). `trade_book(conn, as_of, spreads, value_fn)`
+ `level_history`. Reuses `_Book`, `strategies._build_legs/_entry_value(quoted=)/_event_of/_spot_on`, pairs' levels,
`carry.curve_roll_downs` (new, per-leg roll-down on one curve's common date), `rolls`.
- **Type rule choices** (brief expected CATTLE CP, COPAR3 CE, STEEL CAL, SILARB1 CE, ZNA1 MIXED, SCO1 mismatch; all met):
  a root netting to zero = calendar; else only calendars *put on as one* (same day, opposite months, lots within 5 %,
  trade-vs-trade then pooled) that are NOT rolls (rolls.py trade ids excluded) count. **Why:** CATTLE's 9/16 FC V/X and
  LC V/Z same-day pairs are rolls (else CATTLE reads MIXED); ZNA1's 9/21 SHFE V/X 627 and LME V/X 3150 t are real calendars.
- A root traded and now flat is a cross side when one root alone is left (SILARB1: COMEX SI flat, SHFE AG -2 open).
- Several calendar parts of one kind stay that kind (SCO1 = CALENDAR, 2 parts), level None "holds 2 spreads".
- MIXED is no mismatch when the label names one of its parts (ZNA1's .3 fills are the LME calendar).
- Hedge oversized with no marks: exposure at the fill, CNY converted at the trade's own USD/CNH future fill average
  (a blotter rate, sizing check only). SILARB1: 400k vs 72.6k = 5.5x, and runs WITH the exposure (short China leg).
- Family and close times now come from contract-master (`ContractRoot.family`, `.close`, 2026-09-29 follow-up); the
  close note compares by (exchange, time, tz). No-root trades: XAU pair -> its metal family, FX-only -> 'fx'.
- No futures/LME leg open: typed by the open options' underlying (or the precious forward) -> OUTRIGHT / CALENDAR /
  cross; FX-only (EURVOL1) -> OUTRIGHT "a currency position"; never blank while anything is open (coordinator).
- Sample (ui.sample_book, PBRoot names since 2026-09-29): BRWTI1 labelled .4 reads CROSS_EXCHANGE (Brent and WTI are
  both subsector crude_oil) -> type_mismatch; open question whether Brent-WTI is cross product for the desk.
- China vs West ratio is CONVERTED in strategies._levels for every CN/abroad pair (not only ratio_screen templates);
  `level_china_leg`; usd_per_unit = size x foreign converted price x unit spot. Supersedes [[strategies-pairs]]' raw ratio.
- Sample blotter has no PBRoot: every trade unassigned. Verify by labelling a scratch copy (UPDATE trades SET strategy,
  trade_type, pb_root). Checked: legs' LTD and Daily sum to the trade's, = position pnl, deterministic.
- Scratchpad is shared with other lanes' agents: use a private subfolder (another agent overwrote real.db once).
- Round 1 (2026-09-29): value_book's mark_date is the leg's SETTLE date, not the quote date: the mark's close is the as-of
  (or the filled reader's earlier close, parsed from its note); snapped_at looked up on (instrument, as-of, settle_date).
  A one-contract OUTRIGHT's level is its price (mode 'price', no spec). Closed trades: read as on the last open business
  day (stub strategy entry, no period P&L, so 2 extra valuations: that day and the close date). Scorecard by_type = these
  rule types; by_spread_type (label) kept.
- UNRECOGNISED rows (hard rule 6, 2026-09-29): a leg of its PBRoot trade (status 'unrecognised', name = broker symbol,
  red flag), never typed / levelled / balanced / hedged; only-unrecognised trade = type '' + flag, counts as open. The
  strategy P&L stays None whole (no partial sum, existing rule): the ui may sum priced legs with excl. N. Test by
  inserting instruments + trades rows by hand (asset_class/product UNRECOGNISED, multiplier 0, no legs).
- Leg names (2026-09-29): an option on a future reads 'NYMEX Crude oil Dec26 62 put' (`option_leg_name`, strike via
  `strike_words`: own decimals, thousands commas). `leg_name`'s strike/option_type are keyword-only and default off
  ON PURPOSE: ui/tabs/data_checks.py, blotter_fills.py, risk_folds.py call leg_name and append the strike themselves,
  so never make it add the strike from instrument_id alone (the ui would print it twice).
