---
name: daily-series-2026-09-29
description: engine/pnl/series.py (2026-09-29, user yes under rule 7): one filled valuation per business day, memo in process only, period/monthly/track-record rules (header's rule), realised = settled, weekend as-of
metadata:
  type: project
---

2026-09-29 the user approved the rebuilt P&L tab ("yes to all of this including the change to
rule 7"). I own `engine/pnl/series.py`: `daily_series` (value_book + fill_book per business day,
same rows as ui's `priced_value_book`), `period_pnl` / `periods`, `monthly_pnl`, `track_record`.
Verified equal to the header's figures to the cent on the sample book (as-of 09-04, 09-18,
09-29) and on a copy with holes; the real risk.db (no marks) gives n/a everywhere, as the header.

**Why:** the header, Book and P&L each re-priced the same dates; the series is the one per-day
valuation the ui is to switch its LTD chart (`header._build_chart` / `_cached_ltd`) onto.

**How to apply:**
- The memo is IN PROCESS, keyed (path, mtime, size, day); never write it to the database
  (a write changes mtime and loops every mtime-keyed cache). A new revision drops older ones.
- A period is `resolve_reference(..., frames_filled=True)` + `diff_split`: never re-derive the
  rule; the per-trade sum equals the header by construction (contributing_b ⊂ contributing_a).
- Track record uses the header's Daily rule per day (user yes 2026-09-29, reversing the strict
  first build): `period_pnl(prev bd, d)`, trades left out listed per day as excl. N; a day is
  dropped only when the header would show n/a. Drawdown on the LTD by `_priced_single`'s rule.
- Realised = SETTLED only (user yes 2026-09-29): a closed-out option (CLOSED) is open until expiry,
  as `ui/tabs/pnl.py::realised_entries`; sample LTD realised -18,843.39.
- Drawdown starts from LTD 0 on the business day before the first trade.
- A weekend / holiday as-of (book_today is a Saturday from Fri 17:00 NY) is appended to `days` and valued like any day; reference closes still step on business days; the track record skips it. Reviewer finding 2026-09-29: without it `frame(<weekend>)` raised KeyError.
- Consumers: spreads-engine (period explain on `PeriodPnl.frame_end` / `frame_ref`), ui.

Related: [[lane-inheritance]]
