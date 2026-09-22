---
name: blotter-fx-sample-cells-removed-2026-09-22
description: FX sub-tab's "(sample)" cells for unpriced rows removed (reviewer finding, user yes 2026-09-22); unpriced cell = "n/a" + reason tooltip; hidden _num columns kept; how to test while another lane's engine file is mid-edit
metadata:
  type: project
---

The FX sub-tab no longer paints "(sample)" figures (fill x (1 +/- offset)) into unpriced
mark / P&L cells. An unpriced cell reads "n/a" (`blotter_fx.UNPRICED_TEXT`, one of
ranking's NULL_TEXTS so it ranks last) with the row's `reason` as the DataTable
`tooltip_data` entry; a row priced at the as-of but blank at T-1 / T-2 while on the book
gets `NO_VALUE_AT_CLOSE` ("no value at this close"), and a close the trade had not been
dealt by (`on_book_t1/t2` = 0) stays blank with no note.

**Why:** reviewer finding, user yes 2026-09-22: an invented number where hard rule 2 wants
the reason, even though it never entered a sum. The 2026-09-17 "sample cells" decision is
reversed; [[blotter-fx-strip-and-samples-2026-09-17]] is history only.

**How to apply:**
- The hidden `pnl_*_num` columns stayed: the visible P&L cell can be the text "n/a", so
  the real-number columns are still the one thing "rows shown" sums (and
  `test_blotter_fx_rows_shown_differences_use_rows_priced_at_both_ends` in test_ui.py,
  which is not sample-related, builds records from them).
- `fx_blotter_rows` carries only the as-of `reason`; T-1 / T-2 have no reason of their
  own (a pnl-engine item if the user wants per-close reasons).
- Tests of an unpriced earlier close need `strict_marks`: the near-marks rule carries a
  lone close to the other days, so without it the T-1 cell is priced (1.108 not "n/a").
- Testing while another lane has an engine file mid-edit (valuation.py missing a name
  ledger.py imports): rsync the tree (minus .git/.venv/*.db) into the scratchpad,
  `git show HEAD:<file>` over the broken file there, run pytest from that copy with the
  repo's .venv python and `-p no:cacheprovider`. No git state touched.
- `ui/tabs/ranking.py`'s `value()` docstring still names a "(sample)" cell (ui-shell's).
