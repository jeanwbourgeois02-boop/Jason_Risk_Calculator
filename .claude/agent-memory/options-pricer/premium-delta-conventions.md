---
name: premium-delta-conventions
description: Unit/sign conventions of every engine/options mark (PREMIUM, DELTA, GAMMA, VEGA, THETA, RHO), incl. the digital/touch payout-currency convention fixed 2026-09-18 -- restate before touching marks or anything that sums them.
metadata:
  type: project
---

Load-bearing for anyone reading option marks downstream (cash-ladder, valuation.py's
option P&L line, the UI's Greek headline). Full text lives in
`engine/options/store.py`'s and `pricer.py`'s docstrings; this is the why.

- **PREMIUM, vanilla-type payoffs (VANILLA/AMERICAN/ASIAN/BARRIER).** Vendored
  Garman-Kohlhagen `price` is QUOTE ccy per 1 BASE unit; `premium = price / spot` =
  dimensionless fraction of BASE notional, paid in BASE ccy = the blotter fill's unit
  (EURSEK 35,000,000 @ 0.00579 -> NetInvoice EUR 202,650). Correct since Phase 2.

- **PREMIUM, cash payoffs (DIGITAL/ONE_TOUCH/NO_TOUCH) -- was WRONG until 2026-09-18.**
  The vendored digital/touch pay `cash_payout` QUOTE units; the wrappers passed 1.0 and
  divided by spot, so the mark was exp(-r_d T)N(d2)/S: 1/150 of the truth on USDJPY.
  The blotter books a digital with Quantity = PAYOUT notional, fill = fraction of the
  payout, invoiced in the BASE ccy (`Currency` column: EUR for EURSEK112526C, USD for
  USDJPY111926P), and the payout must be BASE ccy too (USD 248,000 premium cannot buy a
  JPY 2,000,000 payout). So `store.CASH_PAYOUT_CCY = 'BASE'`, PREMIUM in
  [0, base-ccy DF] = exp(-r_f T) N(+-d1).
  **Why:** the book's P&L is quantity x (PREMIUM - fill) x S; only right in the fill's unit.
  **How to apply:** never divide a cash payoff by spot "like a vanilla". Digital = static
  replication (vendored cash digital paying K +/- vendored vanilla); touches = vendored
  touch in the inverted pair (1/S, 1/B, rates swapped, up<->down) mapped back by
  `pricer._flip_to_quote_terms`. The payout ccy has no export column; it was inferred,
  then CONFIRMED BY THE USER 2026-09-18 (USD on the USDJPY digitals, EUR on the EURSEK one).
  "Base notional converted at strike" (QUOTE payout of K per unit) differs from BASE
  payout by exactly one vanilla premium, several % of payout when in the money.

- **Every mark is per 1 unit of trades.quantity, LONG position, sign applied downstream**
  (`t.quantity * m.value`). DELTA = raw spot delta in BASE units (digital near strike:
  |DELTA| ~ 5-10). GAMMA = d(DELTA) per 1.0 of spot AS QUOTED -- EURUSD ~38, EURSEK ~4,
  USDJPY ~0.7 for similar options, so NEVER summable across pairs until rescaled
  (x S/100 = per 1 % move). VEGA per vol POINT, THETA per CALENDAR day, RHO per 1pp of the
  quote-ccy rate: all three in QUOTE ccy.
  **How to apply (USD headline):** value and DELTA convert with USD-per-BASE; GAMMA with
  S/100 x USD-per-BASE; VEGA/THETA/RHO with USD-per-QUOTE. One factor for all six (what
  portfolio.py did until 2026-09-18, and what `ui/tabs/options.py::_leg_row` copied) makes
  a USDJPY delta 150x too small.

- **Domestic = quote ccy, foreign = base ccy** for Garman-Kohlhagen, read off the
  `instruments` row, currency-agnostic. See [[vendor-api-quirks]].

- **Blotter facts worth remembering:** `FxOption Type` is '0' on every option row and no
  Description names a payoff, so the book's three digitals arrive as payoff VANILLA,
  strike 0. "EUR Put"/"EUR Call" in a Description means EUROPEAN exercise, not the euro
  (it appears on USDJPY rows). `set_option_terms` WRITES its payoff='VANILLA' default --
  a strike-only caller must pass the payoff (`store.on_file_terms`).

- **A terms change invalidates the marks (2026-09-18).** `set_option_terms` deletes every
  QL_OPTIONS_PRICER mark of the instrument (all dates, 7 types incl. DELTA_PA) in the
  same transaction when strike/type/payoff/barrier actually change; identical re-save =
  no delete; MANUAL never touched.
  **Why:** the UI's edit-then-reprice left a vanilla's marks official on a digital
  whenever the reprice was skipped. **How to apply:** any NEW pricing term added to
  `instrument_options` (avg_start_date once the engine uses it, a payout-ccy column) must
  join `_same_terms`' comparison. Options are only priced for the live date, so past
  dates stay empty after a change. `engine/pnl/ledger.realise_settled` freezes an expired
  option ONCE and never revisits it, so (coordinator's decision 2026-09-18, done in
  `set_option_terms`, not the ledger) the same change also deletes the instrument's
  `realised_pnl` rows in the same transaction; table absent -> skip silently. An option
  whose terms are corrected AFTER expiry can only be re-frozen if a corrected PREMIUM
  dated on or before expiry is written (`price_and_store` with a past as_of works if that
  day's spot/rates/vol are on file) -- otherwise the ledger names it unrealisable.
  The strikes from the old Excel (USDJPY 152 put x2, EURSEK 11.4 call) were confirmed
  consistent with the fills by the coordinator 2026-09-18 (USDJPY traded 157-163 in Aug).

- **Equity/commodity marks mix bases:** PREMIUM is x `instruments.multiplier` (per
  contract) but the Greeks are per 1 unit of underlying (reader multiplies). No live
  trades; left as is 2026-09-18.
