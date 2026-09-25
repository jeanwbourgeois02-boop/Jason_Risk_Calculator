---
name: data-tab-phase-a-2026-09-25
description: Screens redesign Phase A on ui/tabs/market_data.py — tab is "Data", section order, quiet lines, about() hover, Data issues drawer, 10-output body callback, reference_panels split
metadata:
  type: project
---

Screens redesign Phase A (user-approved plan 2026-09-25, CLAUDE.md "Screens redesign plan"): the Market data tab is now labelled "Data" (ui-shell owns the label; body id stays `tab-body-market-data`, every `market-data-*` component id unchanged).

Order on screen (`build_layout`): title `about("Data", ...)` h3 -> toolbar (date, Pull now, status; the PAIR dropdown moved OUT of the toolbar into the FX section card) -> "Data health" (ISSUES_ID drawer, MISSING_PANEL_ID = rows not loaded + trades left out + what is missing, SUSPECT_PANEL_ID) -> FUTURES_CURVES_PANEL_ID -> FX section (pair dropdown + BODY_ID) -> REFERENCE_PANEL_ID (library + contract dates) -> close completeness + PAST_CLOSES -> manual entry -> Bloomberg check.

Conventions adopted (user's plan decisions, keep them):
- Definitions never a paragraph: `_panel(title, children, about_text=...)` puts them on hover via `ui.tabs.formatting.about`; a Details summary carries them as `title=` + class `about-title`.
- Nothing to report = `_quiet(title, sentence, about_text, extra=[...])`: one status-line Div (class `quiet-line`), never a `section` card. Past closes all complete and suspect with nothing flagged keep their table collapsed in `extra`.
- Reasons that were visible paragraphs (pull_note, suspect's "no marks on prev day", each futures missing-price reason) are now a `marker(...)` with the sentence on hover AND appended to an `issues` list; `_render` passes one list through `whole_book_panels(..., issues=)` and `futures_curves_panel(..., issues=)` and renders `issues_drawer(issues)`. Panels called without `issues` still show the marker, so nothing is silent.
- Missing panel drops the "Bloomberg's reason" column only when every row's reason is blank (the marker says why). Futures tables drop "Why missing" when the block has no missing price; missing prices also get DataTable `tooltip_data` on the price cell.
- `whole_book_panels` still returns a 3-tuple but no longer holds library / contract dates: those are `reference_panels(conn, as_of)`. `_update_body` now has 10 outputs (+REFERENCE_PANEL_ID, +ISSUES_ID).
- MISSING_ABOUT rewords the old "shows no P&L until it arrives" to the near-marks rule (hard rule 2): priced off the nearest official marks, left out with its reason when none.

**Why:** user found the UI poor for commodity RV (2026-09-25); plan: numbers first, definitions on hover, one drawer of reasons, quiet lines for empty sections.
**How to apply:** new sections on this tab follow the same helpers; test the order with `str(layout).index("'<id>'")`. `str(component)` includes `title=` props, so hover text is still assertable with `in str(...)`.

Also seen: `py -3 - <<'EOF'` worked this time (contrast [[futures-curves-phase3-2026-09-24]]); still prefer a scratch file if it hangs. Related: [[market-data-tab]], [[market-data-whole-book-panels-2026-09-21]].
