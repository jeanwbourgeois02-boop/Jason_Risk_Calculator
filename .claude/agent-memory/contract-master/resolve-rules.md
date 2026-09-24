---
name: resolve-rules
description: How data/contracts resolve_future matches a symbol to a root (namespace order, union rule, disambiguation) and why; estimated expiry and one-digit request tickers
metadata:
  type: project
---

`data/contracts/resolve.py::resolve_future` (built 2026-09-24, Phase 1 step 1):

- Bloomberg forms (yellow key, or a padded one-char root 'C Z6') match `bbg_root` first, then
  exchange_code. Needed: 'C Z6 Comdty' by exchange code would hit DCE:C / ICE:C, never CBOT corn.
- Chinese digit forms (CU2611, SR611) match exchange_code among CN roots (ZCE first for YMM).
- Other forms (PB export 'CLZ6-USAA', bare 'CLZ6') match exchange_code AND bbg_root together
  (union), a stricter reading of the brief's "exchange_code first, then bbg_root": 'COZ6' is
  ambiguous LME:CO / ICE:B instead of silently LME cobalt. Reported to the housekeeper.
- Narrowing: explicit prefix -> currency (CNH = CNY) -> venue (names, abbreviations, MICs;
  'CME' = CME Group's four; 'ICE' = both ICE exchanges) -> an exchange named in
  underlying/description. A populated currency or recognised venue that fits no candidate raises
  UnknownContract (hard rule 6 contradiction). Unrecognised venue text is ignored.
- Estimated last trade date = last weekday of the contract month (never earlier than any real
  one). Caveat: while the estimate is used, `request_ticker` keeps the one-digit form for a few
  days after the REAL expiry (e.g. CLZ6 really stops ~20 Nov); bbg-live should store Bloomberg's
  FUT_LAST_TRADE_DT before relying on the request form near expiry.
- `store_static_dates` canonicalises a one-digit ticker using the year of its last trade date.
- `contract_for(root_id, contract_id, conn=None)` (added on the housekeeper's follow-up, for
  bbg-backfill / bbg-library, which store the canonical id as `instruments.instrument_id` and the
  root id as `instruments.base_ccy`): reads back only the two-digit canonical form; a one-digit
  id, a missing yellow key or another root's Bloomberg root raises UnknownContract (so does an
  unknown root_id, converted from get_root's KeyError).
