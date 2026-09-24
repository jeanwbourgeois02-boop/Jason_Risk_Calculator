---
name: phase2-fx-only-2026-09-24
description: book_positions after the Phase 2 removal - keys fx and fx_options only, every currency at official spot, where Phase 3 commodity lines slot in
metadata:
  type: project
---

User approval 2026-09-24 (commodity conversion Phase 2): the equity index line (ES + SPX),
the rates DV01 block and the 1M NDF rates left `engine/ladder/positions.py`.

- `book_positions` returns exactly `{"fx", "fx_options"}`. Blocks are listed in `_BLOCKS` with
  their fallback shape in `_EMPTY`; Phase 3's commodity lines (from curve-positions) are one
  more entry in each, built only when the housekeeper briefs Phase 3.
- Rates are `rates_from_marks` alone (latest official SPOT), the same as
  `ui.tabs.cash_ladder.net_gross_usd` / `load_inputs`. `by_ccy[].label` = the rate entry's
  `pair` ('USDKRW'), '' when the currency has no spot. Missing-rate reason is a local copy of
  the Ladder's wording, "no official SPOT for <as_of>: KRW" (engine must not import ui/).
- An FX option whose pair spot is missing is already named by `option_records_from_db`
  ("o1: no official SPOT for EURUSD on <d> (option delta not converted)"), so the
  positions-side "no official SPOT to convert" message only fires when the base ccy's USD
  pair is missing but the option's own pair spot exists.
- `futures_delta.py` untouched; its tests still use 'ESU6 Index' as a generic USD future
  fixture name (not an equity feature).
- Test fixture uses a USD commodity future (base_ccy 'NYMEX:CL') to show futures are not FX delta.

Related: [[futures-usd-at-spot-2026-09-24]]
