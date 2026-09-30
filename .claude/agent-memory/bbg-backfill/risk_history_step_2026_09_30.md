---
name: risk-history-step-2026-09-30
description: 2026-09-30 risk_history_step in backfill.py fills price_history (Risk tab's daily settle/volume/OI from Bloomberg, never marks); edges-only asks, late-OI refresh, own _Asks and status block, real pull only
metadata:
  type: project
---

User decision 2026-09-30: the Risk tab stops reading the research app's rv.sqlite; its history
comes from Bloomberg into `price_history` (ingest-schema's table; needs from bbg-library's
`risk_history_needs(conn, as_of)`). Built as `backfill.risk_history_step(db_path, session=,
fetch=, today=, log=)`, run from `start_auto_backfill._run` only when `real_pull`, after
auto_backfill (closes + closing ledger), before `_save()` (snapshot).

Choices worth remembering:
- **Edges only:** asks [start, first stored - 1] and [last stored + 1, end], never interior gaps
  (an exchange holiday would otherwise be re-asked every press as a one-day request). Weekday
  test, not config/holidays.txt (Chinese exchanges trade on NY holidays).
- **end capped at book_today - 1:** today's PX_LAST is live, and a stored day is never re-asked.
- **Late OI/volume:** the last stored day of a non-FX need with volume or OI -1 is re-asked
  together with new days (never alone).
- **Own `_Asks`:** its errors stay in status["backfill"]["risk_history"], never in the
  backfill's "errors"/"reason" (a risk-history failure must not read as a failed backfill).
- Default fetch wraps pull_marks.fetch_historical_series with a Diagnostics to read Bloomberg's
  per-security `securityError` (the fetcher returns no rows rather than raising).
- Settle not a number: day left out, reported; volume/OI not a number: -1, reported.
- Snapshot does not carry price_history unless bbg-snapshot adds it to MARKET_TABLES (requested).

**How to apply:** verify with a scratch script that monkeypatches
`data.bloomberg.library.risk_history_needs` and passes `session=(None, None), fetch=fake`.
