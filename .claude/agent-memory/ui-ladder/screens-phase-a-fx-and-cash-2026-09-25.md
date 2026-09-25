---
name: screens-phase-a-fx-and-cash-2026-09-25
description: Ladder tab became "FX & cash" (Screens redesign Phase A) - futures table/rows gone, definitions on hover, markers + Data issues drawer, k/m card and risk table, n/a grid cells; test-helper gotchas
metadata:
  type: project
---

2026-09-25, CLAUDE.md "Screens redesign plan" Phase A (user-approved). Supersedes the
futures parts of [[phase3-fx-only-net-gross-2026-09-24]].

- Tab title `cash_ladder.TAB_TITLE` = "FX & cash"; button "Cash ladder CSV" (file name
  still cash_ladder.csv); grid title "Cash ladder" (`exposure.GRID_TITLE`).
- This tab is the ONLY home of FX Net / Gross USD delta (header dropped them). Card in
  `short_money(..., "$")`, whole figure on hover (`exposure.full_usd`), definition on the
  card label's `title` (`HEADLINE_ABOUT`). `cash_ladder.net_gross_usd` kept unchanged.
- Open futures table, `futures_table*`, `DEFAULT_FUTURES`, `FUTURES_NO_SCENARIO` and the
  risk table's futures rows are DELETED; `combined_risk_frame/table(result, scenarios,
  fallback_ccys)` lost the `futures` arg; `exposure_section` lost `futures` /
  `futures_details`; `load_inputs` no longer returns "futures". One line
  (`futures_note()`, `COMMODITY_NOTE`, id FUTURES_NOTE_ID) points to Curve and Risk tabs.
- Kicker paragraphs -> `about()` hover (grid title = `usd_basis_caption`, id kept; risk
  title; Position table; Local vs USD summary). Rate problems / no-curve / settled
  tickets -> `marker()`s beside the grid title (ids RATE_REASONS_ID, SETTLED_CAPTION_ID kept)
  and once in `issues_drawer` (ISSUES_ID), which names the WHOLE book's missing rates
  (exposure_result + grid result), not just the filtered view.
- Grid: a currency with MISSING/SUSPECT rate shows "n/a" in fx_rate/usd_delta with a
  tooltip (`_mark_unpriced_cells`, reads the summary frame's rate_source row); footer
  blank USD-equivalent cells -> "n/a" + tooltip. The summary frame itself still holds "".
- Risk table: `rk.amount_short` + `rk.whole_units` on rows; footer only on "usd_delta"
  (whole_units turns '' into None = em dash, so never on the empty footer cells).

**Gotchas:** `about()` returns a heading whose children are a LIST `[title, Span]`, so a
test helper that only recurses into components drops the plain string; `_render_text` in
tests/test_ui_ladder.py now returns plain strings. Hover text lives in `.title`, never in
rendered children. Portfolio gross on the RECORDS fixture is 1,000,680 (USD not counted).
