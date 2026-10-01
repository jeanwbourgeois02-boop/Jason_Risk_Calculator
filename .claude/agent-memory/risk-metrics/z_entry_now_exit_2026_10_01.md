---
name: z-entry-now-exit-2026-10-01
description: The three z-scores per spread (entry, now, exit) in engine/risk/trades.py: calendar-year window (z_window_years), per-spread entry from its legs' first fill, closed trades' spec via trade_book on their open_date, output keys.
metadata:
  type: project
---

User decision 2026-10-01: the Book shows z at entry, now and at exit per spread, each against the
level's closes in the 1 CALENDAR year ending that day (not the last 252 closes; `z_window_years` in
config/risk.yaml; level_window_bd stays the window of level_sd / move_sigma only).

- Per spread = every `sub_spreads` entry of trade_book (no hard-coded shape: spreads-engine splits a
  part into several equal-size spreads from 2026-10-01). Entry = earliest trade_date of the fills on
  the legs matching the spread's spec legs (`_spread_entry_date`); exit = `closed.close_date`.
- Closed trades are not rows of position_risk normally, so the top-level `spread_z` {position_id: ...}
  covers every trade_book trade. trade_book's `closed` block carries no spec (asked spreads-engine for
  `closed.spec` / `closed.sub_spreads`); until then `_book_spread_z` calls the public
  `trade_book(conn, closed.open_date)` once per distinct open_date and reads the trade there.
- A window shorter than a year but >= level_min_days closes still gives z, with `note` and
  `full_year` False; fewer is None with "the history starts ..., after the start of the year to ...".
- Checked on Jason's export + synthetic history (scratch rm_setup.py / rm_setup_closed.py closing
  STEEL on 28/09/2026): cold 0.44 s, warm 0.004 s.

**Why:** user decision relayed by the housekeeper, 2026-10-01.
**How to apply:** row keys z / z_entry / z_exit come from `spread_z[pid]["trade_level"]`; a new
spread shape only needs its `level.spec` in sub_spreads. See [[trade-risk-2026-09-29]].
