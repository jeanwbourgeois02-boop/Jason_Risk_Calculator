---
name: blotter-source-quirks
description: Field-mapping quirks discovered building data/ingest/blotter.py against data/raw/new_sample_trades.csv (the new transaction-level trade source superseding bnp.py)
metadata:
  type: project
---

Trade source is moving from the BNP position/P&L snapshot (`data/ingest/bnp.py`) to a
transaction-level blotter export, parsed by `data/ingest/blotter.py` (added 2026-09-16,
`tests/test_blotter.py`). `bnp.py` was left untouched per instruction.

Non-obvious things found in `data/raw/new_sample_trades.csv` (857 rows, 111 cols):

- **The trailing numeric id in `Symbol` is `Instrument Id`, not `Trade Id`** (e.g. Symbol
  `USDJPY091626-197584766` vs `Trade Id 934530555` on the same row). This differs from
  the BNP file, where the `Symbol` id IS the trade id. Do not cross-check Symbol's id
  against Trade Id in this format — confirmed via the separate `Instrument Id` column.
- **`Fin Type` values differ from BNP's `Financial Type`**: `FUTURE` (singular, not
  `FUTURES`), plus a new `OPTION` type; `FORWARD`, `CURRENCY`, `INTEREST_RATE_SWAP` match.
  `Product` column is NOT authoritative (mostly junk like 'FUTURE' on non-future rows).
- **`TradeDate` / `Settle Date` columns are `d/m/yyyy` (non-zero-padded, day-first)**,
  e.g. `20/8/2026` — different format from the FORWARD `Description`'s `TD mm/dd/yyyy`
  (US-style, zero-padded). Use the Description regex for FORWARD trade_date/value_date
  (unambiguous); only FUTURE/OPTION rows (no description TD/VD) fall back to parsing the
  `TradeDate` column with `%d/%m/%Y`.
- **Numeric cells carry thousands separators and load as strings** (`'1,137,580.00'`),
  even though pandas is not told `dtype=str` for these columns explicitly if you read
  without `dtype=str` — but reading with `dtype=str` (used here so name-based lookups are
  robust to column reordering) makes ALL numeric cells strings; commas must be stripped
  before `float()`.
- **FORWARD rows give clean structured buy/sell legs directly** (`Buy Currency`, `Sell
  Currency`, `BuyCurrency Amount`, `SellCurrency Amount`) — no need to reverse-engineer
  `Local Cost` rounding the way `bnp.py` does. `Symbol`/`Description` regexes are
  byte-identical to `bnp.py`'s `FORWARD_SYMBOL_RE`/`DESCRIPTION_RE` on every row of the
  reference sample (imported from `bnp.py`, not redefined).
- **FUTURE rows carry a real per-fill `Price` and `Trade Id`** (unlike the BNP snapshot,
  which nets to one row per contract with no fill data — see `bnp.py`'s module
  docstring). This makes the blotter the actual futures-fill source; CLAUDE.md's
  "xlsx → tables" section describing futures fills as coming from the workbook is now
  stale (flagged for the housekeeper, not changed).
