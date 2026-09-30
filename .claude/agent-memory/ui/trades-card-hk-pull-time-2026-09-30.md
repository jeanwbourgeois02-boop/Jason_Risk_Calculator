---
name: trades-card-hk-pull-time-2026-09-30
description: Data tab Trades card (every trade problem in one place, parse check inside it), Blotter line links to it; pull times in HK via feed_controls.pull_time; heredoc backslash trap
metadata:
  type: project
---

User 2026-09-30: "any issue with the trades it shows up in a trade pull section in the data tab"; "for the last pull i would like the date and the time in hong kong".

- Trades card `market_data.TRADES_CARD_ID` = `market-data-trades`, after the Bloomberg card: head (`TRADES_HEAD_ID`: last upload line + "Trade problems (N)"), table slot (`TRADES_SLOT_ID`, filled from `TRADES_STORE_ID` + funnel store + sort store), then `parse_block()` (the old Parsing card, same PARSE_* ids; `PARSE_CARD_ID` retired).
- Rows: `data_checks.trade_problem_rows(last_upload_issues, inventory.unrecognised)`; statuses NOT RECOGNISED / NOT LOADED / WARNING / FILE / SKIPPED; `trade_problem_words` gives "2 need a fix · 1 warning", shared by the Data status line (`trades_link`) and the Blotter line (`blotter_fills.trades_pointer`, a tab_link idx `blotter-trade-problems`). Problems table no longer lists unrecognised contracts (still excluded from its "No P&L" rows).
- `render()` now returns 15 outputs: 13 = trades head, 14 = trade rows (list). ui_check's render_data does not fill 11-14 (request to infra made 2026-09-30).
- Blotter: rejects fold, file-warning lines, `REJECTS_ID`, `NOT_LOADED_LINK_ID` and their clientside callback are gone.
- Pull times: `feed_controls.pull_time` -> "Wed 30 Sep 13:36 HK" (always the date). Used by feed_headline, bar_state, no-report line, data_checks pull_facts/bloomberg_line/pull_problems, the status line, the Bloomberg check's run time. Closes stay stamped 17:00 NY, but the header Marks chip shows the stamp in HK since 2026-09-30 (user: "gives ny not hk time"; `header.mark_time_words`, date and time both from the HK-converted stamp, "Sat 26 Sep 05:00 HK"). Blotter upload times in HK too (`blotter_fills.hk_time` / `hk_day`); `ny_time` kept only for the Book's mark-stamp hover.
- Card lines carry `tk-headline data-bbg-line` so ui_check counts them as the headline above the table (plain `data-bbg-line` is LOOSE_BLOCK).
- Visible upload sentences: `data_checks.pb_root_words` turns 'JSHY10.3_ZNA1' into "ZNA1 marked cross-exchange" (ENGINE_WORD "code" otherwise); raw text only in the CSV.
- Trap: a python heredoc with `\\b` inside ''' strings wrote a backspace byte into a regex; write regex edits with the Edit tool.
