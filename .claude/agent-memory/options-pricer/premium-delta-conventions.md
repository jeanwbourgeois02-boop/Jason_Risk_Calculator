---
name: premium-delta-conventions
description: Unit/sign conventions decided while wiring engine/options -- restate before touching PREMIUM/DELTA marks or the P&L line that consumes them.
metadata:
  type: project
---

Decided in engine/options/pricer.py's module docstring (2026-09-17), load-bearing
for anyone reading PREMIUM/DELTA marks downstream (cash-ladder, a future
options P&L line):

- **PREMIUM unit conversion.** The vendored Garman-Kohlhagen pricers return
  `price` in QUOTE currency per 1 unit of BASE notional. The blotter's
  `trades.price` fill is a premium expressed as a FRACTION OF BASE-CCY
  NOTIONAL, paid in the BASE currency (confirmed against real rows: EURSEK
  35,000,000 @ 0.00579 -> NetInvoice 202,650 EUR = 35,000,000 * 0.00579).
  `pricer.py` converts every result with `premium = quote_price / spot` so
  the PREMIUM mark lands in the same unit as `trades.price` -- CLAUDE.md's
  `PnL_USD = (premium_mark - premium_fill) * Size` then needs no further
  conversion for the base-ccy leg (only a spot conversion if the premium
  currency itself isn't USD, per CLAUDE.md's own wording).

- **DELTA mark is unsigned per 1 unit of trades.quantity.** `pricer.py`
  returns the vendored pricer's raw spot `delta` field UNMODIFIED -- sign
  as computed for a LONG position in the given option_type (long call
  positive, long put negative), with NO adjustment for whether the actual
  trade is long or short. `trades.quantity`'s own sign (CLAUDE.md: `> 0` =
  long base currency) is applied downstream by the reader (e.g.
  engine/ladder's delta query: `t.quantity * m.value`), never inside
  engine/options itself. Verified with a sign-sanity test:
  `tests/test_options_pricing.py::test_long_call_delta_positive_long_put_delta_negative`.

- **Domestic vs foreign rate for Garman-Kohlhagen**: domestic_rate =
  `instruments.quote_ccy`'s rate, foreign_rate = `instruments.base_ccy`'s
  rate -- read directly off the `instruments` row (base_ccy/quote_ccy), not
  re-derived from `options_calc.fx.g10.pair_convention` (which only covers
  the 45 G10 pairs and would raise on a non-G10 cross). The market-data
  RATE lookup itself (`options_calc.fx.rate_curves`) still only supports
  G10 currencies -- a non-G10 pair skips with "no rate data for pair", but
  the domestic/foreign ASSIGNMENT rule is currency-agnostic and applies to
  every pair. See [[vendor-api-quirks]] for the vendor library's own
  submodule-import gotchas found while wiring this.
