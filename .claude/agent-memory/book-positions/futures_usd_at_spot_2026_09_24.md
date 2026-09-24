---
name: futures-usd-at-spot-2026-09-24
description: How futures_usd_delta converts non-USD futures (commodity conversion Phase 1 step 1) and the shape consumers rely on
metadata:
  type: project
---

User decision 2026-09-24: a non-USD future (SHFE / DCE / ZCE / INE / GFEX in CNY, OSE in JPY,
Euronext / TTF in EUR, NBP in GBP) converts at spot of the valuation date.

- `engine/ladder/futures_delta.py::usd_per_unit(conn, ccy, as_of)` -> (S, source) or (None, reason):
  `_mark_at` exact on USD<ccy> (inverted), else <ccy>USD; 1.0 for USD. Never `_mark_near` /
  `valuation.usd_per_quote` (those estimate; the delta is never estimated, hard rule 2). A stored
  spot that is not a positive number is reported as a data error, not passed over for the other pair.
- USD delta = float(contracts) * float(multiplier) * float(price) * S. With S = 1.0 the product is
  bit-identical to the pre-2026-09-24 figure, so a USD-only book is unchanged.
- Return shape kept: `missing` is still a list of instrument ids (ui/tabs/exposure.py builds one
  Unavailable row per id). `reason` is "; "-joined: "no FUTURE_PX on <d> for <ids>" (unchanged when
  only prices are missing) then "no SPOT for CNY on <d> (<ids>)".
- `details[id]` gained `currency`, `usd_per_unit` (None when no spot) and `reason` ('' when the
  future is in by_instrument). `price` stays in the quote currency.
- positions.equity_index_positions reads `fut["by_instrument"][id]` for the future's USD, and
  `details[id]["reason"]` for a future that is not in it; `level` = the quote-ccy price.
- Reviewer N-3 (2026-09-24): a commodity future is NOT on the equity index line. Test: its
  `instruments.base_ccy` is a contract root (`data.contracts.load_roots()` key, 'SHFE:CU';
  whitespace stripped, upper-cased). Its positions belong to curve-positions. `futures_usd_delta`
  itself still takes every future (the Ladder's futures table and the stress line read it whole).
  The line still takes the first remaining future's multiplier as the ES multiplier.
- All four lane files are LF (measured with Python bytes).
