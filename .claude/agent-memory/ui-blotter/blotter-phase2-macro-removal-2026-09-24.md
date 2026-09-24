---
name: blotter-phase2-macro-removal-2026-09-24
description: Commodity conversion Phase 2 on the Blotter - Rates sub-tab, equity-index/DV01 positions lines, NDF labels, FX-swap package panel and EQ_OPTION removed (FX_SWAP product kept); Futures sub-tab gained local P&L, Ccy, Exchange
metadata:
  type: project
---

On 2026-09-24 the user approved removing the macro trader's products (rates/IRS, NDFs, FX-swap package rule, ES/SPX equity index). The screens went first, top-down; engine lanes remove their outputs after.

What changed in ui/tabs/blotter.py (older memory notes that mention Rates, EQ_OPTION or swap packages are now history):
- Sub-tabs: Total book, FX, Futures, Options, Bundles, Manual entry. `ui.tabs.rates` is no longer imported (ui-rates deletes it).
- SCOPE_PRODUCTS fx = FX_SPOT/FX_FWD/FX_SWAP, options = FX_OPTION only; ASSET_CLASS_OF has no IRS/EQ_OPTION, so a leftover one shows under "Other" (never dropped). FX_SWAP STAYS (housekeeper correction same day): manual entry books a 4-leg swap under one trade and Jason may roll hedges that way; only the blotter's package rule (package_id grouping, swap_review, the package detail panel) left.
- Positions: only currency rows, FX net/gross, FX options. `book_positions` still returns `equity_index` and `rates` until book-positions drops them; they are simply not read. `by_ccy` rows carry only `label` (no `pair`), and an NDF currency's label was the 1M ticker, so `_pair_label` shows a non-pair label as USD+ccy.
- Futures table: Exchange (from `data.contracts.get_root(instruments.base_ccy)`; base_ccy IS the root id for a commodity future, 'SHFE:CU'), P&L (local) = value_book's pnl_local, Ccy = instruments.quote_ccy, then P&L (USD).
- A settled future's pnl_local is always NaN (the frozen realised_pnl row holds USD only) -> tooltip SETTLED_LOCAL_REASON, never 0.

**Why:** CLAUDE.md "Commodity conversion plan" Phase 2.
**How to apply:**
- `tests/test_positions.py` (book-positions' file) has a test of my `positions_rows` that pins the removed Equity/Rates lines: a change to the Positions rows breaks it, so name it in the Handoff.
- Tests that index `scope_layout(...).children` must pass `with_notices=False` when the fixture has an option with no strike: the banner wraps the body otherwise.
- The new sample (`data/sample/blotter_sample.csv`) loads 43 trades; its 2 rejects are ZCZ6 (ambiguous ZCE:ZC / CBOT:ZC) and QQZ6 (unknown root). Futures currencies: USD, CNY, EUR, GBP, JPY.
- Bash heredocs holding many quotes broke ("unexpected EOF"): write big test blocks to the scratchpad with Write, then append with python.
