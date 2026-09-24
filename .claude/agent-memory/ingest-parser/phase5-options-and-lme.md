---
name: phase5-options-and-lme
description: 2026-09-24 Phase 5 ingest of options on futures (CMDTY_OPTION) and LME forwards (LME_FWD) in blotter.py -- routing rules, guesses, the expiry and underlying-instrument choices, sample rows 046-052
metadata:
  type: project
---

Phase 5 (2026-09-24) added two products to `data/ingest/blotter.py`; shapes were fixed by the
housekeeper (curve-positions built on them the same day). No real commodity option or LME row
has been seen, so every recognition rule is a guess (listed as a Request for
docs/blotter-parser-assumptions.md).

**Why:** Jason's book is commodity RV; hard rule 6 means guesses must fail soft (warn / skip),
and only contradictions or unknown roots reject.

**How to apply:**
- OPTION routing (`_is_commodity_option`): FX Symbol form first -> FX; then a listed
  'ROOT/[EA]yymmdd..', Bloomberg 'CLZ6C 75', Chinese 'CU2612C80000', exchange prefix or
  ' Comdty' -> commodity; then a Currency Pair / pair-like Underlying -> FX; any other symbol ->
  commodity (resolver's refusal is the reject). Index options (SPX, SPXW, NDX, RUT, SX5E, ES,
  NQ, RTY, YM, or ' Index' key) are skipped as retired before routing.
- Option type from FxOption Type cell or whole words CALL/PUT only (never single C/P letters:
  the 'CPTY-C' hazard of the FX path, see [[synthetic-sample-book]]).
- Expiry choice (mine, flagged to the housekeeper): Bloomberg's stored date, else the dated
  symbol's own expiry when earlier than contract-master's estimate, else the estimate.
  contract-master's `resolve_option` keeps `last_trade_date` = underlying's estimate even when
  the symbol states the expiry; I asked it to honour `symbol_expiry`.
- The underlying future's instrument is written with no trade (housekeeper request);
  `ParseResult.underlying_only` makes `load` use INSERT OR IGNORE for it, never overwriting.
- LME: FUTURE/FORWARD rows not in FX-forward shape go through `_lme_match` (LM..DY/DS03
  tickers, resolve_future landing on `engine.lme.lme_roots()`, or venue/description LME + a
  metal code/name). Ferrous LME roots stay FUTURE. Quantity = lots (guess), tonnes =
  lots x lot_tonnes. Prompt order: Prompt Date/Prompt/Maturity columns, a date in the
  Description, Settle Date if after trade date, 3M -> three_month_date (info note), month
  symbol -> third Wednesday (info note), Settle Date anyway, else reject. Invalid prompt = warning.
- Sample: rows 910000046-052 appended (4 options: CL call/put 17 Nov, GC Aug call expired
  28 Jul, SHFE CU call CNY; 3 LME: CA 3M no date, AH Dec 3rd Wed sold on a FUTURE row, NI
  prompt 16 Sep past). 52 rows -> 50 trades, 62 legs, 42 instruments (GCQ26, CUZ26
  underlying-only), 9 option terms, 2 rejects, 0 warnings. Generator: append-only script
  (LF endings, NetInvoice = lots x size x price +/- lots x commission).
- Bash heredocs containing many quotes can fail silently in this shell ("unexpected EOF"):
  append big test blocks with the Edit tool. Related: [[commodity-futures-ingest]],
  [[phase2-macro-removal]].
