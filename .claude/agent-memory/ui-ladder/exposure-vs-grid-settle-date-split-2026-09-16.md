---
name: exposure-vs-grid-settle-date-split-2026-09-16
description: Ladder/Delta math must use settle_date > as_of (exposure_records_from_db), grid display keeps settle_date >= as_of (records_from_db) — same exposure_section call now takes both.
metadata:
  type: project
---

2026-09-16: `engine/ladder/exposure_adapter.py` grew a second query path,
`exposure_records_from_db` (`settle_date > as_of`), alongside the original
`records_from_db` default (`settle_date >= as_of`). CLAUDE.md requires the grid to keep
`>=` (a leg settling today is still cash that moved today) but Net/Gross USD and
per-currency delta to use `>` (no delta by close on the settlement day).

`ui/tabs/exposure.py::exposure_section` used to build ONE `build_exposure(records, rates)`
result and feed it to both the grid (`combined_table`) and the delta math
(`headline_numbers`, `combined_risk_table`). It now takes an optional
`exposure_records` param; when supplied, a second `ExposureResult` is built from it and
routed only to `headline_numbers`/`combined_risk_table`, while `combined_table` still
uses the original `records`/`result`. Falls back to `records` when `exposure_records` is
omitted (backward compatible).

`ui/tabs/cash_ladder.py::_render` (the Ladder tab) now fetches both record sets —
`records_from_db` for the grid, `exposure_records_from_db` for `exposure_records` — and
passes both into `exposure_section`. `net_gross_usd` (feeds the app header's Net/Gross
USD cards) switched its one `records_from_db` call to `exposure_records_from_db`
directly since it only ever does delta math, no grid.

Gotcha found while testing: `engine.ladder.exposure.portfolio_totals` on an EMPTY
records set (all legs settle exactly on `as_of` and get excluded) returns
`{"net_usd": nan, "gross_usd": nan, "missing": []}` — `available` still comes back
`True` from `net_gross_usd` because `missing` is empty, but net/gross are NaN. Not an
ui/ bug (belongs to [[cash-ladder]]'s exposure.py), but worth knowing when writing tests
that exercise a single same-day trade: pair it with a second, later-settling trade so
the exposure result isn't degenerate.
