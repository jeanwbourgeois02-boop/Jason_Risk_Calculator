---
name: book-research-context-polish-2026-09-28
description: Book pair rows carry the research app's z, %ile and a sparkline (read-only, never in a figure; the user reversed "no market context on the monitor" on 2026-09-28); the Book's sections read the research app's shape (small-caps titles + meta, borderless tiles, hairline table heads, one Loaded line); proof recipe
metadata:
  type: project
---

User, 2026-09-28 (evening, after [[book-summary-first-2026-09-28]]): "lets fix the market context -
but also polish". This reverses the Phase E rule "no market context on the monitor" for the Book's
pair rows only (CLAUDE.md still says the rule: the housekeeper owns that rewrite). What is true in
`ui/tabs/book.py` / `ui/assets/style.css`:

- **Research columns** on a pair row of the Positions fold, after Move, a column group headed
  "research" in grey (a two-row `thead` in `book_table`; every spanning cell is `3 + len(RESEARCH_COLUMNS)`
  / `11 + len(RESEARCH_COLUMNS)` wide, 14 columns in all): z (`z_primary`, signed), %ile (`pctile_5y`,
  no decimals), a 90 x 18 sparkline (`sparkline_src`: an inline SVG as a data-URI `html.Img`, the
  browser draws it, no plotly; a year of `spread_daily`, the entry level a dashed line when within
  half a span of the history's range, the last point a dot). One hover for the three (`research_hover`).
  Every other row: three empty cells. Never in P&L, delta or a total; the CSV carries
  `research_key`, `research_z`, `research_pctile`.
- **Key**: the pair's `template` id, else spreads-engine's own `research_key(spec_from_dict(level_spec))`
  when the spec's kind is `CALENDAR` (`cal.<exchange>_<code>.<near>_<far>`, instance = the near year);
  a `KIND_PAIR` spec (no template, not a calendar: feeder vs live cattle) has no research row. The
  read is `_research(data, as_of)` in `gather` (one `research_spread_stats` call for every pair key,
  then `research_spread_history` per key), memoised with the rest of `gather` on the risk db's
  revision, so a research-db update shows after the next risk-db change or restart. A reason
  strips the research db's file path (the kit: no paths on a hover).
- On this PC the research db (`../Commodity Dashboard/var/rv.sqlite`) has one stats run, asof
  2026-09-28, and history from 2020-09-28: a render at an earlier as-of finds no stats ("no
  research statistics ... on or before"); the SGX iron ore and LME zinc calendars and the CME HRC
  Oct/Dec calendar are not in its universe (dashes with the reason). Jason's real book at today
  shows z / %ile / sparklines on 6 of its 12 pairs.
- **Polish**: the title row is the "Book" heading alone (the counts sentence left); `section_head`
  (title `about` h4 `.book-section-title` small-caps grey + `.book-section-meta`) over By strategy
  (meta "P&L by spread type; the Book line is the header", Download CSV at its right, the section
  static in `layout` so the button is never re-inserted: `SUMMARY_SECTION_ID` style + `SUMMARY_TABLE_ID`
  children), Break it down (meta + switch) and the Positions fold (its `html.Summary` is the head:
  title span + `POSITIONS_SUMMARY_ID` meta "· 23 · strategy → trade → pairs → legs" (`fold_meta`,
  `FOLD_WORDS` per view) + the switch; a CSS chevron replaces the marker a flex summary loses).
  `parts()` returns `summary` apart from `top`; `counts` is now the fold meta. The Loaded banner is
  `loaded_line` (one muted line under the tiles, gold "LOADED" label; `loaded_banner` is an alias).
  Tiles borderless label-over-figure (`.book-tile`), table heads white with a hairline, 10.5 px
  small-caps muted, body 13 px; the group / strategy lines' legs sentence wraps (`td.l` normal),
  which is what keeps the 14-column table inside 1680 px on the sample book.

**Why:** the user wants the research app's z / percentile beside each pair, and the research Book's
look (`../Commodity Dashboard/rvapp/app/assets/style.css`), with less repetition on the Book.
**How to apply:** proof recipe in the scratchpad: `verify_research.py` (a sample copy built by
`ui.sample_book.build_sample_db(path, through=today)` with strategies set by SQL, and a copy of
`data/raw/risk.db`, both at today: pair research cells, the two-row head, 14 spans per row, Book
line = header in the three views, csv columns, body()), `build_app.py` (no duplicate ids / outputs),
`shot2.py <db> <port> <png>` for the 1680 px screenshot, `overflow.py <db> <port>` (a `--dump-dom`
count of the research parts). Chrome on the same port twice times out: change the port.
