---
name: screens-fix-pass-2026-09-28
description: The fix pass after the screens tidy waves 1-2 (2026-09-28): estimated dates show the date alone with the alert count on hover (Book Next, Timing In / Level "est."), Exposure Net / Gross sum the known months with "excl. N", processing templates named by short name, FX pair decimals (JPY 3, XAU 2, else 4), the header picker's navy-on-white CSS, the test-fixing rule as applied, and the conventions the helpers now carry
metadata:
  type: project
---

Fix pass on 2026-09-28 (the housekeeper's brief_fix.md: six screenshot faults, 29 failing UI tests).
What is now true, beyond the wave 1 / 2 notes:

- **Estimated dates** (`formatting.date_cell(..., alert_date=)` and `estimated_hover(bd, alert_date,
  level, hover)`): an estimated Next / Date cell reads "≈ 30 Nov" alone, grey; the hover is
  "alert counted from 1 Oct while the dates are estimated: 3 bd; level RED by the engine". The
  Timing tab's In cell is "≈" with that hover and its Level chip reads "est." (grey,
  `level-chip--estimated`), never red / amber. Real dates unchanged. Book's `_next_td` passes the
  schedule row's `alert_date`.
- **Exposure Net / Gross never blank** (`curve._net_gross`): the engine's figures when it has them;
  when a leg has no delta the known month cells are summed (display) and the Net cell carries an
  "excl. N" marker with the month reasons on hover; only a commodity with no known month at all
  shows the em dash.
- **Names**: `formatting.SHORT_TEMPLATE_NAMES` + `short_template_name(template_id, name)`; a
  position with `family == "processing"` (or a `proc.` template id) is "3-2-1 crack Nov26" /
  "Board crush Dec26"; benchmarks stay "Brent–WTI Dec26". Book, P&L (through `book.book_rows`)
  share it; Timing shows contracts, not spreads.
- **FX prices**: `price_decimals` takes an FX pair as the unit (`is_fx_pair`, `fx_pair_decimals`:
  JPY 3, XAU / XAG 2, else 4). Book `trade_row` passes the pair for FX_SPOT / FX_FWD; Trades'
  `add_price_units_and_flags` sets `price_unit` to the pair for those products; FX option premiums
  keep '' (their own decimals, floor 4).
- **Data tab**: "Marks that look wrong" Value / Previous are text through `price_text(v, unit)`
  at the unit's tick (`market_data._price_units`, `_suspect_display`); `suspect_rows` still returns
  numbers (its tests read them).
- **Header picker CSS**: `.header-block .DateInput_input { color: var(--navy) }` on a white box,
  the caret drawn by `.header-asof .DateInput::after` (an <input> renders no ::after).
- **Book empty-state Upload button** is a pattern id `{"type": "book-empty-upload", "idx": "book"}`
  with an `ALL` Input (it exists only while the book is empty; the static-callback-id test exempts
  pattern ids). Rule kept: a callback Input is either always in the layout or a pattern id.
- **Trades mark hover**: `_mark_tip` must get `rk.value(rec["mark"])` (the price columns are text,
  so the raw cell is NaN, not None, before formatting) or every unpriced mark reads the generic tip.
- **Tests**: the rule applied was fix where the behaviour stands (em dash for "n/a", real minus
  for parentheses, `status_view` Span + title line for the feed callbacks, `_needs_cached` pinned
  directly), delete where a removed component is pinned (the Trades strike banner, the dead
  Positions-block `commodity_positions_rows`, the Risk end-to-end cards test). `commodity_positions_rows`
  / `positions_table` in ui/tabs/blotter.py are dead code (no screen calls them): an infra finding.

**Why:** the user's screenshots showed the six faults; the suite had 29 UI failures after the
waves. **How to apply:** proof recipe unchanged (`build_sample_db`, direct renders,
`create_app`); the scratch scripts were `scratchpad/verify_fix.py`, `fix_code.py`,
`fix_tests*.py`. Running one failing test by node id (`pytest tests/x.py::name`) is allowed to read
its assertion; never the suite.
