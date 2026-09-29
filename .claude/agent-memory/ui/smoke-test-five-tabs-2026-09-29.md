---
name: smoke-test-five-tabs-2026-09-29
description: How tests/test_ui_smoke.py reads the Phase G app (five tabs): body renders, drawer item count through static Markdown, Book line identity via book.totals
metadata:
  type: project
---

`tests/test_ui_smoke.py` was re-pinned to the five Phase G tabs on 2026-09-29 (14 tests). What it leans on, so a later rebuild knows what to keep or re-pin:

- Bodies: `book.render`, `pnl.render`, `risk.render` return one Div; Blotter and Data through their body callbacks (`..blotter-content.children` called `(AS_OF, "total")`, `..market-data-body.children` called `(AS_OF)`), keeping only the outputs that are Components.
- Shell: one callback (`build_tab_bodies`) owns every `tab-body-<key>.children` and `.style`.
- Drawer count: `issues_drawer` bodies go through `formatting.static_runs`, so plain `<li>` rows are one `dcc.Markdown`; the test counts `<li` in it.
- Blotter drawer present iff `blotter_fills.fills_frame(...)[1]` issues are non-empty.
- Book identity: `book.gather` -> `data["trades"]` covers every fill once; `book.totals(data, trades)[key][0]` equals `data["views"][key].entry["value"]` and the header card for daily / mtd / ytd / ltd; the first Tbody row has class `book-total` and text starting "Book ·".

**Why:** each of these moved in Phase G / the performance pass and broke the old test silently at import.
**How to apply:** if one of these names changes, re-pin the test to the new name; never loosen the Book = header or P&L sums = header identity.
