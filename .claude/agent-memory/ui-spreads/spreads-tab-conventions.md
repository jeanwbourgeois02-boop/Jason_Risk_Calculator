---
name: spreads-tab-conventions
description: How the Spreads tab (ui/tabs/spreads.py) is built on book_spreads, the filled reader, legs as collapsed blocks, Total-line rule, Phase A display rules (hover titles, drawers, k/m)
metadata:
  type: project
---

Spreads tab (built 2026-09-24, Phase 3) renders `engine.spreads.book_spreads(conn, as_of, value_fn=priced_value_book)` inside `pricing_snapshot`; the engine accepts the reader's `(df, n_filled, n_total)` tuple directly, no wrapper needed.

- Shell interface copies Curve / Expiries: `layout(default_date)` = `build_layout`, `register_callbacks(app, get_db_path)`, `BODY_ID = "spreads-body"`, inputs = header as-of store, data revision, safety interval. No date picker. Only `BODY_ID` is read outside the lane (tests/test_app.py).
- Legs "nested under each row" are one collapsed `html.Details` per spread under the ranked table, same order, because a DataTable cannot nest rows without the Options tab's heavy click-to-expand machinery. If the user asks for true in-table expand, copy `ui/tabs/options.py`'s collapsed-store pattern.
- Open spreads ordered by |LTD| (n/a last) with a "#" column; closed spreads in a collapsed Details below; Total line via `ranking.with_footer`, summing known figures only, "excludes N" on hover and in the Legs column.
- Leftover USD per spread is the engine's per-root usd_notional added up (display sum); any root None -> n/a.

Phase A of the Screens redesign plan (2026-09-25) set the display rules:
- Definitions live on `formatting.about(...)` title hover (tab title, Open spreads, Outrights, Legs; the Closed Summary carries `title=`); no `section-kicker` paragraph. Caption = one meta-line of counts.
- `Data issues (N)` drawer (`ISSUES_ID`) gathers the engine's book `reasons` plus each spread / outright's n/a period reasons (grouped by reason).
- For review is a collapsed `issues-drawer` Details (`REVIEW_ID`), one nowrap `Li` per group inside the Ul `REVIEW_TABLE_ID` (the old table id kept), full reason on `title`; a marker "off X%" from the candidates' max `deviation`. No drawer when empty.
- Spreads tables' USD columns (periods + leftover_usd) use `rk.amount_short` over `rk.whole_units` (Total too), full figure first in each tooltip. Outrights are trade rows: full figures (`rk.amount`), compact padding, "why" one line with ellipsis. Legs tables keep full figures.
- Size is a text cell "10 lots" / "5,000 bbl" (`size_text`); the size_unit column is gone.

**Why:** screens never recompute (CLAUDE.md "Tabs as views"); the fill must match the header; the redesign's "numbers first, definitions on hover, trade rows full figures" rule.
**How to apply:** when spreads-engine's output shape changes, only `spread_record`, `leg_record`, `outright_record`, `review_record` need touching. Phase B rebuilds the tab around spread levels (entry / now / sigma / research z-score) once spreads-engine gives them: do not invent those figures before. Tests use a fake result plus a tmp_path book with a stub `ui.app` module (like test_ui_curve).
