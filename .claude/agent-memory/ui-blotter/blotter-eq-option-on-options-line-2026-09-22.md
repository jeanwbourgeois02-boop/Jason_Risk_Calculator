---
name: blotter-eq-option-on-options-line-2026-09-22
description: User decision 2026-09-22: SPX listed index options (EQ_OPTION) sit on the Total book's existing "Options" line and in the options scope, never a class of their own; plus the trap that tests/test_ui_blotter.py still carries header tests from before the ui-shell split
metadata:
  type: project
---

Listed index options (`trades.product = 'EQ_OPTION'`, the SPX puts / calls the parser books from 'SPX/E261016P7615') belong on the Total book's existing "Options" line of the P&L-by-asset-class table and in `SCOPE_PRODUCTS["options"]`, with the FX options. They had been falling to the "Other" line because `ASSET_CLASS_OF` did not know the product.

**Why:** user, 2026-09-22: "just add that to options". The user thinks of the SPX options as options, not as a separate equity class; the Options sub-tab's own grouped table already queried FX_OPTION + EQ_OPTION + CMDTY_OPTION, so the Total book was the odd one out. `CMDTY_OPTION` / `SWAPTION` / `CAP_FLOOR` stay out of both constants on purpose: nothing books them yet, and the "Other" fallback in `asset_class_pnl_rows` is kept for any product still unknown.

**How to apply:** when a new product gets an ingest path, add it to `ASSET_CLASS_OF` and the right `SCOPE_PRODUCTS` entry in the same change, and ask the user which line it belongs on if it is not obviously one of the four (FX / Futures / Rates / Options). A test booking an EQ_OPTION lives in `tests/test_ui_blotter.py::_add_eq_option` (mirrors `tests/test_listed_options.py`'s rows: multiplier 100, one NOTIONAL USD leg at expiry, official FUTURE_PX on the option's own instrument).

Cross-lane trap seen the same day: `tests/test_ui_blotter.py` still holds a few `ui.tabs.header` tests from before the 2026-09-22 ui-shell split (`test_build_chart_returns_graph`, `test_register_callbacks_smoke`, and the strip-title tests around them). When ui-header changes header internals (that day: `_CHART_LOOKBACK_DAYS` removed by the full-span LTD chart work) those tests fail in MY file although the change is theirs, and ui-header cannot edit my file. Report it to the housekeeper rather than "fixing" a header test against another agent's in-flight change; the clean fix is moving those tests to `tests/test_header.py` (infra's mechanical lane).
