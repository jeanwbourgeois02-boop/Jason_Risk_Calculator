---
name: scorecard-and-carry
description: 2026-09-29 scorecard.py (trader ideas, closed-at-flat rule, P&L at as-of, stats) and carry.py (calendar roll-down on the research curve); choices taken and sample quirks
metadata:
  type: project
---

Built 2026-09-29 (user: "i feel like 123 and the hedges stuff would be good to implement").

**Scorecard** (`engine/spreads/scorecard.py::scorecard(conn, as_of, series, spreads)`): ideas = `positions_of` row ids.
- Flat on a day = per (instrument_id, settle_date) the live rows (status not SETTLED/CLOSED) of members dealt by
  then net to zero. Closed = flat on as-of; close date = first day of the flat run reaching as-of.
- P&L = LTD on the AS-OF date for closed and open alike (user yes 2026-09-29, reviewer's 2nd pass, reversing
  my first "LTD at close" choice): closed + open = book LTD by construction, and a flat unsettled non-USD
  future keeps re-converting at spot. `pnl_at_close` / `pnl_at_close_date` / `fx_since_close` are context.
  Unpriced on as-of -> `included` False with the reason, out of every statistic.
- The series appends a weekend / holiday as-of as its last day: holding days count business days only.
  Reviewer's case: 8 TTF lots sold back on 09-10 -> closed, pnl 7,076.01, at close 7,142.66, fx -66.65.
- Win / loss beyond half a cent; scratches count in the win-rate denominator. by_trade_name uses
  `strategy` (PBRoot name), by_spread_type `trade_type` ([[feedback-strategy-means-spread-type]] in user memory).
- Sample 2026-09-18: 8 closed (3 W, 4 L, 1 scratch), 30 open; closed + open = book LTD -49,293.26 (a check).
  A closed-out FX option pair (910000044/45) is two ideas, because the Book gives each FX option its own row.

**Carry** (`engine/spreads/carry.py::carry(conn, as_of, spreads, history)`): rows for every Book position and
strategy pair; only `level_spec.kind == 'calendar'` gets a figure. Mi-1 = previous month of contract-master's
`active_months` (horizon_months says the step). The four contracts read on their latest COMMON research date
on or before as-of (a note when older than one's own latest): the brief said "each contract's latest", common
date keeps both spreads on one curve. Price = research raw settle x OUR price_scale. roll_down_usd = roll x
usd_per_unit (already signed). Front check: research `last_trade_date` of Mi-1 < as-of.
- The mock research DB puts last_trade_date at contract-month end, so CLU26 is still "listed" on 2026-09-18.
- Sample: WTI Z26/F27 research -0.95, rolled (X26/Z26) -1.43, roll -0.48 USD/bbl, -7,200 USD on the 15-lot
  position (-4,800 on the '' strategy's 10-lot pair).

**How to verify:** scratch copy of `data/raw/sample.db`; `sum(open unrealised) + sum(closed total)` = book LTD
when nothing is excluded; carry front case: CLU26/CLV26 spec at 2026-09-18 (CLQ26 expired 08-31).
