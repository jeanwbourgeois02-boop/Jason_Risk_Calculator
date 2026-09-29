---
name: display-text-pass-2026-09-29
description: Capitals / plain names pass run against `py 2_launcher.py ui-check` - cap, cap_lines, tidy, plain_ids in formatting.py; where tidy is called; the traps met
metadata:
  type: project
---

User 2026-09-29: "so many things not capitalised ... its not acceptable". Every visible text AND hover starts with a capital; no n/a / nan / lone None; no Bloomberg tickers or root ids in visible text (ticker on hover); no loose text. The measure is `py 2_launcher.py ui-check` (tools/ui_check.py, infra's file; stricter since that day: only "z" whitelisted, hover LOWERCASE/BANNED fail the run). Ended at 0 findings, exit 0.

**How it is done (ui/tabs/formatting.py):**
- `cap(text)` first letter up (skips glyphs, keeps "vs"/"x"/"z", never touches a lone nan/none/n/a so the checker still sees a real bug); `cap_lines` per line of a hover.
- `tidy(tree)` caps every `title`, DataTable `tooltip_data`/`tooltip_header`, and `title="..."` inside static-block markdown. `_to_jsx` caps titles; `compact` caps titles of kept nodes; `marker`/`missing_cell`/`about`/`money_cell`/`km_cell`/`date_cell` cap.
- tidy is called where the checker renders (it bypasses the callbacks): `book._tidy_parts` at the end of book/pnl/risk `render_parts`, `risk.render_folds`, `header._build_figures`, blotter `_update` outputs, `blotter_fills.fills_table`, `data_checks.problems_table`/`marks_table`, `market_data.render`.
- `plain_ids(text)`: 'CLZ26 Comdty' -> 'WTI Dec26', 'CUZ26C 80000 Comdty' -> 'SHFE copper Dec26 80,000c', 'CBOT:ZC' -> 'Corn', via `load_roots` bbg_root map. Applied in `issues_drawer` (label + sentence), risk price-check fold, options labels / breakdown groups, Data library "Used for".
- `size_words(..., capital=True)`: "Long 15 lots"; pass `capital=False` mid-sentence.
- `_PLAIN_PHRASES` gained book_positions and UNRECOGNISED wording.

**Traps:** a DataTable `filter_query` matching a lowercase phrase breaks when the cell is capitalised (options `{note} contains 'no strike'` became `'o strike'`). A whole-title "nan" came from a NaN `snapped_at` (`data_checks._tip`). Python heredocs through the Bash tool collapse `\\n` / `\\u00b7` into real characters: use the Edit tool for any string with a backslash. `tests/test_ui_smoke.py:238` pins the old "5d n/a:" hover (now "5d not available:").
