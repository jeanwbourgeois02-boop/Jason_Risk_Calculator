---
name: risk-tab-layout-decisions
description: How the Risk tab (ui/tabs/risk.py) is laid out and why; caption line + Data issues drawer, k/m cards with markers, commodities-first single-line table, n/a-vs-blank rule, pinned Book row; built 2026-09-22, redesigned 2026-09-25 (Phase A)
metadata:
  type: project
---

Built 2026-09-22 (the PM's nm-dashboard metrics on this book); redesigned 2026-09-25 under
CLAUDE.md "Screens redesign plan" Phase A (user: numbers first, definitions on hover of the
titles, every reason in one collapsed "Data issues (N)" drawer). The tab renders
`engine.risk.book_risk` and margin-limits' results and computes nothing.

**Why:** "Tabs as views" (no UI recompute) and the rule that no figure is blank without its
reason and never zero for a missing input. The 2026-09-24 tab was ~10,000 px tall: 25 lines
of folder paths before the first figure, 20-line card notes, 100 px FX rows, 160 px Book row.

**How to apply (decisions in force, keep unless the user says otherwise):**
- Top: `caption_parts` = one nowrap line (as-of, FX history to X, commodity history to Y,
  vol target in k/m + "placeholder" marker), each part's full sentence as its hover. Then
  `issues_drawer(issue_items(result, margin), id=ISSUES_ID)`: history (or folders tried) with
  its files, commodity history, parameters, config/history notes, `shown_missing`,
  commodity-stress `reasons`, margin `reasons`. No other reason lists on the tab.
- Cards: exactly 3 children (label, figure line, one clipped note line). Figure via
  `short_money(v, parens=True)`, full figure as the value's hover; markers on the figure line
  ("excl. N" from `delta_missing` / the engine's "excludes N" sentence, "trailing only" for
  vol_note); flag tag beside the figure. A NaN card: "n/a" + reason as hover AND as the
  clipped note (the header's old lesson: hover-only reasons were reported as "not working").
  The FX & cash tab's FX net/gross USD is on the Net/Gross card hover (left the header).
- Key table: commodities grouped by sector FIRST, then FX/metal in engine order, Book pinned
  (`ranking.with_footer`, skip_widths underlyer+note). Money columns `rk.amount_short` over
  `rk.whole_units` (records keep raw values; the table data is rounded). Underlyer and note
  clipped (`_clip`: nowrap + ellipsis at fixed ch), note's full text as its tooltip.
- Cell rule unchanged: missing = "n/a" string + tooltip reason; not applicable = None blank;
  Book "Worst ex vs target %" blank with a tooltip (measured against the cap on the card).
- Definitions: `about(title, hover)` on every section title; no Definitions block, no kicker
  paragraphs outside collapsed blocks except the limits' one-line count.
- Order: cards, underlyers, views, commodity scenarios (detail in one outer collapsed
  Details), FX scenarios ("FX & cash tab's" scenarios), margin, limits.
- Limits (user yes 2026-09-25, "can you apply these"): the table holds only the checks that
  are set (OK / WARN / BREACH / N/A); every NOT_SET check is collapsed under it into one
  `issues-drawer` line "Not set (N)" (`not_set_drawer`, id `risk-limits-not-set`), grouped
  Desk (Book, Net USD by sector, each root) then Exchange (each root), each check one inline
  item with its position, basis + config key on hover. Nothing set: one quiet kicker line
  + the drawer, no table. ~104 of ~110 checks are NOT_SET on the placeholder config.
- "carry included" only on rows with figures; flags carry words + `var(--neg)`; no date
  picker (header as-of store, revision, own safety interval).

Related: [[commodity-sections-2026-09-24]], [[dash-component-behaviour]].
