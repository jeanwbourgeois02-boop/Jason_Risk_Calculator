---
name: z-on-price-ratio-2026-10-01
description: Since 2026-10-01 z (entry / now / exit) is on spreads-engine's PRICE RATIO when a spread has a ratio_spec, with ratio_usual (window mean); falls back to the level series (z_basis 'level'); how the history is built and what the scratch check showed.
metadata:
  type: project
---

User 2026-10-01 ("their usual ratio, z score - this at entry and now"): per spread, z on the price
ratio (both legs USD, Chinese leg on top), plus `ratio_usual` = the mean over the same 1-calendar-year
window as z now (a closed spread: z at exit). `ratio_usual_entry/_now/_exit` per point; each `_z_point`
dict carries `mean` / `sd`.

- Definition is spreads-engine's (`engine.spreads.ratio.ratio_spec` on each `sub_spreads` entry, and on
  the trade only when it is exactly one spread; `ratio_value` the formula). `trades.ratio_history`
  reads each side via `_leg_prices` (adapter `_RatioLeg`: LME leg = instrument_id == root_id at its
  month), times `_usd_per` (price_history's FX, CNY via USDCNH), inner join on both legs' closes.
- No ratio_spec / no ratio history -> the old level series, `z_basis` 'level', `z_basis_reason` says
  why; neither -> z_basis '' with "no z: ..., and ...". Never mixes bases across the three points.
- Trade row: trade's own ratio_spec, else the one sub whose level spec is the trade's level (STEEL:
  2 calendars, the trade row reads its level spread's ratio z). level_sd / move_sigma stay on the level
  series (the Book's level move is in level units).
- On the ratio basis `level_kind` = 'ratio', point `level` = the ratio (the ui z hover reads them).
- Scratch (rm_setup.py real|sample, rm_print.py): history's last point = trade_book's ratio_now to
  0.000 % with USDCNH set equal to USDCNY; an LME-vs-LME pair differed 0.02 % (history reads the
  month's third-Wednesday prompt, the Book the ticket's own prompt).

- 2026-10-02 (user approved, ZNA1 on the Bloomberg PC: Ratio now above Usual but Z now -0.9): on the
  ratio basis z_entry / z_now are now the BOOK's own `ratio_entry` / `ratio_now` (passed as
  `book_ratio`, open trades only, `_book_ratio`) against the window's mean / sd, point `source`
  'book'; the exit and a missing Book figure read the last close (note says so). Cause was: z read
  the history's last close (to yesterday; SHFE shut for Golden Week so 30 Sep), Ratio now read
  today's marks. Rule: sign(z now) = sign(ratio now - usual), |gap| / |z| = window sd. Scratch
  check: rm_setup.py real + rm_perturb.py (history ends 30 Sep, SHFE zinc -15 %) + rm_zcmp.py
  (old module from `git show HEAD:` as rm_trades_old.py vs new).

**Why:** user decision relayed by the housekeeper 2026-10-01.
**How to apply:** a new spread shape needs only a `ratio_spec` from spreads-engine; see
[[z-entry-now-exit-2026-10-01]] for the window and entry / exit dates.
