---
name: phase5-options-lme
description: Phase 5 (2026-09-24) booking side of CMDTY_OPTION / LME_FWD - breakdown labels, underlying-only futures, why contract_dates re-keys every mark type and keeps its result shape
metadata:
  type: project
---

Phase 5 (options on commodity futures `CMDTY_OPTION`, LME forwards `LME_FWD`) reached the upload
on 2026-09-24 through ingest-parser.

- `upload.loaded_breakdown` uses plain labels, never product codes: "29 futures, 4 options on
  futures, 3 LME forwards, 8 FX forwards, 1 FX spot, 5 FX options". Listed options (EQ_OPTION)
  and FX swaps are named only when non-zero. The sample now loads 50 trades / 62 legs.
- An option whose underlying the file does not trade makes the parser write that future as an
  instrument with no trade (`ParseResult.underlying_only`, INSERT OR IGNORE). The summary names
  them. An upload never deletes instruments, so they survive re-uploads untouched.
- `apply_contract_dates` covers asset_class FUTURE and CMDTY_OPTION (never LME_FWD, which is
  perpetual). An option's stored date is keyed by its canonical option id, which is its instrument id.
- It re-keys EVERY mark type at the old expiry, not only FUTURE_PX. **Why:** the options
  pricer keys PREMIUM / Greeks on the expiry too, and the delta SQL joins without settle_date.
  A Greek left under the old key would double-count beside one priced at the new date.
- The result dict shape stayed unchanged. Adding an `asset_class` key broke bbg-snapshot's
  pinned dict in tests/test_snapshot.py. `summary_sentence` tells options from futures with
  `data.contracts.tickers.parse_option_ticker(instrument_id)`.

**How to apply:** a new key in `apply_contract_dates`' result is an interface change for bbg-live
and bbg-snapshot (their tests pin it exactly), so avoid one unless it is truly needed.
Related: [[contract-dates]], [[phase2-removal]].
