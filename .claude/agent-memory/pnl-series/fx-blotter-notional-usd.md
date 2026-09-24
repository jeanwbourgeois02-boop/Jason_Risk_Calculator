---
name: fx-blotter-notional-usd
description: fx_blotter quantity_usd_notional is USD for futures since 2026-09-24 (spot of as_of via usd_per_quote), None + notional_reason when no spot; crosses still show base quantity
metadata:
  type: project
---

2026-09-24 (commodity Phase 3 wave A): a FUTURE row's `quantity_usd_notional` =
contracts x multiplier x fill x S, S from `valuation.usd_per_quote(conn, quote_ccy, as_of)`
(the P&L's own lookup, near-marks included). No spot, or a text spot, -> None and a new
column `notional_reason` ("USD notional n/a: ..."); '' when fine. `reason` stays value_book's.

**Why:** futures now trade in CNY / EUR / GBP / JPY; the local figure was shown as dollars.

**How to apply:** crosses (EURSEK etc.) still show the raw base quantity in that column,
which is not USD either: flagged to the user as found-not-done, do not change without a brief.
reference.py / aggregate.py have no product lists, so CMDTY_OPTION (like EQ_OPTION) needs
nothing there. Consumers: ui-blotter-fx, ui-blotter.

Related: [[lane-inheritance]]
