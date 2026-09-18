---
name: irs-direction-and-numeric-gate
description: 2026-09-18 findings - swap direction is absent from every export (user override is primary), blotter column identities usable for recovery (NetInvoice, Notional, Quantity), and the _num date-coercion hole
metadata:
  type: project
---

**Swap direction is not in the export, anywhere.** Checked column by column on
`data/raw/new_sample_trades.csv`: nothing separates the three swaps the desk holds as
receivers (Trade Id 918421481 625M, 920118423 995M, 932385416 158.22M) from the seven
payers - Side = Buy, unsigned Notional/Quantity, NetInvoice 0, PayLegPmtFreq =
RecvLegPmtFreq, constant DCFs on all ten. The coordinator checked the user's own export
(2026-09-18): identical. The minus signs in the retired Excel book sat in a HAND-TYPED
"Direction (local ccy)" column, so the user's knowledge was always the source.
**Why:** the user said "the rates, they're all buy right now; the P&L calc is correct but
the parser sign is not always right" and agreed to set Pay/Receive by hand.
**How to apply:** `data/ingest/irs_direction.py` (table `irs_direction_overrides`, no FK
to trades on purpose) is the PRIMARY direction source; the parser's signal detection is
only there in case an export layout adds a marker later. Never infer direction from
offsetting notionals, rates or anything else. Side = Buy is NOT evidence of pay fixed.
A swap on file as PAY with no override = "defaulted, needs the user's choice"
(`irs_direction.direction_report`); the parser never defaults to RECEIVE, which is what
makes that derivable from the database alone. Supersedes the IRS paragraph in
[[blotter-source-quirks]].

**A flipped swap keeps its history by SIGN REVERSAL, never deletion (coordinator decision
2026-09-18).** Swaps are priced for today only (`data/bloomberg/live.py` calls engine/rates
for the current as-of; the historical backfill never does), so deleted swap marks never
come back and Daily/5d/MTD/YTD stay blank for good. On an actual flip `irs_direction`
multiplies the instrument's `QL_PRICER` PV_USD / DV01_USD / CASHFLOW_USD by -1 on every
date, leaves PAR_RATE alone, deletes those three types from any other source (BBG_BDH,
MANUAL: direction unknown) and deletes the trade's realised_pnl row. Proved bit-exact
against engine/rates (payer vs receiver, forward-starting / seasoned / expired) in
`tests/test_ingest.py`; DV01 is stored SIGNED (bumped NPV minus NPV). Reverse exactly
ONCE per book rewrite: the upload loads into staging with `turn_swap_marks=False` and
`upload._stage_and_publish` reverses on live, because the staged->live merge is an upsert
and would carry reversed values across for a second reversal. NetInvoice is NOT a swap
direction signal (upfront cash amount) - dropped from `IRS_SIGN_COLUMNS`.

**Test trap: QuantLib's fixing store is process-global.** Any test that prices a seasoned
swap leaves SOFR fixings in `ql.IndexManager`, which broke
`tests/test_rates_pricing.py::test_seasoned_swap_fails_without_fixings...` (runs after
test_ingest.py alphabetically) until the identity tests got a fixture calling
`ql.IndexManager.instance().clearHistories()` before and after.

**Column identities verified on the reference sample (usable to rebuild a mangled cell):**
- FORWARD and spot CURRENCY rows (all 828): `Quantity` = base amount, `|NetInvoice|` =
  quote amount, regardless of Side. `Price` = quote/base except two EURUSD spot rows
  (896192283, 896192284: Price 1.14046 / 1.14135 vs amounts ratio 1.14 / 1.141).
- FUTURE rows (all 11): `Notional` = contracts x 50 (so the multiplier IS implied by the
  file after all: Notional / Quantity); `NetInvoice` = contracts x 50 x Price + `Total
  Fees` on a buy, - `Total Fees` on a sell. Exact on 10 rows; row 932647949 differs by
  0.0036 because the `Price` column is rounded to 2 dp (7702.11 vs 7702.1136). One row
  (903180416) has blank Total Fees and Commission 0.
- OPTION rows (all 8): `|NetInvoice|` = Quantity x Price exactly. Its SIGN is unreliable:
  -142,500 on 934168029 (Side Buy), positive on the one Sell row. Side carries direction.
- `Currency Pair` is blank or '0' on most CURRENCY rows; only FORWARD rows populate it.

**The `_num` hole (fixed 2026-09-18).** `_num` deleted every non-digit before `float()`,
so date-like text became a wrong NUMBER, not a miss: '24 Jul' -> 24.0, 'Jul-24' -> -24.0,
'7/24/2026' -> 7242026.0, '05:45:36' -> 54536.0, '1e999' -> inf ('24-Jul' and a real
Excel datetime cell, which read_table delivers as '2026-07-24 00:00:00', were already
NaN). No blotter path ever wrote TEXT to a REAL column - every value goes through
`_num`/`float()` - so the "could not convert string to float ... 24 Jul" incident did not
come from blotter.py writing text; a NaN would have failed NOT NULL and sunk the whole
upload instead. `blotter._enforce_numeric` is now the last gate and
`blotter.non_numeric_cells(conn)` names text already in a database.
**How to apply:** when that error is reported again, run `non_numeric_cells` on the
Bloomberg PC's database first; if it is clean the text is coming from a UI input (e.g.
`engine/options/store.py` does `float(strike or 0.0)` on what the user typed), not from a
stored cell.
