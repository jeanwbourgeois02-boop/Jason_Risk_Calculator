---
name: risk-tab-layout-decisions
description: How the Risk tab (ui/tabs/risk.py) is laid out and why; the n/a-vs-blank cell rule, the pinned Book row, card hovers; built 2026-09-22 for the PM's nm-dashboard metrics
metadata:
  type: project
---

The Risk tab was built 2026-09-22 from the housekeeper's brief: the PM wants the risk
metrics of his nm-dashboard project (blended vol, 1y 95 % VaR, worst day ex shocks /
raw, scenario stress) on this book, whole book and per underlyer. Everything on it is
`engine.risk.book_risk`'s dict rendered; the tab computes nothing.

**Why:** CLAUDE.md "Tabs as views" (a UI file never recomputes a metric) and the user's
rule that no figure is blank without its reason and never zero for a missing input.

**How to apply (decisions taken, keep them unless the user says otherwise):**
- Cell rule in the key table: a number is stored as a number (ranking); a MISSING figure is
  the string "n/a" (ranks last via `ranking.NULL_TEXTS`) with its reason as the cell
  tooltip; a figure that DOES NOT APPLY to the row is None, blank (the old example, DV01
  on a currency, left with the rates rows on 2026-09-24: [[retired-macro-underlyers]]). Column formats use `nully=""` so the two cases look different.
- The Book is the pinned footer (`ranking.with_footer`), kind "Book"; its "Worst ex vs
  target %" cell is blank on purpose (the engine measures the book against the CAP, the
  card shows "x % of cap"), with a tooltip saying so.
- "carry included" goes in the Note only on a row with figures (`days > 0`): the engine's
  `carry` flag is true whenever a carry series exists, even for an unsized row (e.g. a metal with
  no spot), which would read as nonsense.
- Cards: the definition is the card's `title` (hover); a NaN card shows "n/a" + the reason
  as the note AND as the hover (the header's lesson: hover-only reasons were reported as
  "not working"). A long list of reasons (e.g. 18 FX options without DELTA) is
  summarised "first (+17 more on hover)" with the full list in the hover.
- Flags: `over_cap` / `over_vol_target` add the words "over cap" / "over vol target" as a
  tag plus the negative colour inline (`var(--neg)`): ui-shell owns the CSS, so no new
  class; the words make the flag readable without the colour.
- The currency x scenario matrix is collapsed by default (`html.Details`), the scenario
  table above it open; header wrap on the long scenario names as exposure.py does.
- No date picker: the tab follows `header.AS_OF_STORE_ID`, re-renders on
  `revision.DATA_REVISION_ID` and on its own `dcc.Interval` (`safety_refresh_ms`) because
  the parquet history lives outside the database and a fresh nm-dashboard pull moves no
  revision.
- Percent formats: "Worst ex vs target %" is unsigned (a share of a target), built with
  dash `Format` directly (`_pct_format`) rather than `ranking.percent`, which is signed.

Related: [[dash-component-behaviour]].
