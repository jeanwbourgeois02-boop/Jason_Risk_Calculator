---
name: bloomberg-field-assumptions
description: Unverified Bloomberg field and ticker guesses the ticker check relies on (LME, options, bulk fields, the 2026-09-29 desk checks)
metadata:
  type: project
---

Nothing below has been seen on a terminal yet (no Bloomberg on the dev PC; the user runs `py 2_launcher.py bbg-check` at the Bloomberg PC). When the user pastes a real report, update this note with what Bloomberg actually returned.

- **Bulk fields (OPT_CHAIN):** `pull_marks.fetch_reference` reads one value per field (`getElement(f).getValue()`), which drops a bulk field's rows, so the check sends its own ReferenceDataRequest (`BlpapiClient.bulk`) and reads the array of sequences row by row. Rows are assumed to carry 'Security Description'.
- **OPT_CHAIN on a generic** ('CL1 Comdty'): assumed to list the options on the current front future. If it comes back empty on the terminal, try the dated future ('CLX6 Comdty') before blaming the root.
- **Option ticker form:** we write 'CLX6C 70 Comdty' (no trailing zeros, one-digit year). Bloomberg's chain may write '70.00'. The check asks our form as well and calls it FORM_MISMATCH only if Bloomberg refuses our form or resolves it to another option.
- **OPT_EXER_TYP** is read as 'American' / 'European' by prefix. OPT_UNDL_TICKER may come without a yellow key.
- **LME:** 'LMCADY' (cash), 'LMCADS03' (3M) and the dated 'LPV6' (monthly) come from engine/lme. The prompt-date field is bbg-curves' `LME_PROMPT_DATE_FIELD` ('FUT_DLV_DT_LAST'). A 3M date one LME business day behind today counts as "not rolled yet", not a mismatch.

- **Desk checks (added 2026-09-29), all unverified:**
  - Delivery type: no known field; the check asks candidates FUT_DELIVERY_TYPE, FUT_SETTLE_TYP, CASH_SETTLED, FUT_DLV_TYP and uses the first that answers (a Y/N on a CASH field = cash/physical). If none answers, the report says SKIPPED and asks the user to FLDS <GO>. Record the real field here once seen.
  - Holidays: CALENDAR_NON_SETTLEMENT_DATES (bulk) asked on one front generic per calendar with overrides CALENDAR_START_DATE / CALENDAR_END_DATE only, assuming a future's own settlement calendar is its exchange's. If it answers a country calendar instead, fill `CALENDAR_CODES` (SETTLEMENT_CALENDAR_CODE override). Bulk rows parsed for any date-like value (sub-field name assumed 'Holiday Date').
  - PX_SETTLE history on Chinese contracts assumed to be the exchange's volume-weighted settlement; PX_LAST their last trade.
  - SGX USD/CNH: contracts.csv has UC + Curncy (a guess); the check also tries UC Comdty, XUC Curncy, XUC Comdty.
  - LME OPEN_INT / PX_VOLUME: asked on LM<code>DS03 and on the research app's two-digit-year monthly tickers ('LPZ26 Comdty').

- **Pull tickers part (added 2026-09-30, `--pull`), unverified:**
  - A currency pair's CRNCY ('USDCNH Curncy') is assumed to be its quote currency (CNH); a mismatch is a CHECK. If every pair comes back CHECK on the first real run, this assumption is wrong, not the tickers.
  - LAST_UPDATE_DT older than 7 days on a live need = stale (CHECK). An FX pair may return a time, not a date: then it is ignored.
  - Expired chain contracts (the two-digit year form 'C K24 Comdty') are judged only on a security error; one HistoricalDataRequest probes those refused. On the sample book the pull lists ~600 tickers (424 of them risk-history chain contracts), so `check_book(include_pull=True)` is off by default.

- **App fixes search (added 2026-09-30, `check_book(include_search=True)`), unverified:**
  - A Curncy root is searched with yellowKeyFilter `YK_FILTER_CURR` (commodity roots `YK_FILTER_CMDT`); the enum name is from the API docs, not seen on a terminal.
  - Besides the search hits, the unknown root's alternate codes (bbg_root, exchange_code, `ALTERNATE_BBG_ROOTS`, a root named after "bbg_root" in its notes) are asked as generics; a Curncy root under Curncy and Comdty. SGX:XUC: XUC1 Curncy / XUC1 Comdty / UC1 Curncy / UC1 Comdty. Record which one answers.
  - A candidate is judged on NAME / EXCH_CODE / CRNCY / a price of its generic only; a generic's NAME is "Generic 1st 'X' Future", so the name rarely helps.

**Why:** the user runs the check only when they finally have Bloomberg access (2026-09-24). Every guess here is something the first real run confirms or corrects.
**How to apply:** when a real report arrives, check these guesses first. Record what Bloomberg said, word for word. Related: [[worksheet-fixable-fields]].
