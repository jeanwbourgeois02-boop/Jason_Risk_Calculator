---
name: plain-words-pass-2026-09-28
description: The labels pass of 2026-09-28 over Book, Exposure, P&L, Trades, Data and the header (no engine words, paths or identifiers on screen): formatting.plain_words and the chokepoints it runs in, the recipe that finds engine words on a rendered screen, what Risk still shows, and the test pins that follow the labels
metadata:
  type: project
---

Labels-only pass on 2026-09-28 (after the Risk tab's own pass, `risk.plain_reason`, commit ddff2bd).

- **`formatting.plain_words(text)`** is the one place engine vocabulary becomes a trader's words
  (mark types in a reason: "no FUTURE_PX for" -> "no price for", "no FWD_OUTRIGHT" -> "no forward price",
  "INTERP: FUTURE_PX of" -> "estimated from the close of"; lane names: spreads-engine, curve-positions,
  book-positions; `value_book`, `realised_pnl`, "the ledger has frozen" -> "settled"; QL_OPTIONS_PRICER ->
  "the app's option pricer"; product codes; the synthetic sample path). Ordered phrase list; an unlisted
  phrase passes through unchanged (an engine reason is never invented).
- **Chokepoints that run it:** `about` (title hovers), `marker`, `missing_cell`, `issues_drawer`; Book's
  every `title=` site (`_tip`, the row cells, the group net lines, movers, Needs-you, load line); the
  Options sub-tab's "Not priced" kicker; the header's marks chip breakdown and `_missing_marks_reason`
  (mark types lower-cased words: "no official spot / forward for <date>").
- **Kept out of `plain_words` on purpose:** `config/*.yaml` paths and "underlyer" (Risk's own strings;
  `tests/test_ui_risk.py` pins "not set in config/limits.yaml" and the margin basis). Risk still shows
  those in its hovers and the "Not set" lines: a follow-up for the Risk pass, not this one.
- **Left as found:** the Options sub-tab's MARS-style column names ("MktPx (current premium)", "UndFwdPx")
  are the user's own layout (2026-09-21); the Data tab's manual mark-entry dropdown lists mark types by
  name (its vocabulary); the Trades table tooltips (`_format_rows`) still carry the raw reason
  ("no FUTURE_PX") because `tests/test_ui_blotter_commodity.py` pins them.
- **Recipe that finds engine words on screen** (scratch `dump.py`): render every tab on
  `data/raw/sample.db` (from `ui.sample_book.build_sample_db()`) and on the empty `risk.db`, walk the
  component tree collecting strings, `title=`, DataTable `columns[].name`, `tooltip_header`, dropdown
  `options[].label` and figure titles, then grep for mark types, lane names, paths and identifiers.
  Trades and Data render through their callbacks (`app.callback_map`), as `tests/test_ui_smoke.py` does.
- **Test pins that follow the labels:** `test_header.py` (`_missing_marks_reason` words),
  `test_ui_formatting.py` (`about` hover), `test_ui_blotter.py` (the headline strip's excl. marker).
- **Bash tool note:** a quoted heredoc here halves backslashes (`"\n"` in a pattern became a newline);
  write patch scripts with the Write tool when they carry backslashes.
