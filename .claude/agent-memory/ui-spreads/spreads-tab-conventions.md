---
name: spreads-tab-conventions
description: How the Spreads tab (ui/tabs/spreads.py) is built on book_spreads, the filled reader, legs as collapsed blocks, Total-line rule
metadata:
  type: project
---

Spreads tab (built 2026-09-24, Phase 3) renders `engine.spreads.book_spreads(conn, as_of, value_fn=priced_value_book)` inside `pricing_snapshot`; the engine accepts the reader's `(df, n_filled, n_total)` tuple directly, no wrapper needed.

- Shell interface copies Curve / Expiries: `layout(default_date)` = `build_layout`, `register_callbacks(app, get_db_path)`, `BODY_ID = "spreads-body"`, inputs = header as-of store, data revision, safety interval. No date picker.
- Legs "nested under each row" are one collapsed `html.Details` per spread under the ranked table, same order, because a DataTable cannot nest rows without the Options tab's heavy click-to-expand machinery. If the user asks for true in-table expand, copy `ui/tabs/options.py`'s collapsed-store pattern.
- Open spreads ordered by |LTD| (n/a last) with a "#" column; closed spreads in a collapsed Details below; Total line via `ranking.with_footer`, summing known figures only, "excludes N" on hover and in the Legs column.
- Leftover USD per spread is the engine's per-root usd_notional added up (display sum); any root None -> n/a.

**Why:** screens never recompute (CLAUDE.md "Tabs as views"); the fill must match the header.
**How to apply:** when spreads-engine's output shape changes, only `spread_record`, `leg_record`, `outright_record`, `review_record` need touching. Tests use a fake result plus a tmp_path book with a stub `ui.app` module (like test_ui_curve).
