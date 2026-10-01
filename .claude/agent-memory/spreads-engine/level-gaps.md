---
name: level-gaps
description: 2026-09-30 the Book's missing Entry/Now filled - near-marks spot at entry (estimated flags), 3-leg template levels (crack/crush), options-only net premium level; choices and sample figures
metadata:
  type: project
---
User 2026-09-30: "the size/entry is messy still" (half the sample's open trades had no entry level).

- **Entry spot gap**: `book.spot_near` = exact official SPOT, else `valuation.usd_per_quote` (its `_mark_near`,
  hard rule 2), never a second rule. `level_on` / `entry_level` now return a 5th element, the estimate
  sentences (INTERP spot or mark). Trade level carries `entry/prev/now_estimated` + `*_estimate_note` +
  `estimate_note`; pairs / book_spreads carry `level_estimated {entry, prev, now}`; history points
  `level_estimated`. An INTERP forward mark (LME ticket, XAU forward off spot) also flags now/prev
  estimated (CUAL1, XAU1 in the sample) - consistent with the ui's own INTERP-leg grey.
- **3+ leg parts**: `_template_level` covers a cross part whole with one template via
  `grouping.candidates` (closest ratio, then file order); a ratio outside 5 % still uses the formula,
  said in `note` (CRUSH1 10:10:10 is 17 % off the board crush; its usd_per_unit is the smallest leg, so
  change x usd_per_unit != Daily there - expected). CRACK1 21.70 -> 20.91 USD/bbl.
- **Options only**: `_premium_level`, mode 'premium': sum q x price x price_scale / max|q|; unit = root
  quote_unit, '' for FX options (the ui prints a unitless price at 4 decimals, its FX-premium rule).
  Single non-hedge option stays mode 'price' (CUVOL1 1,850 -> 1,521.73 CNY, unchanged). FX options are
  hedge rows, so an FX-options-only trade takes all its open rows as the position.
  usd_per_unit = N x mult / scale x spot: change x usd_per_unit == Daily on WTIRR1 / JPYVOL1 exactly.
- `price_legs` in the level lets `level_history` chart 'price' and 'premium' levels (no spec).
- The ui still greys only on INTERP leg marks: reading `entry_estimated` is a request to ui (2026-09-30).
- 2026-10-01: a cross part whose other side is flat (`part.closed_roots`, SILARB1: COMEX silver
  bought and sold, SHFE silver short, USD/CNH hedge) takes the open side's own level
  (`_open_side_level`): one contract -> its price level, several months long/short -> calendar,
  else blank; the flat-side sentence stays as `note`. Type, P&L, grouping untouched.
  The real file loads into a scratch DB with `upload.import_blotter(bytes, name, db)` (PYTHONPATH=.).
