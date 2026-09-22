---
name: sign-and-scope-conventions
description: IRS pay/receive sign convention, DV01 definition, direction-flip mark reversal (the pricer's own, QuantLib-free callers import it lazily), and Phase 1 (OIS-only) scope for engine/rates/
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
- Non-USD notional: since 2026-09-17 PV / DV01 / cashflows are computed in the swap's
  currency and converted at that day's official SPOT (`store._usd_per_ccy`); no SPOT
  on file raises, never an unconverted number under a `_USD` name.
- **Direction flip (2026-09-22, reviewer finding):** when the user flips a swap's
  pay/receive (`data/ingest/irs_direction.py`), the pricer's own marks are reversed by
  the pricer, `engine/rates/store.py::reverse_direction_marks(conn, trade_id,
  instrument_id)`: QL_PRICER PV_USD / DV01_USD / CASHFLOW_USD × −1 on every date,
  PAR_RATE untouched, other sources' rows of those types deleted, `realised_pnl` row
  deleted, all three types deleted (not reversed) when two trades share one
  instrument. Exact for a vanilla OIS (receiver = payer with every cashflow negated on
  the same curve; USD conversion one positive factor). Hard rule 2 is why it lives in
  the pricer, not in ingest. **Why:** `engine.rates.store` loads QuantLib at import
  (via `.curves` / `.valuation`), and the parser, upload and UI must stay QuantLib-free
  at import time, so ingest imports it lazily inside `irs_direction._reverse_marks`.
  **How to apply:** never add a module-level `engine.rates` import to `data/ingest/`
  or `ui/`; anything else the ingest layer needs from the pricer goes the same lazy
  way, and stays inside the caller's transaction (commit nothing in store.py helpers
  called from ingest).
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