- **`INTEREST_RATE_SWAP` direction**: `Side` is `'Buy'` on all 10 reference rows and
  carries no direction. User-confirmed 2026-09-16: the sign of the `Notional` column IS
  the direction signal (positive = pay fixed, negative = receive fixed) — `blotter.py`
  now parses IRS rows using `quantity = Notional` signed, in **full notional units**
  (this file's `Notional` column, e.g. `625,000,000`, is already full units — unlike its
  own millions-scaled `Quantity` column, which is NOT used for IRS). This matches
  `irs.py`'s BNP path (`quantity_mm * 1e6`) and CLAUDE.md's "notional (IRS: + = pay
  fixed)" wording, so IRS quantity/leg amounts are on the same scale regardless of
  source. Only zero-Notional rows are rejected (can't infer direction); all 10 reference
  rows have positive Notional and parse cleanly.
- **`CURRENCY` rows are settlement-level cash movements, not an EOD balance snapshot**
  (this file is transaction-level). `blotter.py` writes the `CASH-<CCY>` instrument only,
  no trade/legs/positions row — there is no defined `positions` grain for a
  transaction-level cash movement. This is a data-contract gap worth flagging, not solved
  here.
- **`trades.strategy` has no equivalent column** in this file (BNP's `NM Strategy`);
  `blotter.py` leaves it `''`.
- No blank-`Trade Id` rows, no `" "`-as-Price rows, and no `lyang@NMCL`-linked malformed
  rows were actually present in the 2026-09-16 reference sample despite being called out
  as known quirks in the task brief — `blotter.py` still defends against blank Trade Id
  and unparseable Quantity/Price defensively, but those paths are untested against real
  data (only via synthetic rows in `tests/test_blotter.py`).
- `trades.source` enum is `BNP | XLSX | MANUAL` per CLAUDE.md; the blotter is closest to
  `'XLSX'` (no dedicated `'BLOTTER'` value exists) — used `SOURCE = "XLSX"` in
  `blotter.py`, flagged as a naming compromise, not a hidden decision.

**Guess-audit findings, 2026-09-17 (user asked "are there things where you are guessing?"
— full table given in that session's report, not persisted as a doc file since docs/ isn't
data-ingest's to write; summary here for future sessions).** Things confirmed genuinely
absent from the file (i.e. NOT a parser gap, don't go looking for a fix again):
  - **No column anywhere carries option strike** for rows whose Description lacks a
    "`<n> STRIKE`" token (`EURSEK112526C-197906813`, `USDJPY111926P-197571137`,
    `USDJPY111926P-197957397` — the 3 no-strike options seen in the dev DB). Checked every
    populated cell on those rows by hand. The 0.0 sentinel is correct, not a bug.
  - **No column carries a futures contract multiplier** (`QtyFactor`/`Factor` are blank on
    all 11 reference FUTURE rows) — `FUTURE_MULTIPLIERS` (imported from `bnp.py`) stays a
    hardcoded market-convention table. Only `ES` (multiplier 50) is actually exercised by
    the reference sample; `NQ`/`RTY`/`YM` are unverified-by-data guesses inherited unchanged.
  - **No column indicates NDF-ness** — `NDF_CCYS` (BRL/TWD/KRW/IDR, from `bnp.py`, marked
    PROVISIONAL there) stays the only source; same guess as the BNP file, not specific to
    the blotter.
  - **`FxOption Type` is a literal `"0"` on every OPTION row**, not "Call"/"Put" text
    despite the column name — useless as a call/put source; harmless since the word-match
    parser strips digits anyway, but don't try to read it as authoritative later.
  - **The OPTION rows' `Settle Date` is the premium payment date (T+2ish), not the option's
    expiry** (e.g. Symbol `EURSEK092326C-...` expiry 2026-09-23 vs that row's own
    `Settle Date` = 25/8/2026). Confirmed by cross-referencing against the Symbol-encoded
    expiry. `blotter.py` correctly never reads this column for options; don't "fix" it into
    using Settle Date as expiry later, that would be wrong.
  - **`Side`/`Fin Type`/`Status`/`Fund` are 100% exact-canonical-string in the reference
    sample** ("Buy"/"Sell", the 5 literal Fin Type values, "Completed", "NMMF") — every
    tolerance alias beyond the exact match (`_side`'s "b"/"bought"/"bot"/"+"/... ,
    `EXCLUDED_STATUS_WORDS`'s "error"/"draft" which aren't even in CLAUDE.md's own list,
    `_kind_of`'s extra keywords like OUTRIGHT/NDF/CASH/bare-"SWAP") is unexercised by real
    data — reasonable tolerance per the "as flexible as possible" instruction, but genuinely
    unverified guesses, not bugs to chase.
  - No duplicate `Trade Id` in the reference sample (857/857 unique) — the `Version`
    keep-highest de-dupe rule (documented in CLAUDE.md) was completely untested until this
    session added synthetic-data tests for it directly (`test_repeated_trade_id_keeps_*` in
    `tests/test_blotter.py`); still never verified against real duplicate rows.

Two real (small) bugs fixed this session, both evidenced from the reference file itself:
  - `_parse_currency`'s old fallback order (`Symbol or Currency or Buy Currency`) used the
    `Currency` column as a second-choice source for a CURRENCY row's own ccy — but `Currency`
    disagrees with `Symbol` on **all 85** reference CURRENCY rows and is provably the OTHER
    leg's currency of the underlying FX deal (e.g. `Symbol=EUR.C-EUAA` /
    `Currency=SEK.C-SSAA` on a EURSEK cash line), never this row's own. Never actually wrong
    in practice (Symbol is always populated), but a real latent bug if Symbol were ever
    blank. Fixed to be Side-aware (`Buy Currency` on Side=Buy, `Sell Currency` on Side=Sell —
    both verified to equal Symbol whenever Symbol is present).
  - The option-expiry-from-Description fallback path used the file's day-first/month-first
    auto-detected convention (`_date`) instead of the fixed US mm/dd/yyyy shape that
    `US_DATE_RE`-matched Description dates are always in (like the FORWARD `TD`/`VD` dates,
    which already correctly use `_us_date`, not `_date`). Silently correct on the reference
    sample only because every embedded day happens to be > 12 (forcing the day-first parse
    to fail and fall back) — would have silently misread an ambiguous date (e.g. "08/09")
    on a different file. Fixed to always use `_us_date`, and added a cross-check: when
    Symbol parses (100% of the sample) AND Description carries its own date/CALL-PUT word,
    a disagreement between the two now rejects instead of blindly trusting the Symbol.
