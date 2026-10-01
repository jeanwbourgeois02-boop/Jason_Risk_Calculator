---
name: price-ratio
description: 2026-10-01 the Book's price ratio and USD spread per two-leg spread (engine/spreads/ratio.py) - numerator, USD and unit rules, entry spot rule, the choices made where the brief was open, what the real book shows
metadata:
  type: project
---
User 2026-10-01: "price ratio yeah - in the same currency - always usd - with the chinese [leg] as numerator".
Built in `engine/spreads/ratio.py`, wired in `trades._trade` after `_leg_portions`: keys `ratio_entry`, `ratio_now`,
`ratio_basis`, `ratio_reason`, `ratio_entry_reason`, `ratio_now_reason`, `ratio_estimate_note`, `ratio_spec` on each
sub and on the trade (copied only when the trade is exactly one sub that is a spread). Display only.

- Two legs only (sub legs minus hedges), FUTURE / LME_FWD only. UNMATCHED / OUTRIGHT / one-open-leg subs: blank + reason.
- Numerator: the one CN-country leg; else the sub level spec's first leg (matched on instrument_id + month); else near
  month (same root) or the long leg.
- Price: quoted x price_scale x quantity_factor(to, quote qty) x USD per ccy. Unit converts only when same `subsector`
  AND quantity_factor works (mass/volume/energy): target t/bbl/mmbtu if either leg has it, else the denominator's unit.
  **Choice:** the brief named mass units only; volume/energy convert too (NBP therm -> MWh against TTF).
- Entry: leg row `avg_fill` at `usd_per_quote_on_or_before(first OPEN fill date)` (exact official, no estimate, unlike
  the level's entry which uses spot_near). Now: leg `mark` at its own value_book row's `spot`; INTERP mark/spot ->
  figure kept, said in ratio_estimate_note.
- A CNY calendar's entry ratio would carry FX between the two legs' first-fill dates (definition as briefed).
- `ratio_spec` usd_pair is what the valuation reads (USDCNY); risk's history converts CNY via USDCNH (known basis).
- Real book (made-up marks): COPAR3 HG/LME 1.0047 -> 1.0126 (HG numerator, level order); ZNA1 two SHFE/LME month
  pairs; STEEL 2, SCO1 3 calendars (near on top); CATTLE none (4 legs); SILARB1 none (COMEX side flat).
- USD spread (same day, user "always usd"): `spread_usd_entry/now/unit/reason` = numerator - denominator of the same
  USD prices (`usd_price`, shared by `ratio_value` and `spread_value`); None across two units (`spread_unit`).
  Real-book check: COPAR3 65.00 USD/t at entry and STEEL Nov/Dec -8.00 USD/st agree with the Book's entry levels.
See [[spread-levels]], [[equal-size-spreads]], [[template-units]].
