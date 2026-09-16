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
