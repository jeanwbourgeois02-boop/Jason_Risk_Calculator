---
name: blotter-fx-by-currency-2026-09-21
description: FX sub-tab's two per-currency tables (fixed whole-book + "rows shown" driven by the trade table's native filter); why the fixed one calls row_scoped_headline per currency, what it costs, the cache guard, hidden real-number columns, timing recipe on a marks-less dev DB
metadata:
  type: project
---

User, 2026-09-21: "pnl breakdown by currency - as fixed tables - also with a dynamic p&l sum
per currency - all the tables above the general table". Built in `ui/tabs/blotter_fx.py`
(+ `.fx-ccy-*` in style.css, one `register_callbacks` line in blotter.py).

**Why the fixed table calls `row_scoped_headline` once per currency instead of regrouping
the frames.** The same day another agent made `_priced_diff_scoped` step a period's
reference close back to the last business day that has value, PER TRADE-ID SET
(`engine/pnl/reference.py::resolve_reference`, entries gain `ref_date_used` / `ref_note` /
`ref_note_detail` / `ref_dates_skipped`). A local regroup of the six date frames would have
silently used different closes from the strip. **How to apply:** any per-group P&L table in
ui/ goes through `row_scoped_headline` / `row_scoped_period_pnl` on the group's trade ids;
show `ref_note` on hover when present; never re-derive the reference dates.

**What it costs (857-trade dev book copy, 19 currencies, machine busy with other agents):**
about 20 `row_scoped_headline` calls = ~380 `_scoped_frame` calls, each a full-book
`df.copy()` + `isin` (that copy is inside blotter_pricing, not avoidable from outside):
0.35-0.7 s uncached, against 0.2-0.35 s for the rest of the sub-tab. Hence
`_BY_CURRENCY_CACHE`, keyed like `priced_value_book` (`_render_cache_key`, as_of), and a hit
is used only if its signature still holds: same trade count per currency AND the same
whole-book Total, which is recomputed every render because it must equal the strip
(0.05 s). That guard is what makes an mtime-keyed cache safe against anything the figures
depend on besides the file (the backfill status file, a future rule change).

**Rows-shown table.** The FX trade table's visible cells are formatted strings, some
"(sample)". Real numbers travel in `hidden_columns` (`pnl_eod_num` / `pnl_t1_num` /
`pnl_t2_num`, `None` for a sample or a blank, plus `currency`, `trade_id`, `on_book_t1/2` =
`trade_date <=` that close, value_book's own on-the-book rule) and come back in
`derived_virtual_data`; the callback reads nothing else (no DB, no State). The difference
rule mirrors `_priced_diff_scoped`: both ends priced, a trade dealt since counts in full, a
one-sided row is left out, blocked > both means n/a. Same pattern as Options' hidden
columns (browser-verified there on 2026-09-18; this table was NOT browser-checked, by
instruction). `FILTER_ROW_CSS` is imported from `ui.tabs.options` for the legible filter row.

**Timing recipe when the dev DB has no marks** (it never has): copy `data/raw/risk.db` to
the scratchpad, insert a `BBG_BFXFORWARD` `FWD_OUTRIGHT` per (FX instrument, leg
settle_date) and a `SPOT` per pair for `as_of` and every `period_reference_dates(as_of)`
date, open it with `connect_readonly`, time inside `pricing_snapshot`. Pre-import
`ui.tabs.blotter` and `ui.tabs.options` first or the first run measures imports (~0.5 s).

**Tests:** `tests/test_ui.py` "blotter FX P&L by currency" block; `_currency_book` fixture
names its INSERT columns and has a cross (EURSEK via USDSEK spot), an unpriced pair and a
future. `"blotter-fx-"` was added to `test_every_static_callback_id_exists_in_layout`'s
dynamic prefixes. `_strip_cards(layout, "fx")` in test_ui_blotter.py finds the strip as the
layout's ONLY `className == "cards"` node: never reuse `cards` / `card` classes in this sub-tab.

**Line endings differ by file and the quick check lies.** `ui/assets/style.css` and
`tests/test_ui.py` are CRLF; `ui/tabs/blotter_fx.py` and `ui/tabs/blotter.py` are LF. The
Edit tool keeps each file's own. `grep -c $'\r' file` in the Bash tool counted EVERY line as
a match (reported 337/337 on an LF file): count `b"\r\n"` with Python against
`git show HEAD:<file>` instead.

Related: [[blotter-fx-standard-convention-2026-09-17]], [[blotter-strips-partial-pricing-2026-09-17]],
[[options-inline-terms-and-headline-2026-09-18]].
