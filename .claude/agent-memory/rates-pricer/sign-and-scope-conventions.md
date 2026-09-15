---
name: sign-and-scope-conventions
description: IRS pay/receive sign convention, DV01 definition, and Phase 1 (OIS-only) scope for engine/rates/
metadata:
  type: project
---

Binding conventions for `engine/rates/` (also written into
`engine/rates/__init__.py`'s module docstring -- this memory entry is the quick-recall
version, that docstring is authoritative):

- `trades.quantity > 0` = pay fixed (CLAUDE.md "Leg layouts", matches
  `data/ingest/irs.py`'s BNP-`Position`-sign convention). `instruments.build_instrument`
  takes `pay_fixed: bool` directly (no `Direction` enum, unlike the reference project) --
  the call site in `store.py::price_and_store` computes it as `quantity > 0`.
- `PV_USD` mark = QuantLib's own `ql_swap.NPV()` for the Payer/Receiver type built (positive
  = asset to the fund). `DV01_USD` mark = NPV(+1bp parallel bump) − NPV(base)
  (`valuation.BUMP = 1e-4`). Verified in tests: a payer's DV01 is positive (benefits
  from rates rising), a receiver's is negative.
- **Known gap, not yet fixed**: Phase 1 has no FX-spot source wired into
  `engine/rates/`, so for a non-USD-notional IRS, the `PV_USD`/`DV01_USD` marks are
  actually PV/DV01 in the swap's own notional currency, not converted to USD. Every IRS
  in `data/raw/HA_PNL_20260818.csv` today is USD notional (`IRSOIS-USD-...`), so this
  doesn't bite yet. If a non-USD IRS ever appears, this needs an FX conversion step
  before the mark can be trusted as literally USD.
- Scope is deliberately OIS-only (USD SOFR, EUR ESTR, GBP SONIA, JPY TONA, CHF SARON,
  CAD CORRA, AUD AONIA) -- term-rate (EURIBOR/BBSW), basis and cross-currency (XCCY)
  curve/instrument logic from the reference project ("Rates Swap Calculator",
  `swapcalc/pricing/`) was intentionally NOT ported. If that scope is ever extended,
  port the corresponding pieces of `curves.py`/`instruments.py`/`valuation.py` from the
  reference project rather than reinventing conventions -- see
  [[pricing-conventions-doc-does-not-exist]] for where the reference project's own
  convention notes live (and why this repo has no equivalent doc).
- `curves.curve_id` convention used when writing to the `curves` table:
  `f"{ccy}-{index}-OIS"` (e.g. `"USD-SOFR-OIS"`), matching the example in CLAUDE.md's
  `curves` table comment.
- `curve_quotes` source precedence in `store._read_curve_quotes`: prefer `BBG_BDP` if
  present for the (as_of, ccy, index), else the alphabetically-first source present.
  This is NOT documented anywhere in CLAUDE.md (curve_quotes has no `marks_official`-
  style official-source mapping) -- it's this module's own tie-break, worth
  re-examining if a second real curve_quotes source (beyond BBG_BDP/MANUAL) shows up.
