---
name: strategies-pairs
description: 2026-09-28 pairs inside a strategy (engine/spreads/strategies.py) - the choices the brief left open, what Jason's real book pairs into, the raw China/foreign ratio, the Daily split's contract, what is pending the user
metadata:
  type: project
---

Built 2026-09-28 (user-approved design, reviewer findings folded in): `book_spreads(...)["strategies"]`.

Choices I took (low stakes unless the user objects):
- **Labelled rule first, the other two after it, noted.** A .3 label whose legs do not fit
  (SCO1: HRC Oct/Nov beside iron ore Oct/Feb, Nov/Mar) leaves them unpaired; they then pair as
  calendars with `type_source` 'fallback' and a note, never silently.
- CROSS_EXCHANGE needs the same `subsector` on different exchanges; CROSS_PRODUCT the same
  `sector` (else gold pairs with aluminium because both are December). The '' catch-all (unlabelled
  trades) makes a cross pair only where a `config/spreads/` template names the two roots.
- Whole lots (cross pairs sized by VALUE since 2026-09-29, [[value-sizing-one-spread]]): the smaller side pairs in full, the larger side rounds to its
  nearest whole lot (min 1); an LME leg (tonnes, lots fractional) pairs exact tonnes. The pair's
  `residual_units` is that tail (COPAR3: 31 HG lots vs 350 t leaves +1.5 t); whole lots left are
  the strategy's `residuals`.
- Greedy by (months apart, earlier month, contract ids); every other possible partner named in
  `note`. SCO1's iron ore decomposes as Nov/Feb 1000, Oct/Feb 1521, Oct/Mar 1000 (not the trader's
  own Oct/Feb 2521 + Nov/Mar 1000): economically the same, said in the note.
- Trade ids: a contract's trades are listed once, on the first pair using it (else its residual);
  `contract_trade_ids` lists them all. Lots split, fills never do.
- Hedges: fx-sector roots (SGX:XUC) AND FX spot/forward/swap/OPTION on a currency pair under the
  strategy's PBRoot (user, 2026-09-28; options added 2026-09-29), whatever their status. Never a
  precious-metal pair (XAUUSD is a residual of its own). An FX forward's usd_notional is its USD leg
  from the fill; an FX option's is the USD leg of its DELTA (qty x DELTA, x the pair's official SPOT
  on a USD-quoted pair; exact marks, None without): long USD positive like the forwards, same
  magnitude as book_positions' by_pair. The golden sample's DELTA marks are 0.45+-0.2 for puts too
  (tests/golden_book.py), so its puts read long USD: a fixture quirk, not the rule.
- Coverage (2026-09-29) reads `hedge_cny_usd` (hedges on a CNH/CNY pair only); `hedge_usd` stays
  every hedge. Before, a EURGBP forward with no USD leg blanked the CNY coverage of the sample.
- **SUPERSEDED 2026-09-29: the ratio is now CONVERTED (user), see [[trade-book]].** Was: Ratio is RAW (China CNY/unit over foreign USD/unit, no FX) where the template has
  `params.ratio_screen` (now `Template.ratio_screen`): the desk convention text says "8.0-8.3 at FX
  7.1-7.3", i.e. FX left in. ZNA1 entry 6.64 / 6.75. Converted-vs-raw still open with Jason
  ([[project-ratio-convention]] in the user's memory). `level_alt` is the template's USD/t difference.
- `usd_per_unit` of a ratio = China paired size x foreign price (CNY/unit) x CNY spot, with the
  foreign leg unchanged.
- **Coverage sign (user, 2026-09-28, from Jason's fills: buy 150 ZNAX6 ~3.0m USD, sell 30 XUCX6
  = 3.0m):** a long China leg is long USDCNH at its notional (import parity), hedged by a SHORT
  USD/CNH future. `hedge_coverage_net` = -hedge / CNY net (1.0 = hedged), `unhedged_cny` = CNY net +
  hedge, warn when hedge * net > 0. `hedge_coverage` = |hedge| / CNY gross stays (the brief). Jason
  sizes to the NET CNY exposure (ZNA1: -75 lots vs ~+7m net).
- The Daily split runs only when the strategy's `pnl_usd['daily']` is a figure, on the very rows
  it was measured from (`book.daily[sid]`), so `total` == Daily to the cent; else zeros with
  `reason`. `total` is never the Daily's stand-in on a screen.

**How to verify:** `tests.golden_book.build_book()` into scratch, `UPDATE trades SET strategy=...,
trade_type=...` on SHFE:CU + COMEX:HG (+ USDCNH) and NYMEX:CL, then `book_spreads`; check
`daily.total - pnl_usd['daily'] == 0` and `level_change x usd_per_unit` per clean pair. On a copy of
`data/raw/risk.db` (no marks) every level but the entry is None with its reason; the structure
(pairs / residuals / hedges, membership exact) is the check there.
