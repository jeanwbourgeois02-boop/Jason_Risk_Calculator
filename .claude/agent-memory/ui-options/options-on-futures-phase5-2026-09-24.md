---
name: options-on-futures-phase5-2026-09-24
description: How the Options sub-tab shows CMDTY_OPTION (options on commodity futures): group name, value from the book's mark, Greek units per row and the one-unit sum rule, read-only terms, tooltips on their own callback, By-underlying grouping
metadata:
  type: project
---

Phase 5 (housekeeper brief 2026-09-24). `LISTED_OPTION_PRODUCTS = ("CMDTY_OPTION",)` (not
valuation's constant: that also names EQ_OPTION, which left the app). Group "Options on
futures" (`FUTURES_OPTIONS_CLASS`), built by `_futures_option_leg`.

**Facts from the feeding lanes, as of that day:**
- instruments: id 'CLZ26C 75 Comdty', base_ccy = root id 'NYMEX:CL', quote_ccy = contract ccy,
  multiplier = the underlying future's; the parser also writes the underlying future's own
  instruments row (no trade). instrument_options payoff AMERICAN | VANILLA (= European).
- options-store's marks since 2026-09-24: PREMIUM = Bloomberg's price AS QUOTED (no longer
  x multiplier), DELTA = futures lots per option lot, GAMMA/VEGA/THETA/RHO in the quoted price
  unit per unit of underlying (x multiplier for ccy per lot).
- value_book never gives CMDTY_OPTION status CLOSED (only FX_OPTION groups); the tab still
  follows the status if it ever does.

**Why the unit machinery:** FX Greeks are USD (engine.options.portfolio), futures-option Greeks
are lots / contract currency. Every leg carries `<greek>_unit` + `greeks_in`; `_unit_sum` adds
a Greek only over one unit (lots of two roots are two units; USD per day FX theta DOES add to a
USD contract's theta). Group rows get `mixed` + a note; headline shows one line per unit;
breakdown tables add a "Greeks in" column and drop "USD" from headings only when futures
options exist (FX-only book keeps "By pair" / "Delta USD" — a test pins that).

**How to apply:**
- MktVal/Start use the BOOK's mark and spot, so MktVal - Start USD = book P&L USD exactly.
- Underlying via `data.contracts.option_for(root, id, conn=conn)` (contract-master: a read the
  lane table's "Reads" does not list yet — reported in the handoff).
- Implied vol is NOT shown: `price_listed_commodity_option` recomputes the Greeks (pricing) and
  the lane rule is "never prices an option itself". Model named from payoff instead.
- Terms read-only: hidden `terms_fixed`; `OWN_ROW_QUERY`/`READ_ONLY_TERMS_QUERY` in the styles
  and dropdowns; `apply_edit` / `save_and_price` refuse; `option_instruments` excludes them
  (also clears blotter.py's missing-terms banner of them).
- `tooltip_data` has its OWN callback on Input(table,"data") -> Output(table,"tooltip_data"):
  dash-table's loading state is keyed on the `data` prop only, so this never wipes typing
  ([[datatable-loading-state-wipes-typing-2026-09-21]]); keeps `_render`'s return arity.
- Test helper `_add_futures_option` builds ids with contract-master's `option_contract`, so the
  canonical ids stay right if the id format changes.

Supersedes the "LISTED_OPTION_PRODUCTS is ()" part of [[equity-index-removed-listed-path-kept-2026-09-24]].
